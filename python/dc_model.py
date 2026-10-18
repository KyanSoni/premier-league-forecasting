"""
Dixon-Coles model for the prem_sims database.

For every model run in v_runs (e.g. '2026-27 after GW6') it:
  1. fits Dixon-Coles attack/defence ratings, home advantage and a low-score correction (rho),
     using ONLY matches played before that run (with time-decay weighting),
  2. predicts home/draw/away probabilities for every fixture of that season
     -> table external_predictions (source = 'dixon_coles'), scored by v_scores,
  3. simulates the rest of the season 10,000 times
     -> table dc_sim_summary (title / top-4 / relegation chances),
  4. stores the fitted ratings -> table dc_team_ratings.

Run it from a terminal with:   python dc_model.py
Edit the CONFIG section first (your password).
"""
import math
import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

# ============================== CONFIG ==============================
DB_URL = "postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/prem_sims"
XI = 0.0019          # time decay per day (0.0019 ~ a match a year old counts about half)
N_SIMS = 10000       # simulated seasons per run
MAX_GOALS = 10       # score grid 0..10
RIDGE = 10           # shrinkage: pulls teams with little data towards average (0 = none; 3-10 works well)
ONLY_RUN = None      # e.g. "2026-27 after GW6" to fit just one run; None = every run
HISTORY_SEASONS = 2  # past seasons used as history (Upgrade 7); 1 = old behaviour
SEED = 42
# ====================================================================


# ---------------------------------------------------------------- model
def _tau(x, y, lam, mu, rho):
    """Dixon-Coles low-score adjustment (vectorised)."""
    t = np.ones_like(lam)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    t[m00] = 1 - lam[m00] * mu[m00] * rho
    t[m01] = 1 + lam[m01] * rho
    t[m10] = 1 + mu[m10] * rho
    t[m11] = 1 - rho
    return np.clip(t, 1e-10, None)


def fit_dixon_coles(train, teams, ref_date, xi=XI):
    """train: DataFrame with home, away, hg, ag, match_date. Returns a dict of parameters."""
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    h = train["home"].map(idx).to_numpy()
    a = train["away"].map(idx).to_numpy()
    x = train["hg"].to_numpy(dtype=float)
    y = train["ag"].to_numpy(dtype=float)
    days = (pd.Timestamp(ref_date) - pd.to_datetime(train["match_date"])).dt.days.clip(lower=0).to_numpy()
    w = np.exp(-xi * days)

    def unpack(p):
        mu0, home, rho = p[0], p[1], p[2]
        att = p[3:3 + n] - p[3:3 + n].mean()
        dfn = p[3 + n:3 + 2 * n] - p[3 + n:3 + 2 * n].mean()
        return mu0, home, rho, att, dfn

    def nll(p):
        mu0, home, rho, att, dfn = unpack(p)
        lam = np.exp(mu0 + home + att[h] + dfn[a])
        mu = np.exp(mu0 + att[a] + dfn[h])
        ll = (np.log(_tau(x, y, lam, mu, rho))
              + x * np.log(lam) - lam - gammaln(x + 1)
              + y * np.log(mu) - mu - gammaln(y + 1))
        return -(w * ll).sum() + RIDGE * (att @ att + dfn @ dfn)   # shrinkage towards average

    p0 = np.concatenate([[0.3, 0.2, -0.05], np.zeros(2 * n)])
    bounds = [(None, None), (None, None), (-0.2, 0.2)] + [(None, None)] * (2 * n)
    res = minimize(nll, p0, method="L-BFGS-B", bounds=bounds, options={"maxiter": 2000})
    mu0, home, rho, att, dfn = unpack(res.x)
    return {"mu0": mu0, "home": home, "rho": rho,
            "att": dict(zip(teams, att)), "def": dict(zip(teams, dfn)), "converged": res.success}


