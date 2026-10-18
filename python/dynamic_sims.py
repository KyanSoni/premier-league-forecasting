"""
Upgrade 11: season simulations that allow for rating uncertainty.

Your Excel sims treat every team's rating as exactly right for the rest of the season. Here each
simulated season first draws each team's attack and defence from around its rating
(multiplied by exp(N(0, sigma))), so a team can turn out better or worse than rated, as real teams do.
sigma shrinks as the season goes on: sigma_g = SIGMA0 * sqrt(15 / (15 + gameweek)).

It uses your main model's ratings for every run (the xg_50 rebuild before 2026-27 GW6, your exported
Excel ratings after), plays the rest of the season N_SIMS times, and stores
  dyn_sim_summary     (title / top-4 / relegation chances, average finish)
  dyn_position_probs  (chance of every finishing position)
with source = 'dyn_<sigma x 100>': dyn_0 (no uncertainty, like Excel), dyn_5, dyn_10 and dyn_15.
Upgrade 8's views score them automatically.

Run:  python dynamic_sims.py        (same folder as dc_model.py; uses its DB_URL)
"""
import os
import sys

import numpy as np
import pandas as pd

import dc_model as dc

# ============================== CONFIG ==============================
DB_URL = dc.DB_URL
ONLY_RUN = None          # e.g. "2026-27 after GW6"; None = every run
SIGMAS = [0.0, 0.10]   # 0.0 = fixed ratings (like Excel); the others add uncertainty
N_SIMS = 10000
SEED = 7
# ====================================================================


def simulate(ratings, avg_goals, home_f, away_f, season, gw, sigma0, rng):
    teams = sorted(set(season["home"]) | set(season["away"]))
    idx = {t: i for i, t in enumerate(teams)}
    n, N = len(teams), N_SIMS
    A = np.array([ratings[t][0] for t in teams])
    D = np.array([ratings[t][1] for t in teams])
    sig = sigma0 * np.sqrt(15 / (15 + gw))
    if sig > 0:
        A = A * np.exp(sig * rng.standard_normal((N, n)) - sig ** 2 / 2)
        D = D * np.exp(sig * rng.standard_normal((N, n)) - sig ** 2 / 2)
    else:
        A = np.tile(A, (N, 1)); D = np.tile(D, (N, 1))
    P = np.zeros((N, n)); GD = np.zeros((N, n)); GF = np.zeros((N, n))
    for m in season.itertuples():
        h, a = idx[m.home], idx[m.away]
        if m.round_no <= gw and not pd.isna(m.hg):
            hg = np.full(N, m.hg); ag = np.full(N, m.ag)
        else:
            hg = rng.poisson(A[:, h] * D[:, a] * avg_goals * home_f)
            ag = rng.poisson(A[:, a] * D[:, h] * avg_goals * away_f)
        GF[:, h] += hg; GF[:, a] += ag; GD[:, h] += hg - ag; GD[:, a] += ag - hg
        P[:, h] += np.where(hg > ag, 3, np.where(hg == ag, 1, 0))
        P[:, a] += np.where(ag > hg, 3, np.where(hg == ag, 1, 0))
    key = P * 1e6 + (GD + 500) * 1e3 + GF + rng.random((N, n)) * 0.1
    order = np.argsort(-key, axis=1)
    pos = np.empty_like(order)
    pos[np.arange(N)[:, None], order] = np.arange(1, n + 1)
    summary = pd.DataFrame({
        "team_name": teams,
        "avg_finish": pos.mean(0).round(1),
        "title_pct": (100 * (pos == 1).mean(0)).round(1),
        "top4_pct": (100 * (pos <= 4).mean(0)).round(1),
        "relegation_pct": (100 * (pos >= n - 2).mean(0)).round(1),
    })
    probs = pd.DataFrame([(t, k, round(100 * float((pos[:, i] == k).mean()), 2))
                          for i, t in enumerate(teams) for k in range(1, n + 1)],
                         columns=["team_name", "final_position", "pct"])
    return summary, probs[probs["pct"] > 0]


def main():
    from sqlalchemy import create_engine, text
    engine = create_engine(os.environ.get("PREM_DB_URL", DB_URL))
    matches = pd.read_sql("""
        SELECT s.label AS season, m.round_no, th.team_name AS home, ta.team_name AS away,
               m.home_goals AS hg, m.away_goals AS ag
        FROM matches m JOIN seasons s USING (season_id)
        JOIN teams th ON th.team_id = m.home_team_id JOIN teams ta ON ta.team_id = m.away_team_id""", engine)
    rat = pd.read_sql("""
        SELECT r.run_label, r.season, r.gameweek, t.team_name, vr.att, vr.def AS dfn,
               ra.avg_goals, v.home_factor, v.away_factor
        FROM v_variant_ratings vr
        JOIN v_runs r          ON r.run_id = vr.run_id
        JOIN v_run_avg ra      ON ra.run_id = vr.run_id
        JOIN model_variants v  ON v.variant = vr.variant
        JOIN teams t           ON t.team_id = vr.team_id
        WHERE vr.variant = CASE WHEN r.season >= '2026-27' AND r.gameweek >= 6
                                THEN 'home_adv' ELSE 'xg_50' END
          AND vr.variant IN ('home_adv', 'xg_50')""", engine)
    if ONLY_RUN:
        rat = rat[rat["run_label"] == ONLY_RUN]
    if rat.empty:
        sys.exit("No runs found. Check ONLY_RUN matches a run_label exactly.")
    print(f"{rat['run_label'].nunique()} runs x {len(SIGMAS)} settings, {N_SIMS:,} seasons each")
    rng = np.random.default_rng(SEED)
    for label, g in rat.groupby("run_label"):
        season = matches[matches["season"] == g["season"].iloc[0]]
        ratings = {r.team_name: (float(r.att), float(r.dfn)) for r in g.itertuples()}
        avg, hf, af = float(g["avg_goals"].iloc[0]), float(g["home_factor"].iloc[0]), float(g["away_factor"].iloc[0])
        gw = int(g["gameweek"].iloc[0])
        for sigma in SIGMAS:
            src = f"dyn_{int(round(sigma * 100))}"
            summ, probs = simulate(ratings, avg, hf, af, season, gw, sigma, rng)
            with engine.begin() as con:
                con.execute(text("DELETE FROM dyn_sim_summary    WHERE source = :s AND run_label = :r"), {"s": src, "r": label})
                con.execute(text("DELETE FROM dyn_position_probs WHERE source = :s AND run_label = :r"), {"s": src, "r": label})
            summ.insert(0, "run_label", label); summ.insert(0, "source", src)
            probs.insert(0, "run_label", label); probs.insert(0, "source", src)
            summ.to_sql("dyn_sim_summary", engine, if_exists="append", index=False)
            probs.to_sql("dyn_position_probs", engine, if_exists="append", index=False)
            leader = summ.sort_values("title_pct", ascending=False).iloc[0]
            print(f"{label} {src}: leader {leader.team_name} {leader.title_pct}% title")
    print("Done. Now run blocks 11.1 to 11.6 of 26_upgrade11_12_sims_benchmarks.sql.")


if __name__ == "__main__":
    main()
