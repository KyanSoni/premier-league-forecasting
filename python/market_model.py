"""
Upgrade 10: market-implied team ratings.

For every run it fits attack/defence ratings and home advantage so that a Poisson model reproduces,
as closely as possible, the bookmakers' (margin-free) win/draw/loss chances for matches played
BEFORE the run (this season up to the run's gameweek, plus earlier seasons with time decay).
The odds were set before kick-off, so no result leaks in. It then predicts every fixture of the
season and stores them as source = 'market' in external_predictions (scored by v_scores).

Run:  python market_model.py        (same folder as dc_model.py; uses its DB_URL)

Tuning (Upgrade 10b): give a decay and a ridge to store a test version under its own name,
so the main 'market' rows are not touched:
      python market_model.py 0.01 2      -> source 'market_xi010_r2'
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

import dc_model as dc

# ============================== CONFIG ==============================
DB_URL = dc.DB_URL
ONLY_RUN = None          # e.g. "2026-27 after GW6"; None = every run
HISTORY_SEASONS = 2      # earlier seasons' odds used (with decay)
XI = 0.01               # decay per day (~ half weight after 70 days)
RIDGE = 2.0              # small shrinkage towards average
MIN_MATCHES = 30
SOURCE = "market"
# ====================================================================
if len(sys.argv) == 3:                       # tuning run: python market_model.py <XI> <RIDGE>
    XI, RIDGE = float(sys.argv[1]), float(sys.argv[2])
    SOURCE = f"market_xi{round(XI * 1000):03d}_r{RIDGE:g}"

G = np.arange(11)
LG = gammaln(G + 1)
H_MASK = np.tril(np.ones((11, 11)), -1)
D_MASK = np.eye(11)
A_MASK = np.triu(np.ones((11, 11)), 1)


def outcome_probs(lh, la):
    ph = np.exp(np.outer(np.log(lh), G) - lh[:, None] - LG)
    pa = np.exp(np.outer(np.log(la), G) - la[:, None] - LG)
    grid = ph[:, :, None] * pa[:, None, :]
    tot = grid.sum((1, 2))
    return np.stack([(grid * H_MASK).sum((1, 2)), (grid * D_MASK).sum((1, 2)),
                     (grid * A_MASK).sum((1, 2))], 1) / tot[:, None]


def fit_market(train, teams, ref_date):
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    h = train["home"].map(idx).to_numpy()
    a = train["away"].map(idx).to_numpy()
    pm = train[["b_home", "b_draw", "b_away"]].to_numpy(dtype=float)
    days = (pd.Timestamp(ref_date) - pd.to_datetime(train["match_date"])).dt.days.clip(lower=0).to_numpy()
    w = np.exp(-XI * days)

    def unpack(p):
        return p[0], p[1], p[2:2 + n] - p[2:2 + n].mean(), p[2 + n:] - p[2 + n:].mean()

    def loss(p):
        mu, home, att, dfn = unpack(p)
        q = outcome_probs(np.exp(mu + home + att[h] + dfn[a]), np.exp(mu + att[a] + dfn[h]))
        return -(w[:, None] * pm * np.log(q + 1e-12)).sum() + RIDGE * (att @ att + dfn @ dfn)

    r = minimize(loss, np.concatenate([[0.3, 0.2], np.zeros(2 * n)]), method="L-BFGS-B")
    mu, home, att, dfn = unpack(r.x)
    return {"mu0": mu, "home": home, "att": dict(zip(teams, att)), "def": dict(zip(teams, dfn))}


def run_one(run, matches):
    season, gw = run["season"], int(run["gameweek"])
    s = matches[matches["season"] == season]
    season_teams = sorted(set(s["home"]) | set(s["away"]))
    y0, y1 = int(season[:4]), int(season[5:7])
    prev_labels = [f"{y0 - k}-{(y1 - k) % 100:02d}" for k in range(1, HISTORY_SEASONS + 1)]
    cur = s[(s["round_no"] <= gw) & s["b_home"].notna()]
    prev = matches[matches["season"].isin(prev_labels) & matches["b_home"].notna()]
    train = pd.concat([prev, cur])
    if len(train) < MIN_MATCHES:
        return None, f"skipped: only {len(train)} matches with odds before this run"
    ref = cur["match_date"].max() if len(cur) else pd.to_datetime(s["match_date"]).min() - pd.Timedelta(days=1)
    teams = sorted(set(train["home"]) | set(train["away"]))
    params = fit_market(train, teams, ref)
    params["rho"] = 0.0
    missing = dc.fill_missing_teams(params, season_teams)
    preds = dc.predict(params, s[["match_id", "home", "away"]])
    note = f"fitted on {len(train)} matches with odds; home x{np.exp(params['home']):.3f}"
    if missing:
        note += f"; no odds yet for {', '.join(missing)} (bottom-3 average)"
    return preds[["match_id", "p_home", "p_draw", "p_away"]], note


def main():
    from sqlalchemy import create_engine, text
    engine = create_engine(os.environ.get("PREM_DB_URL", DB_URL))
    matches = pd.read_sql("""
        SELECT m.match_id, s.label AS season, m.round_no, m.match_date,
               th.team_name AS home, ta.team_name AS away,
               b.p_home AS b_home, b.p_draw AS b_draw, b.p_away AS b_away
        FROM matches m
        JOIN seasons s ON s.season_id = m.season_id
        JOIN teams th  ON th.team_id = m.home_team_id
        JOIN teams ta  ON ta.team_id = m.away_team_id
        LEFT JOIN external_predictions b
               ON b.match_id = m.match_id AND b.source = 'bookmaker' AND b.run_label = 'odds'""", engine)
    runs = pd.read_sql("SELECT run_label, season, gameweek FROM v_runs ORDER BY season, gameweek", engine)
    if ONLY_RUN:
        runs = runs[runs["run_label"] == ONLY_RUN]
    print(f"Fitting source '{SOURCE}' with XI {XI} (half weight after {np.log(2) / XI:.0f} days), RIDGE {RIDGE}")
    if runs.empty:
        sys.exit("No runs found. Check ONLY_RUN matches a run_label exactly.")
    for _, run in runs.iterrows():
        label = run["run_label"]
        preds, note = run_one(run, matches)
        print(f"{label}: {note}")
        if preds is None:
            continue
        with engine.begin() as con:
            con.execute(text("DELETE FROM external_predictions WHERE source = :s AND run_label = :r"), {"s": SOURCE, "r": label})
        p = preds.copy()
        p[["p_home", "p_draw", "p_away"]] = p[["p_home", "p_draw", "p_away"]].astype(float).round(4)
        p.insert(0, "run_label", label); p.insert(0, "source", SOURCE)
        p.to_sql("external_predictions", engine, if_exists="append", index=False)
    print(f"Done: stored as source '{SOURCE}' (XI {XI}, RIDGE {RIDGE}).")


if __name__ == "__main__":
    main()
