"""
Upgrade 22: were the model upgrades real, or luck? A paired bootstrap on every pair of models.

    python significance.py                       # the upgrade-log comparisons
    python significance.py xg_50 ens_dcxg_50     # any two models (old first, new second)

For each pair it uses only predictions both models made (same run, same match; out of sample,
runs after GW3 to GW34 of 2024-25 and 2025-26). The bookmakers are compared on the same matches.

Why a bootstrap by match: one match is predicted by up to 10 runs, so those rows are not
independent. The bootstrap redraws whole matches (with all their runs) 5,000 times, which gives
an honest 95% interval for the RPS change. If the interval is entirely below zero, the newer
model is genuinely better; if it crosses zero, the gain could be luck.

Output: a table, plus significance.csv (paste into the Upgrade log sheet).
"""
import sys

import numpy as np
import pandas as pd

import quant_tools as qt

PAIRS = [  # (old, new, upgrade)
    ("base", "home_adv", "1. Home advantage"),
    ("home_adv", "blend_15", "2. Blend ratings"),
    ("blend_15", "xg_50", "5. xG ratings"),
    ("xg_50", "dixon_coles", "6. Dixon-Coles alone"),
    ("xg_50", "ens_dc_25", "9. Ensemble with Dixon-Coles"),
    ("ens_dc_25", "ens_mkt_50", "10. Market-implied ensemble"),
    ("ens_dc_25", "ens_dcxg_50", "12. Ensemble with DC on xG"),
    ("xg_50", "value_prom_50", "13. Squad value (promoted)"),
    ("ens_dcxg_50", "ens_dcxg_50_cal", "19. Calibration"),
    ("ens_dcxg_50", "bookmaker", "Gap to the bookmakers"),
]
N_BOOT = 5000
SEED = 2026


def load(model, eng):
    d = qt.q("""SELECT model, season, run_label, match_id, p_home, p_draw, p_away, result, rps
                FROM v_scored_predictions
                WHERE model = :m AND season = ANY(:s)
                  AND (run_label LIKE '%after GW%' OR run_label = 'odds')""",
             {"m": model, "s": qt.TEST_SEASONS}, eng=eng)
    d["y"] = d["result"].map({"H": 0, "D": 1, "A": 2})
    d["ll"] = qt.logloss(d[["p_home", "p_draw", "p_away"]].to_numpy(float), d["y"].to_numpy())
    return d


def compare(a, b):
    """Rows both models predicted. The bookmaker has one row per match (run 'odds'), so it is
    matched to every run's prediction of that match."""
    on = ["match_id"] if (a["run_label"] == "odds").all() or (b["run_label"] == "odds").all() else ["run_label", "match_id"]
    m = a.merge(b, on=on, suffixes=("_a", "_b"))
    if "season_a" in m:
        m["season"] = m["season_a"]
    return m


def boot(m, col_a, col_b, rng):
    """Paired cluster bootstrap by match: mean difference (b - a) and its 95% interval."""
    g = m.assign(diff=m[col_b].astype(float) - m[col_a].astype(float)).groupby("match_id")["diff"].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = rng.integers(0, len(g), size=(N_BOOT, len(g)))
    means = s[idx].sum(1) / c[idx].sum(1)
    est = s.sum() / c.sum()
    lo, hi = np.percentile(means, [2.5, 97.5])
    p = 2 * min((means <= 0).mean(), (means >= 0).mean())
    return est, lo, hi, max(p, 1 / N_BOOT)


def main():
    eng = qt.engine()
    pairs = [(sys.argv[1], sys.argv[2], "custom")] if len(sys.argv) == 3 else PAIRS
    have = set(qt.q("SELECT DISTINCT model FROM v_scores", eng=eng)["model"])
    cache, rows = {}, []
    rng = np.random.default_rng(SEED)
    for old, new, label in pairs:
        if old not in have or new not in have:
            print(f"skip {label}: {old if old not in have else new} not found")
            continue
        for mdl in (old, new):
            if mdl not in cache:
                cache[mdl] = load(mdl, eng)
        m = compare(cache[old], cache[new])
        if m.empty:
            continue
        r = {"upgrade": label, "old": old, "new": new, "predictions": len(m), "matches": m["match_id"].nunique(),
             "RPS old": m["rps_a"].astype(float).mean(), "RPS new": m["rps_b"].astype(float).mean()}
        est, lo, hi, p = boot(m, "rps_a", "rps_b", rng)
        r.update({"RPS change": est, "95% low": lo, "95% high": hi, "p": p})
        for season in qt.TEST_SEASONS:
            ms = m[m["season"] == season]
            r[f"change {season}"] = (ms["rps_b"].astype(float) - ms["rps_a"].astype(float)).mean() if len(ms) else np.nan
        ll_est, ll_lo, ll_hi, _ = boot(m, "ll_a", "ll_b", rng)
        r["log loss change"] = ll_est
        same_sign = np.sign(r[f"change {qt.TEST_SEASONS[0]}"]) == np.sign(r[f"change {qt.TEST_SEASONS[1]}"])
        if hi < 0 and same_sign:
            r["verdict"] = "real gain"
        elif lo > 0 and same_sign:
            r["verdict"] = "real loss"
        elif hi < 0 or lo > 0:
            r["verdict"] = "significant overall, seasons disagree"
        else:
            r["verdict"] = "could be luck"
        rows.append(r)

    t = pd.DataFrame(rows)
    if t.empty:
        raise SystemExit("No pairs to compare.")
    show = t.copy()
    for c in ["RPS old", "RPS new", "RPS change", "95% low", "95% high", "log loss change"] + \
             [f"change {s}" for s in qt.TEST_SEASONS]:
        show[c] = show[c].map(lambda v: f"{v:+.4f}" if "change" in c or "%" in c else f"{v:.4f}")
    show["p"] = show["p"].map(lambda v: f"{v:.3f}")
    pd.set_option("display.width", 250)
    print("\nRPS change = new minus old (negative = new is better). 95% interval from 5,000 bootstrap redraws of whole matches.")
    print(show[["upgrade", "matches", "RPS old", "RPS new", "RPS change", "95% low", "95% high", "p",
                f"change {qt.TEST_SEASONS[0]}", f"change {qt.TEST_SEASONS[1]}", "log loss change", "verdict"]]
          .to_string(index=False))
    t.to_csv("significance.csv", index=False)
    print("\nSaved significance.csv")


if __name__ == "__main__":
    main()