def fill_missing_teams(params, season_teams):
    """Teams with no data (e.g. promoted clubs) start at the average of the 3 weakest fitted teams."""
    fitted = list(params["att"].keys())
    strength = sorted(fitted, key=lambda t: params["att"][t] - params["def"][t])
    weakest = strength[:3]
    att_w = np.mean([params["att"][t] for t in weakest])
    def_w = np.mean([params["def"][t] for t in weakest])
    missing = [t for t in season_teams if t not in params["att"]]
    for t in missing:
        params["att"][t] = att_w
        params["def"][t] = def_w
    return missing


def score_grid(params, home, away):
    lam = math.exp(params["mu0"] + params["home"] + params["att"][home] + params["def"][away])
    mu = math.exp(params["mu0"] + params["att"][away] + params["def"][home])
    g = np.arange(MAX_GOALS + 1)
    ph = np.exp(g * math.log(lam) - lam - gammaln(g + 1))
    pa = np.exp(g * math.log(mu) - mu - gammaln(g + 1))
    grid = np.outer(ph, pa)
    rho = params["rho"]
    grid[0, 0] *= 1 - lam * mu * rho
    grid[0, 1] *= 1 + lam * rho
    grid[1, 0] *= 1 + mu * rho
    grid[1, 1] *= 1 - rho
    return grid / grid.sum(), lam, mu


def predict(params, fixtures):
    rows = []
    for f in fixtures.itertuples():
        grid, lam, mu = score_grid(params, f.home, f.away)
        rows.append((f.match_id, float(np.tril(grid, -1).sum()), float(np.trace(grid)),
                     float(np.triu(grid, 1).sum()), lam, mu))
    return pd.DataFrame(rows, columns=["match_id", "p_home", "p_draw", "p_away", "xg_home", "xg_away"])


def simulate(params, season_teams, played, remaining, n_sims=N_SIMS, seed=SEED):
    rng = np.random.default_rng(seed)
    idx = {t: i for i, t in enumerate(season_teams)}
    n = len(season_teams)
    pts = np.zeros(n); gf = np.zeros(n); ga = np.zeros(n)
    for m in played.itertuples():
        hi, ai = idx[m.home], idx[m.away]
        gf[hi] += m.hg; ga[hi] += m.ag; gf[ai] += m.ag; ga[ai] += m.hg
        pts[hi] += 3 if m.hg > m.ag else (1 if m.hg == m.ag else 0)
        pts[ai] += 3 if m.ag > m.hg else (1 if m.hg == m.ag else 0)
    P = np.tile(pts, (n_sims, 1)); GF = np.tile(gf, (n_sims, 1)); GA = np.tile(ga, (n_sims, 1))
    k = MAX_GOALS + 1
    for f in remaining.itertuples():
        grid, _, _ = score_grid(params, f.home, f.away)
        cum = np.cumsum(grid.ravel())
        draws = np.searchsorted(cum, rng.random(n_sims) * cum[-1])
        hg, ag = draws // k, draws % k
        hi, ai = idx[f.home], idx[f.away]
        GF[:, hi] += hg; GA[:, hi] += ag; GF[:, ai] += ag; GA[:, ai] += hg
        P[:, hi] += np.where(hg > ag, 3, np.where(hg == ag, 1, 0))
        P[:, ai] += np.where(ag > hg, 3, np.where(hg == ag, 1, 0))
    key = P * 1_000_000 + (GF - GA + 500) * 1_000 + GF          # points, then goal difference, then goals
    order = np.argsort(-key, axis=1)
    pos = np.empty_like(order)
    pos[np.arange(n_sims)[:, None], order] = np.arange(1, n + 1)
    return pd.DataFrame({
        "team_name": season_teams,
        "avg_finish": pos.mean(axis=0).round(1),
        "title_pct": (100 * (pos == 1).mean(axis=0)).round(1),
        "top4_pct": (100 * (pos <= 4).mean(axis=0)).round(1),
        "relegation_pct": (100 * (pos >= n - 2).mean(axis=0)).round(1),
    })


