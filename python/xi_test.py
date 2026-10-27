"""
Upgrade 26: the perfect-XI test. If you had known each team's actual starting XI before kick-off,
would the match predictions have been better? If not, a predicted-XI model can't help either.

    python xi_test.py

For every match of 2024-25 and 2025-26:
  - the main model's expected goals from the run just before it (match_lambdas, as in Upgrade 23)
  - each team's expected XI before the gameweek (player_minutes / player_rates backtest: chance to
    start, attacking rate per 90) and the XI that actually started (pl_player_matches)

Two numbers per team and match:
  attack factor     = sum over actual starters of (non-penalty xG + xA per 90)
                      / sum over the squad of (chance to start x the same rate)
                      below 1 = weaker attackers started than expected
  selection factor  = sum of the starters' chances to start / sum of the 11 highest chances
                      below 1 = the manager rotated or regulars were missing (covers defence and keeper)

Adjusted expected goals:
  home = model home x (home attack factor)^b1 x (home selection)^b2 x (away selection)^-b3
  (and the same for the away team). b1, b2, b3 are fitted on one season and tested on the other.

The result: out-of-sample RPS with and without the line-up information, a 95% interval from a
bootstrap by match, and how much of the gap to the bookmakers it would close.
"""
import re

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

import quant_tools as qt

MAXG = 10
N_BOOT = 5000
WORTH_IT = 0.002          # RPS gain worth building expected-XI factors for (Upgrade 31)


def gw_of(label):
    m = re.search(r"GW(\d+)", str(label))
    return int(m.group(1)) if m else -1


def load(eng):
    lam = qt.q("""SELECT l.run_label, l.match_id, l.lambda_home, l.lambda_away, m.round_no,
                         m.home_team_id, m.away_team_id, m.home_goals, m.away_goals, s.label AS season
                  FROM match_lambdas l JOIN matches m USING (match_id) JOIN seasons s USING (season_id)
                  WHERE m.home_goals IS NOT NULL AND s.label = ANY(:s)""", {"s": qt.TEST_SEASONS}, eng=eng)
    lam["gw"] = lam["run_label"].map(gw_of)
    lam = lam[lam["gw"] < lam["round_no"]].sort_values("gw").drop_duplicates("match_id", keep="last")

    rates = qt.q("""SELECT * FROM player_rates
                    WHERE mode = 'backtest' AND season = ANY(:s)""", {"s": qt.TEST_SEASONS}, eng=eng)
    # each match is predicted by the run made after the gameweek before it
    rates["gw"] = rates["run_label"].map(gw_of)
    rates = rates.merge(lam[["match_id", "round_no"]], on="match_id")
    rates = rates[rates["gw"] == rates["round_no"] - 1]
    xg = "xg_nopen_90" if "xg_nopen_90" in rates.columns else "xg_90"
    rates["q"] = (pd.to_numeric(rates[xg], errors="coerce").fillna(0)
                  + pd.to_numeric(rates["xa_90"], errors="coerce").fillna(0))
    rates["p_start"] = pd.to_numeric(rates["p_start"], errors="coerce").fillna(0)

    act = qt.q("""SELECT match_id, team_id, player_code, started FROM v_player_match_ids
                  WHERE season = ANY(:s) AND minutes > 0""", {"s": qt.TEST_SEASONS}, eng=eng)
    act["started"] = act["started"].astype(str).str.lower().isin(["true", "t", "1"])
    return lam, rates, act[act["started"]]


def factors(lam, rates, act):
    """Attack and selection factor for every team in every match."""
    rows, unknown = [], 0
    starters = act.groupby(["match_id", "team_id"])["player_code"].apply(set).to_dict()
    for (mid, tid), g in rates.groupby(["match_id", "team_id"]):
        st = starters.get((mid, tid))
        if not st or len(st) < 9:
            continue                              # line-up missing or incomplete in the data
        q = dict(zip(g["player_code"], g["q"]))
        p = dict(zip(g["player_code"], g["p_start"]))
        qfill = float(np.median(g["q"])) if len(g) else 0.0
        miss = [c for c in st if c not in q]      # started but not in the predicted squad (new signing)
        unknown += len(miss)
        scale = 11 / g["p_start"].sum() if g["p_start"].sum() > 0 else 1.0   # expected starters = 11
        exp_att = float((g["p_start"] * g["q"]).sum() * scale)
        act_att = sum(q.get(c, qfill) for c in st)
        top11 = float(np.sort(g["p_start"].to_numpy())[::-1][:11].sum())
        act_sel = sum(p.get(c, 0.0) for c in st)
        rows.append({"match_id": mid, "team_id": tid,
                     "att": np.clip(act_att / exp_att, 0.5, 1.5) if exp_att > 0 else 1.0,
                     "sel": np.clip(act_sel / top11, 0.3, 1.0) if top11 > 0 else 1.0})
    f = pd.DataFrame(rows)
    d = lam.merge(f.rename(columns={"team_id": "home_team_id", "att": "att_h", "sel": "sel_h"}),
                  on=["match_id", "home_team_id"])
    d = d.merge(f.rename(columns={"team_id": "away_team_id", "att": "att_a", "sel": "sel_a"}),
                on=["match_id", "away_team_id"])
    return d, unknown


def adjusted(d, b):
    b1, b2, b3 = b
    lh = d["lambda_home"].to_numpy(float) * d["att_h"] ** b1 * d["sel_h"] ** b2 * d["sel_a"] ** -b3
    la = d["lambda_away"].to_numpy(float) * d["att_a"] ** b1 * d["sel_a"] ** b2 * d["sel_h"] ** -b3
    return np.asarray(lh, float), np.asarray(la, float)


