"""
Upgrade 23: does the model get the SHAPE of scores right (0-0s, 1-1s, draws, blowouts)?

    python scoreline_check.py

For every match of 2024-25 and 2025-26 it takes the main model's expected goals from the run just
before the match (match_lambdas, the same numbers your player and value markets use), works out
the chance of every scoreline (Poisson), and compares predicted and actual counts.

  z = (actual - predicted) / standard error. Beyond about ±2 is a real gap, not luck.
  Also: total-goals distribution (chi-square test), and the Dixon-Coles low-score correction
  (rho) that best fits these matches, with the improvement it would give.

Decision guide (printed at the end):
  0-0 and 1-1 under-predicted and draws too few -> add the low-score correction (rho) to the
  Poisson markets (value log over/under, correct-score and player goal markets).
  Big wins (3+ margin) over-predicted -> cap goal difference in the ratings or lean more on xG.
"""
import re

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import chisquare, poisson

import quant_tools as qt

MAXG = 10


def grids(lh, la, rho=0.0):
    """(n, 11, 11) scoreline chances; rho is the Dixon-Coles correction for 0-0, 1-0, 0-1, 1-1."""
    g = np.arange(MAXG + 1)
    ph = poisson.pmf(g[None, :], lh[:, None])
    pa = poisson.pmf(g[None, :], la[:, None])
    p = ph[:, :, None] * pa[:, None, :]
    if rho:
        p[:, 0, 0] *= 1 - lh * la * rho
        p[:, 0, 1] *= 1 + lh * rho
        p[:, 1, 0] *= 1 + la * rho
        p[:, 1, 1] *= 1 - rho
        p = np.clip(p, 0, None)
    return p / p.sum((1, 2), keepdims=True)


def main():
    eng = qt.engine()
    d = qt.q("""SELECT l.run_label, l.match_id, l.lambda_home, l.lambda_away, m.round_no,
                       m.home_goals, m.away_goals, s.label AS season
                FROM match_lambdas l JOIN matches m USING (match_id) JOIN seasons s USING (season_id)
                WHERE m.home_goals IS NOT NULL AND s.label = ANY(:s)""", {"s": qt.TEST_SEASONS}, eng=eng)
    d["gw"] = d["run_label"].map(lambda s: int(re.search(r"GW(\d+)", s).group(1)) if "GW" in s else -1)
    d = d[d["gw"] < d["round_no"]].sort_values("gw").drop_duplicates("match_id", keep="last")
    lh, la = d["lambda_home"].astype(float).to_numpy(), d["lambda_away"].astype(float).to_numpy()
    hg, ag = d["home_goals"].astype(int).to_numpy(), d["away_goals"].astype(int).to_numpy()
    n = len(d)
    print(f"{n} matches (2024-25 and 2025-26), main model, run just before each match")

    P = grids(lh, la)
    H, A = np.meshgrid(np.arange(MAXG + 1), np.arange(MAXG + 1), indexing="ij")
    events = {
        "0-0": (H == 0) & (A == 0), "1-0": (H == 1) & (A == 0), "0-1": (H == 0) & (A == 1),
        "1-1": (H == 1) & (A == 1), "2-1": (H == 2) & (A == 1), "1-2": (H == 1) & (A == 2),
        "2-2": (H == 2) & (A == 2), "any draw": H == A, "home win by 3+": H - A >= 3,
        "away win by 3+": A - H >= 3, "0-1 goals in total": H + A <= 1, "4+ goals": H + A >= 4,
        "5+ goals": H + A >= 5, "both teams score": (H > 0) & (A > 0),
    }
    rows = []
    for name, mask in events.items():
        p = (P * mask).sum((1, 2))
        hit = mask[np.minimum(hg, MAXG), np.minimum(ag, MAXG)]
        exp, act = p.sum(), hit.sum()
        se = np.sqrt((p * (1 - p)).sum())
        rows.append({"scoreline / event": name, "predicted": round(exp, 1), "actual": int(act),
                     "actual / predicted": round(act / exp, 2), "z": round((act - exp) / se, 2),
                     "flag": "CHECK" if abs((act - exp) / se) >= 2 else ""})
    print(pd.DataFrame(rows).to_string(index=False))

    tot = H + A
    bins = [0, 1, 2, 3, 4, 5]
    exp_t = [(P * (tot == b)).sum() for b in bins] + [(P * (tot >= 6)).sum()]
    act_t = [int(((hg + ag) == b).sum()) for b in bins] + [int(((hg + ag) >= 6).sum())]
    chi = chisquare(act_t, np.array(exp_t) * sum(act_t) / sum(exp_t))
    print("\nTotal goals: " + "  ".join(f"{b}: {a} vs {e:.1f}" for b, a, e in
                                        zip([*map(str, bins), "6+"], act_t, exp_t)))
    print(f"Chi-square p = {chi.pvalue:.3f} (below 0.05 = the total-goals shape is off)")
    var_pred = (P * tot ** 2).sum() / n - ((P * tot).sum() / n) ** 2
    print(f"Spread of total goals: predicted variance {var_pred:.2f}, actual {np.var(hg + ag):.2f}")

    def nll(rho):
        g = grids(lh, la, rho)
        return -np.log(g[np.arange(n), np.minimum(hg, MAXG), np.minimum(ag, MAXG)] + 1e-12).sum()
    res = minimize_scalar(nll, bounds=(-0.25, 0.25), method="bounded")
    gain = (nll(0.0) - res.fun) / n
    print(f"\nBest low-score correction for these matches: rho = {res.x:.3f} "
          f"(log loss better by {gain:.4f} per match; your dc_model.py fits its own rho)")

    z = {r["scoreline / event"]: r["z"] for r in rows}
    print("\nReading it:")
    if z["0-0"] >= 2 or z["1-1"] >= 2 or z["any draw"] >= 2:
        print(f"- Low scores / draws are under-predicted: use rho = {res.x:.2f} in the Poisson markets (Upgrade 23 step 4).")
    elif res.x < -0.03 and gain > 0.0005:
        print(f"- A low-score correction still helps a little (rho {res.x:.2f}): optional, see step 4.")
    else:
        print("- Low scores and draws look right: no correction needed.")
    if z["home win by 3+"] <= -2 or z["away win by 3+"] <= -2:
        print("- Big wins are over-predicted: cap goal difference in the ratings or lean more on xG.")
    elif z["home win by 3+"] >= 2 or z["away win by 3+"] >= 2:
        print("- Big wins are under-predicted: the model is too flat for mismatches (see Upgrade 19).")
    if chi.pvalue < 0.05:
        wider = np.var(hg + ag) > var_pred
        print("- The total-goals shape is off: actual totals are " +
              ("MORE spread out than predicted (more 0-0s and 5+ goal games than Poisson allows)." if wider else
               "LESS spread out than predicted (more 2-3 goal games: the model's goal expectations vary too much "
               "between matches, often a sign of over-sharp ratings).") )


if __name__ == "__main__":
    main()
