"""
Upgrade 27a: are the season forecasts honest at the edges (title, top 4, relegation, final position)?

    python season_tails.py

Every run's finishing-position chances (the Excel model's 1,000 sims, v_position_probs, and the
Python uncertainty sims, dyn_position_probs) for every season with a final table.

1. PIT: where the actual finish fell in each forecast's distribution (0 = far better than forecast,
   1 = far worse). An honest forecast gives an even spread over 5 bins (20% each).
   Too many in the end bins = overconfident (outcomes outside what it thought likely);
   too many in the middle = underconfident (too wide).
2. Coverage: how often the actual finish was inside the forecast's central 50%, 80% and 95% range.
   Honest = 50%, 80%, 95%. Below = overconfident, above = too cautious.
3. Tails: title, top-4 and relegation chances grouped by size, against how often they happened.

Sample size warning: a season has only 20 outcomes, and every run in a season is judged on the same
final table, so the runs are not independent. Two seasons = 40 real outcomes. Read big gaps only.
"""
import numpy as np
import pandas as pd
from scipy.stats import chisquare

import quant_tools as qt

LEVELS = (0.5, 0.8, 0.95)
STAGES = [(0, 0, "preseason"), (1, 10, "GW1-10"), (11, 22, "GW11-22"), (23, 38, "GW23+")]
TAIL_BINS = [0, 2, 10, 30, 60, 100.1]
TAIL_LABELS = ["0-2%", "2-10%", "10-30%", "30-60%", "60-100%"]


def load(eng):
    ex = qt.q("""SELECT 'excel' AS source, season, gameweek, run_label, team_name, final_position, pct
                 FROM v_position_probs""", eng=eng)
    try:
        dy = qt.q("""SELECT p.source, r.season, r.gameweek, p.run_label, p.team_name, p.final_position, p.pct
                     FROM dyn_position_probs p JOIN v_runs r USING (run_label)""", eng=eng)
    except Exception:                            # noqa: BLE001 - table missing: Excel sims only
        dy = pd.DataFrame(columns=ex.columns)
    act = qt.q("""SELECT s.label AS season, t.team_name, f.position
                  FROM final_tables f JOIN seasons s USING (season_id) JOIN teams t USING (team_id)""", eng=eng)
    probs = pd.concat([ex, dy], ignore_index=True)
    probs = probs.merge(act, on=["season", "team_name"])
    return probs


def forecasts(probs):
    """One row per source / run / team: PIT, coverage flags, interval width, tail chances."""
    rng = np.random.default_rng(2026)
    out = []
    for (src, run, team), g in probs.groupby(["source", "run_label", "team_name"]):
        p = np.zeros(21)
        p[g["final_position"].astype(int).to_numpy()] = g["pct"].astype(float).to_numpy()
        p = p[1:] / p[1:].sum()
        F = np.cumsum(p)
        k = int(g["position"].iloc[0])
        pit = (F[k - 2] if k > 1 else 0.0) + rng.uniform() * p[k - 1]
        row = {"source": src, "run_label": run, "season": g["season"].iloc[0],
               "gameweek": int(g["gameweek"].iloc[0]), "team": team, "actual": k, "pit": pit,
               "title": 100 * p[0], "top4": 100 * p[:4].sum(), "releg": 100 * p[17:].sum()}
        for lv in LEVELS:
            lo = int(np.searchsorted(F, (1 - lv) / 2 - 1e-9) + 1)
            hi = int(np.searchsorted(F, 1 - (1 - lv) / 2 - 1e-9) + 1)
            row[f"in{int(lv * 100)}"] = lo <= k <= hi
            row[f"w{int(lv * 100)}"] = hi - lo + 1
        out.append(row)
    return pd.DataFrame(out)


def stage(gw):
    for lo, hi, name in STAGES:
        if lo <= gw <= hi:
            return name
    return "other"


def main():
    eng = qt.engine()
    probs = load(eng)
    if probs.empty:
        raise SystemExit("No position chances for seasons with a final table.")
    f = forecasts(probs)
    f["stage"] = f["gameweek"].map(stage)
    seasons = sorted(f["season"].unique())
    print(f"Seasons with a final table: {', '.join(seasons)} ({20 * len(seasons)} real outcomes); "
          f"{f['run_label'].nunique()} runs, sources: {', '.join(sorted(f['source'].unique()))}")

    print("\n1-2. PIT bins (% of forecasts; honest = 20 each) and coverage (honest = 50 / 80 / 95)")
    rows = []
    for (src, stg), g in f.groupby(["source", "stage"], sort=False):
        counts = np.histogram(g["pit"], bins=5, range=(0, 1))[0]
        p = chisquare(counts).pvalue if len(g) >= 25 else np.nan
        rows.append({"source": src, "stage": stg, "forecasts": len(g),
                     **{f"PIT {i * 20}-{i * 20 + 20}": round(100 * c / len(g)) for i, c in enumerate(counts)},
                     "in 50%": round(100 * g["in50"].mean()), "in 80%": round(100 * g["in80"].mean()),
                     "in 95%": round(100 * g["in95"].mean()), "80% range (places)": round(g["w80"].mean(), 1),
                     "p": round(p, 3) if not np.isnan(p) else ""})
    t = pd.DataFrame(rows)
    order = {s[2]: i for i, s in enumerate(STAGES)}
    t = t.sort_values(["source", "stage"], key=lambda c: c.map(order) if c.name == "stage" else c)
    pd.set_option("display.width", 250)
    print(t.to_string(index=False))
    print("p = chi-square test that the PIT bins are even; it treats runs as independent, so it is too "
          "optimistic: only trust p below 0.01.")

    print("\n3. Tails: forecast chance vs how often it happened (all runs of each source pooled)")
    for src, g in f.groupby("source"):
        print(f"\n  {src}")
        rows = []
        for col, name, hit in (("title", "title", g["actual"] == 1), ("top4", "top 4", g["actual"] <= 4),
                               ("releg", "relegation", g["actual"] >= 18)):
            band = pd.cut(g[col], TAIL_BINS, labels=TAIL_LABELS, right=False)
            for b, gg in g.assign(band=band, hit=hit).groupby("band", observed=True):
                rows.append({"event": name, "forecast band": b, "forecasts": len(gg),
                             "teams": gg[["season", "team"]].drop_duplicates().shape[0],
                             "avg forecast %": round(gg[col].mean(), 1), "happened %": round(100 * gg["hit"].mean(), 1)})
        print(pd.DataFrame(rows).to_string(index=False))

    print("\nReading it (each source and stage with 40+ forecasts)")
    for (src, stg), g in f.groupby(["source", "stage"], sort=False):
        if len(g) < 40:
            continue
        c80 = 100 * g["in80"].mean()
        ends = np.histogram(g["pit"], bins=5, range=(0, 1))[0]
        ends = 100 * (ends[0] + ends[-1]) / len(g)
        if c80 < 70 or ends > 55:
            print(f"- {src} {stg}: OVERCONFIDENT. {c80:.0f}% of finishes inside the 80% range "
                  f"({ends:.0f}% in the two end PIT bins, honest 40%).")
        elif c80 > 92:
            print(f"- {src} {stg}: too cautious ({c80:.0f}% inside the 80% range).")
        else:
            print(f"- {src} {stg}: honest within the noise ({c80:.0f}% inside the 80% range).")
    print("- In the tails table, compare 'avg forecast %' with 'happened %' only where 'teams' is 5 or more; "
          "the 0-2% bands matter most (a 1% team that goes down is a big miss if it happens often).")


if __name__ == "__main__":
    main()
