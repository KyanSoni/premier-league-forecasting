"""
Upgrade 19: is the match model too flat? Measure it, and fix it with one calibration number.

    python calibrate.py            # diagnose and test (changes nothing)
    python calibrate.py --write    # also save '<model>_cal' predictions for every run

What it does
  1. Takes the main match model (ens_dcxg_50, or the best that exists) and, for every match in
     2024-25 and 2025-26, the prediction from the latest run before the match.
  2. Calibration slope for home / draw / away: 1.00 is perfect, above 1 means the model is too
     cautious (chances too close to the middle), below 1 means too confident.
  3. Fits one sharpening power 'a' (each chance raised to the power a, then rescaled to add to 1)
     on one season and tests it on the other, both ways round, against RPS and log loss.
  4. The decision rule (same as every upgrade): adopt only if it improves BOTH test seasons.
     Then it prints the CAL_POWER to put in weekly.py, fitted on both seasons together.
"""
import argparse

import numpy as np
import pandas as pd

import quant_tools as qt

MODELS = ["ens_dcxg_50", "value_prom_50", "ens_dc_25", "xg_50"]   # first one found is used


def season_scores(p, y):
    return qt.rps(p, y).mean(), qt.logloss(p, y).mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--write", action="store_true", help="save <model>_cal rows to external_predictions")
    args = ap.parse_args()

    eng = qt.engine()
    model = args.model or qt.first_existing(MODELS, eng)
    if not model:
        raise SystemExit("None of " + ", ".join(MODELS) + " found in v_scores.")
    print(f"Model: {model}  (each match predicted once, by the latest run before it)")
    d = qt.closest_predictions(model, eng=eng)
    if d.empty:
        raise SystemExit("No predictions found for 2024-25 / 2025-26.")
    bk = qt.q("""SELECT match_id, p_home AS b_home, p_draw AS b_draw, p_away AS b_away
                 FROM external_predictions WHERE source = 'bookmaker' AND run_label = 'odds'""", eng=eng)
    d = d.merge(bk, on="match_id", how="left")
    P = d[["p_home", "p_draw", "p_away"]].to_numpy(float)
    y = d["y"].to_numpy(int)

    print("\n1. Calibration slope (1.00 = perfect; above 1 = too flat; ± is one standard error)")
    rows = []
    for season, g in d.groupby("season"):
        pg, yg = g[["p_home", "p_draw", "p_away"]].to_numpy(float), g["y"].to_numpy(int)
        for k, name in enumerate(["home", "draw", "away"]):
            a, b, se = qt.logistic_slope(pg[:, k], yg == k)
            rows.append({"season": season, "outcome": name, "matches": len(g),
                         "slope": round(b, 2), "±": round(se, 2), "intercept": round(a, 2)})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n2. Sharpening power, fitted on one season and tested on the other")
    out, better = [], 0
    for train, test in [("2024-25", "2025-26"), ("2025-26", "2024-25")]:
        tr, te = d["season"] == train, d["season"] == test
        if not tr.any() or not te.any():
            continue
        a = qt.fit_power(P[tr], y[tr])
        r0, l0 = season_scores(P[te], y[te])
        r1, l1 = season_scores(qt.power_scale(P[te], a), y[te])
        gb = d.loc[te, ["b_home", "b_draw", "b_away"]].dropna()
        rb = qt.rps(gb.to_numpy(float), y[te][d.loc[te, "b_home"].notna().to_numpy()]).mean() if len(gb) else np.nan
        better += (r1 < r0) and (l1 < l0)
        out.append({"fit on": train, "test on": test, "a": round(a, 3), "matches": int(te.sum()),
                    "RPS before": round(r0, 4), "RPS after": round(r1, 4),
                    "log loss before": round(l0, 4), "log loss after": round(l1, 4),
                    "bookmakers RPS": round(rb, 4)})
    print(pd.DataFrame(out).to_string(index=False))

    a_all = qt.fit_power(P, y)
    adopt = better == len(out) and len(out) == 2
    print(f"\nPower fitted on both seasons: a = {a_all:.3f}")
    if adopt:
        print(f"DECISION: ADOPT. Better in both test seasons. Set CAL_POWER = {a_all:.2f} in weekly.py's CONFIG.")
    else:
        print("DECISION: NOT ADOPTED (not better in both test seasons). Leave CAL_POWER = 1.0 in weekly.py.")

    if args.write and not adopt:
        print("(--write skipped: only save calibrated predictions once the test says ADOPT)")
    elif args.write:
        allp = qt.q("""SELECT run_label, match_id, p_home, p_draw, p_away FROM external_predictions
                       WHERE source = :m""", {"m": model}, eng=eng)
        if allp.empty:
            print(f"(--write skipped: {model} is not stored in external_predictions)")
            return
        cal = qt.power_scale(allp[["p_home", "p_draw", "p_away"]].to_numpy(float), a_all)
        rows = [{"s": model + "_cal", "r": r, "m": int(m), "h": round(float(c[0]), 4),
                 "d": round(float(c[1]), 4), "a": round(float(c[2]), 4)}
                for r, m, c in zip(allp["run_label"], allp["match_id"], cal)]
        qt.execute_many("""INSERT INTO external_predictions (source, run_label, match_id, p_home, p_draw, p_away)
                           VALUES (:s, :r, :m, :h, :d, :a)
                           ON CONFLICT (source, run_label, match_id)
                           DO UPDATE SET p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw,
                                         p_away = EXCLUDED.p_away""", rows, eng=eng)
        print(f"Saved {len(rows)} rows as source '{model}_cal' (a = {a_all:.3f}). "
              f"They now appear in v_scores and on the dashboard's model comparison.")


if __name__ == "__main__":
    main()