def nll(b, d):
    lh, la = adjusted(d, b)
    return -(poisson.logpmf(d["home_goals"], lh) + poisson.logpmf(d["away_goals"], la)).sum()


def probs(lh, la):
    g = np.arange(MAXG + 1)
    ph, pa = poisson.pmf(g[None, :], lh[:, None]), poisson.pmf(g[None, :], la[:, None])
    j = ph[:, :, None] * pa[:, None, :]
    home = np.tril(np.ones((MAXG + 1, MAXG + 1)), -1).astype(bool)
    p = np.stack([j[:, home].sum(1), j[:, np.eye(MAXG + 1, dtype=bool)].sum(1), j[:, home.T].sum(1)], 1)
    return p / p.sum(1, keepdims=True)


def boot(diff, clusters, rng):
    g = pd.DataFrame({"d": diff, "c": clusters}).groupby("c")["d"].mean().to_numpy()
    idx = rng.integers(0, len(g), size=(N_BOOT, len(g)))
    return g.mean(), *np.percentile(g[idx].mean(1), [2.5, 97.5])


def main():
    eng = qt.engine()
    lam, rates, act = load(eng)
    if rates.empty:
        raise SystemExit("No player_rates backtest rows. Run: python player_minutes.py backtest, then "
                         "python player_rates.py backtest")
    d, unknown = factors(lam, rates, act)
    d["y"] = qt.result_index(d["home_goals"], d["away_goals"])
    print(f"{len(d)} matches with both line-ups ({len(lam)} with model expected goals); "
          f"{unknown} starters weren't in the predicted squads (given the squad's median rate)")
    print("Factor spread: attack " + ", ".join(f"{q:.2f}" for q in np.percentile(np.r_[d.att_h, d.att_a], [5, 50, 95]))
          + " | selection " + ", ".join(f"{q:.2f}" for q in np.percentile(np.r_[d.sel_h, d.sel_a], [5, 50, 95]))
          + "  (5th, 50th, 95th percentile)")

    book = qt.q("""SELECT match_id, p_home, p_draw, p_away FROM v_scored_predictions
                   WHERE model = 'bookmaker' AND season = ANY(:s)""", {"s": qt.TEST_SEASONS}, eng=eng)
    book = book.drop_duplicates("match_id").set_index("match_id")
    rng = np.random.default_rng(2026)
    rows, all_diff, all_m = [], [], []
    for train, test in [(qt.TEST_SEASONS[0], qt.TEST_SEASONS[1]), (qt.TEST_SEASONS[1], qt.TEST_SEASONS[0])]:
        tr, te = d[d["season"] == train], d[d["season"] == test]
        b = minimize(nll, x0=[0.0, 0.0, 0.0], args=(tr,), method="Nelder-Mead").x
        p0 = probs(te["lambda_home"].to_numpy(float), te["lambda_away"].to_numpy(float))
        p1 = probs(*adjusted(te, b))
        r0, r1 = qt.rps(p0, te["y"]), qt.rps(p1, te["y"])
        est, lo, hi = boot(r1 - r0, te["match_id"].to_numpy(), rng)
        bk = book.reindex(te["match_id"])
        ok = bk.notna().all(axis=1).to_numpy()
        rb = qt.rps(bk.to_numpy(float)[ok], te["y"].to_numpy()[ok]).mean() if ok.any() else np.nan
        rows.append({"fit on": train, "test on": test, "matches": len(te),
                     "b1 attack": round(b[0], 2), "b2 own selection": round(b[1], 2), "b3 opp selection": round(b[2], 2),
                     "RPS model": round(r0.mean(), 4), "RPS with XI": round(r1.mean(), 4), "change": round(est, 4),
                     "95% interval": f"{lo:+.4f} to {hi:+.4f}",
                     "log loss change": round((qt.logloss(p1, te["y"]) - qt.logloss(p0, te["y"])).mean(), 4),
                     "RPS bookmakers": round(rb, 4),
                     "gap closed %": round(100 * (r0.mean() - r1.mean()) / (r0.mean() - rb), 1) if rb < r0.mean() else np.nan})
        all_diff.append(r1 - r0)
        all_m.append(te["match_id"].to_numpy())
    pd.set_option("display.width", 250)
    print("\nOut of sample (negative change = knowing the XI helps). b = how strongly each factor moves expected goals.")
    print(pd.DataFrame(rows).to_string(index=False))
    est, lo, hi = boot(np.concatenate(all_diff), np.concatenate(all_m), rng)
    print(f"\nBoth seasons together: RPS change {est:+.4f} (95% interval {lo:+.4f} to {hi:+.4f})")

    print("\nDecision")
    both = all(r["change"] < 0 for r in rows)
    if hi < 0 and both and -est >= WORTH_IT:
        print(f"- PASS: knowing the XI improves RPS by {-est:.4f} (at least {WORTH_IT}) in both seasons. "
              "Line-ups are real information: build Upgrade 31 (expected-XI factors, re-price after team news).")
    elif hi < 0 and both:
        print(f"- SMALL: a real but small gain ({-est:.4f}). Worth a light version only: re-price on team news "
              "with these b values, no full expected-XI model.")
    else:
        print("- FAIL: knowing the actual XI doesn't reliably help this model. Skip the line-up work "
              "(Upgrade 31); the bookmakers' edge is somewhere else.")
    print("Note: this is the best case (the XI known for certain). A predicted XI before team news gets part of it.")


if __name__ == "__main__":
    main()
