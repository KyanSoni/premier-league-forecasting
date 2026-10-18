"""
Upgrade 12: extra benchmark models for the prem_sims database.

For every run in v_runs (or just ONLY_RUN) it fits, using ONLY matches played before the run:
  pi_ratings : Constantinou & Fenton (2013) pi-ratings (separate home/away ratings, goal-margin
               updates with diminishing returns), turned into home/draw/away chances with an
               ordered-logit fitted on the same history.
  dc_xg      : Dixon-Coles fitted on expected goals instead of goals (less luck in the inputs).
Predictions go to external_predictions (source = 'pi_ratings' / 'dc_xg') and are scored by v_scores,
exactly like Dixon-Coles.

Run:  python benchmarks.py          (same folder and same DB_URL as dc_model.py)
"""
import math
import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, gammaln

import dc_model as dc        # re-uses your Dixon-Coles code and settings

# ============================== CONFIG ==============================
DB_URL = dc.DB_URL           # taken from dc_model.py, so the password lives in one place
ONLY_RUN = None              # e.g. "2026-27 after GW6"; None = every run
HISTORY_SEASONS = 2          # past seasons used for warm-up / fitting
PI_LAMBDA = 0.035            # pi-ratings learning rate (paper's value)
PI_GAMMA = 0.7               # how much a home result moves the away rating (paper's value)
XG_RIDGE = 10                # shrinkage for dc_xg (same idea as RIDGE in dc_model.py)
# ====================================================================


# ---------------------------------------------------------------- pi-ratings
def _g(r):   # rating -> expected goal margin
    return math.copysign(10 ** (abs(r) / 3) - 1, r)


def _psi(e):  # error -> diminishing update
    return math.copysign(3 * math.log10(1 + abs(e)), e)


def pi_history(train, season_teams):
    """Run pi-ratings through train (sorted by date). Returns ratings and (expected margin, result) pairs."""
    R = {}
    pairs = []
    for m in train.sort_values("match_date").itertuples():
        for t in (m.home, m.away):
            if t not in R:                         # newcomer: start at the average of the 3 weakest
                if len(R) >= 3:
                    low = sorted(R.values(), key=lambda v: v[0] + v[1])[:3]
                    R[t] = [np.mean([v[0] for v in low]), np.mean([v[1] for v in low])]
                else:
                    R[t] = [0.0, 0.0]
        hh, aa = R[m.home][0], R[m.away][1]
        exp_gd = _g(hh) - _g(aa)
        res = 0 if m.hg < m.ag else (1 if m.hg == m.ag else 2)
        pairs.append((exp_gd, res))
        e = (m.hg - m.ag) - exp_gd
        d = _psi(e) * PI_LAMBDA
        R[m.home][0] += d
        R[m.home][1] += d * PI_GAMMA
        R[m.away][1] -= d
        R[m.away][0] -= d * PI_GAMMA
    for t in season_teams:
        if t not in R:
            low = sorted(R.values(), key=lambda v: v[0] + v[1])[:3]
            R[t] = [np.mean([v[0] for v in low]), np.mean([v[1] for v in low])]
    return R, pairs


def fit_ordered_logit(pairs):
    x = np.array([p[0] for p in pairs]); y = np.array([p[1] for p in pairs])
    def nll(p):
        b, c1, d = p
        c2 = c1 + math.exp(d)
        pa = expit(c1 - b * x); pad = expit(c2 - b * x)
        pr = np.where(y == 0, pa, np.where(y == 1, pad - pa, 1 - pad))
        return -np.log(np.clip(pr, 1e-12, None)).sum()
    r = minimize(nll, [1.0, -0.5, 0.0], method="Nelder-Mead", options={"maxiter": 4000})
    b, c1, d = r.x
    return b, c1, c1 + math.exp(d)


