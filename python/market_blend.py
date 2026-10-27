"""
Upgrade 20: how much should your model count against the bookmakers, and how should the
bookmaker's margin be removed?

    python market_blend.py

Three parts, all out of sample on 2024-25 and 2025-26 (2023-24 is used in part 1 too):
  1. Margin removal: proportional (what you use now), power and Shin, scored against results
     on Pinnacle's closing prices (the sharpest prices in football-data's files).
  2. Blend weight: blended chance = w x model + (1 - w) x bookmaker fair chance, with w fitted by
     log loss on Bet365's pre-match prices (the prices your value log uses), for 1X2 and for
     over/under 2.5 separately.
  3. Flag backtest: the VALUE flag (blended edge of 5% or more) replayed on every past match for
     several weights, judged by the closing line (Pinnacle) and by results.

It prints the four CONFIG lines to set in weekly.py. Nothing is changed in the database.
"""
import re

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import poisson

import quant_tools as qt

METHODS = ["proportional", "power", "shin"]
MODELS = ["ens_dcxg_50_cal", "ens_dcxg_50", "value_prom_50", "xg_50"]
FLAG_EDGE = 0.05
WEIGHTS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]


def cols(d, *names):
    """Row by row, the first set of columns that has prices (e.g. Pinnacle closing, else
    market-average closing: football-data dropped Pinnacle from 2026-27)."""
    out = np.full((len(d), len(names[0])), np.nan)
    for group in names:
        if all(c in d.columns for c in group):
            v = d[list(group)].apply(pd.to_numeric, errors="coerce").to_numpy(float)
            gap = np.isnan(out).any(1) & ~np.isnan(v).any(1)
            out[gap] = v[gap]
    return out


def load_fd():
    parts = [qt.read_fd(s) for s in qt.SEASONS]
    fd = pd.concat([p for p in parts if p is not None], ignore_index=True)
    fd = fd[fd["FTHG"].notna()].copy()
    fd["y"] = qt.result_index(fd["FTHG"], fd["FTAG"])
    fd["over"] = (fd["FTHG"] + fd["FTAG"] >= 3).astype(int)
    return fd


def part1_margins(fd):
    print("\n1. Margin removal on closing prices (log loss, lower is better; scored against results)")
    close12 = cols(fd, ("PSCH", "PSCD", "PSCA"), ("AvgCH", "AvgCD", "AvgCA"))
    close_ou = cols(fd, ("PC>2.5", "PC<2.5"), ("AvgC>2.5", "AvgC<2.5"))
    rows = []
    for season in sorted(fd["season"].unique()):
        idx = (fd["season"] == season).to_numpy()
        r = {"season": season, "matches": int(idx.sum())}
        for m in METHODS:
            p = qt.fair_probs(close12[idx], m)
            ok = ~np.isnan(p).any(1)
            r[f"1X2 {m}"] = qt.logloss(p[ok], fd["y"].to_numpy()[idx][ok]).mean()
            po = qt.fair_probs(close_ou[idx], m)
            ok2 = ~np.isnan(po).any(1)
            yo = 1 - fd["over"].to_numpy()[idx][ok2]          # column 0 = over
            r[f"O/U {m}"] = qt.logloss(po[ok2], yo).mean()
        rows.append(r)
    t = pd.DataFrame(rows)
    print(t.round(4).to_string(index=False))
    tot = {m: t[f"1X2 {m}"].mean() + t[f"O/U {m}"].mean() for m in METHODS}
    best = min(tot, key=tot.get)
    print(f"Best overall: {best}")
    return best


def fit_w(pm, pb, y):
    f = lambda w: qt.logloss(w * pm + (1 - w) * pb, y).mean()
    return float(minimize_scalar(f, bounds=(0, 1), method="bounded").x)


def model_ou(eng):
    """Model chance of over 2.5 for each match, from the run just before it (match_lambdas)."""
    lam = qt.q("""SELECT l.run_label, l.match_id, l.lambda_home, l.lambda_away, m.round_no
                  FROM match_lambdas l JOIN matches m USING (match_id)""", eng=eng)
    lam["gw"] = lam["run_label"].map(lambda s: int(re.search(r"GW(\d+)", s).group(1)) if "GW" in s else -1)
    lam = lam[lam["gw"] < lam["round_no"]].sort_values("gw").drop_duplicates("match_id", keep="last")
    lh, la = lam["lambda_home"].astype(float).to_numpy(), lam["lambda_away"].astype(float).to_numpy()
    under = sum(poisson.pmf(i, lh) * poisson.pmf(j, la) for i in range(3) for j in range(3) if i + j <= 2)
    lam["m_over"] = 1 - under
    return lam[["match_id", "m_over"]]