# ---------------------------------------------------------------- one run
def run_one(run, matches):
    season, gw = run["season"], int(run["gameweek"])
    s = matches[matches["season"] == season]
    season_teams = sorted(set(s["home"]) | set(s["away"]))
    cur = s[(s["round_no"] <= gw) & s["hg"].notna()]
    y0, y1 = int(season[:4]), int(season[5:7])
    prev_labels = [f"{y0 - k}-{(y1 - k) % 100:02d}" for k in range(1, HISTORY_SEASONS + 1)]
    prev = matches[matches["season"].isin(prev_labels) & matches["hg"].notna()]
    train = pd.concat([prev, cur])
    if len(train) < 50:
        return None, f"skipped: only {len(train)} matches available before this run"
    ref_date = (cur["match_date"].max() if len(cur) else pd.to_datetime(s["match_date"]).min() - pd.Timedelta(days=1))
    teams = sorted(set(train["home"]) | set(train["away"]))
    params = fit_dixon_coles(train, teams, ref_date)
    missing = fill_missing_teams(params, season_teams)
    preds = predict(params, s[["match_id", "home", "away"]])
    played = s[(s["round_no"] <= gw) & s["hg"].notna()]
    remaining = s[s["round_no"] > gw]
    sims = simulate(params, season_teams, played, remaining)
    ratings = pd.DataFrame({
        "team_name": season_teams,
        "attack": [round(math.exp(params["att"][t]), 3) for t in season_teams],
        "defence": [round(math.exp(params["def"][t]), 3) for t in season_teams],
    })
    ratings["home_adv"] = round(math.exp(params["home"]), 3)
    ratings["rho"] = round(params["rho"], 3)
    ratings["avg_goals"] = round(math.exp(params["mu0"]), 3)
    note = f"fitted on {len(train)} matches; home advantage x{math.exp(params['home']):.3f}; rho {params['rho']:.3f}"
    if missing:
        note += f"; no data for {', '.join(missing)} (set to bottom-3 average)"
    if not params["converged"]:
        note += "; WARNING: optimiser did not fully converge"
    return {"preds": preds, "sims": sims, "ratings": ratings}, note


# ---------------------------------------------------------------- database
def main():
    from sqlalchemy import create_engine, text
    engine = create_engine(os.environ.get("PREM_DB_URL", DB_URL))   # Upgrade 16: password can live in an environment variable
    matches = pd.read_sql("""
        SELECT m.match_id, s.label AS season, m.round_no, m.match_date,
               th.team_name AS home, ta.team_name AS away,
               m.home_goals AS hg, m.away_goals AS ag
        FROM matches m
        JOIN seasons s ON s.season_id = m.season_id
        JOIN teams th  ON th.team_id = m.home_team_id
        JOIN teams ta  ON ta.team_id = m.away_team_id""", engine)
    runs = pd.read_sql("SELECT run_label, season, gameweek FROM v_runs ORDER BY season, gameweek", engine)
    if ONLY_RUN:
        runs = runs[runs["run_label"] == ONLY_RUN]
    if runs.empty:
        sys.exit("No runs found. Check ONLY_RUN matches a run_label exactly.")

    for _, run in runs.iterrows():
        label = run["run_label"]
        out, note = run_one(run, matches)
        print(f"{label}: {note}")
        if out is None:
            continue
        with engine.begin() as con:
            con.execute(text("DELETE FROM external_predictions WHERE source = 'dixon_coles' AND run_label = :r"), {"r": label})
            con.execute(text("DELETE FROM dc_sim_summary WHERE run_label = :r"), {"r": label})
            con.execute(text("DELETE FROM dc_team_ratings WHERE run_label = :r"), {"r": label})
        p = out["preds"][["match_id", "p_home", "p_draw", "p_away"]].copy()
        p.insert(0, "run_label", label); p.insert(0, "source", "dixon_coles")
        p[["p_home", "p_draw", "p_away"]] = p[["p_home", "p_draw", "p_away"]].round(4)
        p.to_sql("external_predictions", engine, if_exists="append", index=False)
        sims = out["sims"]; sims.insert(0, "run_label", label)
        sims.to_sql("dc_sim_summary", engine, if_exists="append", index=False)
        rt = out["ratings"]; rt.insert(0, "run_label", label)
        rt.to_sql("dc_team_ratings", engine, if_exists="append", index=False)
    print("Done. In Excel: Data > Refresh All.")


if __name__ == "__main__":
    main()