def run_pi(train, season, season_teams):
    R, pairs = pi_history(train, season_teams)
    if len(pairs) < 50:
        return None
    b, c1, c2 = fit_ordered_logit(pairs[len(pairs) // 3:])   # skip the warm-up third
    rows = []
    for f in season.itertuples():
        x = _g(R[f.home][0]) - _g(R[f.away][1])
        pa = expit(c1 - b * x); pad = expit(c2 - b * x)
        rows.append((f.match_id, 1 - pad, pad - pa, pa))
    return pd.DataFrame(rows, columns=["match_id", "p_home", "p_draw", "p_away"])


# ---------------------------------------------------------------- Dixon-Coles on xG
def run_dc_xg(train, season, season_teams, ref_date):
    t = train.dropna(subset=["xg_home", "xg_away"]).copy()
    if len(t) < 50:
        return None
    t["hg"], t["ag"] = t["xg_home"].astype(float), t["xg_away"].astype(float)
    old = dc.RIDGE
    dc.RIDGE = XG_RIDGE
    try:
        teams = sorted(set(t["home"]) | set(t["away"]))
        params = dc.fit_dixon_coles(t, teams, ref_date)    # gammaln handles non-whole xG values
    finally:
        dc.RIDGE = old
    params["rho"] = 0.0                                    # rho is a goals effect; xG has no 0-0 excess
    dc.fill_missing_teams(params, season_teams)
    return dc.predict(params, season[["match_id", "home", "away"]])[["match_id", "p_home", "p_draw", "p_away"]]


# ---------------------------------------------------------------- one run
def run_one(run, matches):
    season_lbl, gw = run["season"], int(run["gameweek"])
    s = matches[matches["season"] == season_lbl]
    season_teams = sorted(set(s["home"]) | set(s["away"]))
    y0, y1 = int(season_lbl[:4]), int(season_lbl[5:7])
    prev_labels = [f"{y0 - k}-{(y1 - k) % 100:02d}" for k in range(1, HISTORY_SEASONS + 1)]
    cur = s[(s["round_no"] <= gw) & s["hg"].notna()]
    prev = matches[matches["season"].isin(prev_labels) & matches["hg"].notna()]
    train = pd.concat([prev, cur])
    ref_date = cur["match_date"].max() if len(cur) else pd.to_datetime(s["match_date"]).min() - pd.Timedelta(days=1)
    return {"pi_ratings": run_pi(train, s, season_teams),
            "dc_xg": run_dc_xg(train, s, season_teams, ref_date)}


def main():
    from sqlalchemy import create_engine, text
    engine = create_engine(os.environ.get("PREM_DB_URL", DB_URL))
    matches = pd.read_sql("""
        SELECT m.match_id, s.label AS season, m.round_no, m.match_date,
               th.team_name AS home, ta.team_name AS away,
               m.home_goals AS hg, m.away_goals AS ag, x.xg_home, x.xg_away
        FROM matches m
        JOIN seasons s ON s.season_id = m.season_id
        JOIN teams th  ON th.team_id = m.home_team_id
        JOIN teams ta  ON ta.team_id = m.away_team_id
        LEFT JOIN match_xg x ON x.match_id = m.match_id""", engine)
    runs = pd.read_sql("SELECT run_label, season, gameweek FROM v_runs ORDER BY season, gameweek", engine)
    if ONLY_RUN:
        runs = runs[runs["run_label"] == ONLY_RUN]
    if runs.empty:
        sys.exit("No runs found. Check ONLY_RUN matches a run_label exactly.")
    for _, run in runs.iterrows():
        label = run["run_label"]
        out = run_one(run, matches)
        for source, preds in out.items():
            if preds is None:
                print(f"{label} {source}: skipped (under 50 matches of history)")
                continue
            with engine.begin() as con:
                con.execute(text("DELETE FROM external_predictions WHERE source = :s AND run_label = :r"),
                            {"s": source, "r": label})
            p = preds.copy()
            p[["p_home", "p_draw", "p_away"]] = p[["p_home", "p_draw", "p_away"]].astype(float).round(4)
            p.insert(0, "run_label", label); p.insert(0, "source", source)
            p.to_sql("external_predictions", engine, if_exists="append", index=False)
            print(f"{label} {source}: {len(p)} predictions saved")
    print("Done. In pgAdmin run the Upgrade 12 comparison, then Refresh All in Excel.")


if __name__ == "__main__":
    main()