def main():
    eng = qt.engine()
    fd = load_fd()
    method = part1_margins(fd)
    fd = qt.attach_match_ids(fd, eng)

    model = qt.first_existing(MODELS, eng)
    pr = qt.closest_predictions(model, eng=eng)
    print(f"\n2. Blend weight (model = {model}, each match predicted by the latest run before it)")
    d = fd.merge(pr[["match_id", "p_home", "p_draw", "p_away"]], on="match_id", how="inner")
    d = d.merge(model_ou(eng), on="match_id", how="left")
    open12 = cols(d, ("B365H", "B365D", "B365A"))
    open_ou = cols(d, ("B365>2.5", "B365<2.5"))
    close12 = cols(d, ("PSCH", "PSCD", "PSCA"), ("AvgCH", "AvgCD", "AvgCA"))
    close_ou = cols(d, ("PC>2.5", "PC<2.5"), ("AvgC>2.5", "AvgC<2.5"))
    fb12, fc12 = qt.fair_probs(open12, method), qt.fair_probs(close12, method)
    fbou, fcou = qt.fair_probs(open_ou, method), qt.fair_probs(close_ou, method)
    pm12 = d[["p_home", "p_draw", "p_away"]].to_numpy(float)
    pmou = np.column_stack([d["m_over"], 1 - d["m_over"]])
    y12, you = d["y"].to_numpy(), 1 - d["over"].to_numpy()

    rows, w12, wou = [], {}, {}
    for season in qt.TEST_SEASONS:
        s = (d["season"] == season).to_numpy()
        ok = s & ~np.isnan(fb12).any(1)
        w12[season] = fit_w(pm12[ok], fb12[ok], y12[ok])
        ok2 = s & ~np.isnan(fbou).any(1) & ~np.isnan(pmou).any(1)
        wou[season] = fit_w(pmou[ok2], fbou[ok2], you[ok2]) if ok2.sum() > 50 else np.nan
        rows.append({"season": season, "1X2 matches": int(ok.sum()), "best 1X2 weight": round(w12[season], 2),
                     "O/U matches": int(ok2.sum()), "best O/U weight": round(wou[season], 2)})
    print(pd.DataFrame(rows).to_string(index=False))
    ok = ~np.isnan(fb12).any(1)
    w_all = fit_w(pm12[ok], fb12[ok], y12[ok])
    ok2 = ~np.isnan(fbou).any(1) & ~np.isnan(pmou).any(1)
    w_ou_all = fit_w(pmou[ok2], fbou[ok2], you[ok2]) if ok2.sum() > 50 else 0.0
    print(f"Both seasons together: 1X2 weight {w_all:.2f}, O/U weight {w_ou_all:.2f} "
          f"(your current setting is 0.50 for both)")

    print(f"\n3. VALUE flag replayed (edge of {FLAG_EDGE:.0%} or more on Bet365 pre-match prices)")
    print("   exp. ROI = Bet365 price x Pinnacle closing fair chance - 1: the closing-line test, far less noisy than results")
    res = []
    for w in WEIGHTS:
        for name, pm, fb, fc, odds, yy in [("1X2", pm12, fb12, fc12, open12, y12),
                                           ("O/U", pmou, fbou, fcou, open_ou, you)]:
            okr = ~(np.isnan(pm).any(1) | np.isnan(fb).any(1) | np.isnan(fc).any(1))
            bl = w * pm[okr] + (1 - w) * fb[okr]
            edge = bl * odds[okr] - 1
            flag = edge >= FLAG_EDGE
            n = int(flag.sum())
            if n == 0:
                res.append({"weight": w, "market": name, "flagged": 0})
                continue
            exp_roi = (odds[okr] * fc[okr] - 1)[flag]
            won = (np.arange(pm.shape[1])[None, :] == yy[okr][:, None])[flag]
            profit = np.where(won, odds[okr][flag] - 1, -1).sum()
            se = exp_roi.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
            res.append({"weight": w, "market": name, "flagged": n,
                        "exp ROI vs close %": round(100 * exp_roi.mean(), 2),
                        "t-stat": round(exp_roi.mean() / se, 2) if se and se > 0 else np.nan,
                        "beat close %": round(100 * (odds[okr][flag] > 1 / fc[okr][flag]).mean(), 1),
                        "actual ROI %": round(100 * profit / n, 1)})
    print(pd.DataFrame(res).to_string(index=False))

    w12_cfg = round(round(w_all / 0.05) * 0.05, 2)
    wou_cfg = round(round(w_ou_all / 0.05) * 0.05, 2)
    print("\nSet these in weekly.py CONFIG (Upgrade 20):")
    print(f'  MARGIN_METHOD = "{method}"')
    print(f"  VALUE_MODEL_WEIGHT = {w12_cfg}        # 1X2")
    print(f"  VALUE_MODEL_WEIGHT_OU = {wou_cfg}     # over/under 2.5")
    print(f'  VALUE_MODEL_SOURCE = "{model}"')
    print("If part 3 shows a negative expected ROI at every weight, the model has no edge over the "
          "closing line on these markets yet: keep stakes at zero or token size.")


if __name__ == "__main__":
    main()
