"""
Upgrades 15.5 and 15.6: player markets for every player in every fixture.
  15.5 attacking:  to score (1+, 2+, 3+), to assist, goal or assist, shots (1+, 2+, 3+),
                   shots on target (1+, 2+, 3+)
  15.6 defensive:  fouls committed (1+, 2+, 3+), fouled (1+, 2+, 3+), tackles (1+, 2+, 3+),
                   to be carded (yellow or red), red card, keeper saves (1+ to 5+)

Put this file in the DC upgrade folder (next to player_rates.py and team_counts.py) and run:

  python player_markets.py backtest   every gameweek that has player_minutes, player_rates and
                                      team_counts backtest rows, with what actually happened
  python player_markets.py next       the next gameweek (after the weekly 'next' runs of
                                      player_minutes, player_rates and team_counts)

Needs match_lambdas (block 15.5a in 29_upgrade15_data.sql): each team's expected goals from your
main model, matched to your ensemble's win/draw/loss chances where it has them.

How each player's chance is built, for his team in that fixture:
  team totals       expected goals (your model) x 0.97 (about 3% of goals are own goals),
                    expected assists = expected goals x the league's assists per goal,
                    expected shots and shots on target from team_counts (15.4)
                    fouls, tackles, yellows and reds: the team's own expected count (15.4,
                    including the referee for fouls); fouled: the opponent's expected fouls
                    keeper saves: opponent's expected shots on target minus their expected goals
  his share         his rate per 90 (15.3) x his expected minutes (15.2), as a share of the
                    team's total, so the team's players add up to the team's expected total.
                    How much of that team/opponent scaling to use (ALPHA, 0 to 1) is chosen per
                    stat on 2024-25 and checked on 2025-26, like 15.2-15.4
  his chance        Poisson, mixing "he starts" and "he comes on" (15.2), and given that he
                    plays (bookmakers void player bets when the player doesn't play)
  calibration       per market, fitted on 2024-25 and kept only if it also helps 2025-26:
                    negative binomial spread (r), level (c) and a pull of extreme players
                    towards the middle (g below 1)
Two comparisons are stored for the test:
  baseline          his raw rate this season x his usual minutes (no team or opponent)
  rates only        his 15.3 rate x his usual minutes (no team or opponent scaling)
"""
import os
import sys
import json

import numpy as np
import pandas as pd
from scipy.stats import poisson, nbinom
from scipy.optimize import minimize
from scipy.optimize import least_squares

import player_minutes as pmin
from player_minutes import to_numbers

OWN_GOAL_SHARE = 0.03
SETTINGS_FILE = "player_markets_settings.json"
CAL_FILE = "player_markets_calibration.json"
SCALE_FILE = "prop_scaling.json"      # Upgrade 24 (prop_audit.py): Opta / your-data count ratios
NO_CAL = {"red"}       # too rare to fit a calibration
ALPHAS = [0.0, 0.25, 0.5, 0.75, 1.0]   # how much of the team/opponent scaling to use (1 = all)
# stat: (rate column from 15.3, line used to choose ALPHA)
SCALED = {"goals": "xg_90", "assists": "xa_90", "shots": "shots_90", "sot": "sot_90",
          "fouls": "fouls_90", "fouled": "fouled_90", "tackles": "tackles_90",
          "yellow": "yellow_90", "red": "red_90"}
# market: (intensity parts, actual parts, lines)
MARKETS = {
    "goals":          (["goals"], ["goals"], [1, 2, 3]),
    "assists":        (["assists"], ["assists"], [1]),
    "goal_or_assist": (["goals", "assists"], ["goals", "assists"], [1]),
    "shots":          (["shots"], ["shots"], [1, 2, 3]),
    "sot":            (["sot"], ["sot"], [1, 2, 3]),
    "fouls":          (["fouls"], ["fouls"], [1, 2, 3]),
    "fouled":         (["fouled"], ["fouled"], [1, 2, 3]),
    "tackles":        (["tackles"], ["tackles"], [1, 2, 3]),
    "carded":         (["yellow", "red"], ["yellow", "red"], [1]),
    "red":            (["red"], ["red"], [1]),
    "saves":          (["saves"], ["saves"], [1, 2, 3, 4, 5]),
}
COUNT_STATS = ["shots", "sot", "fouls", "tackles", "yellows", "reds"]
MIN_SAVES = 0.3


# ------------------------------------------------------------------ data
def load(eng, mode):
    test = os.environ.get("TEST_DIR")
    if test:
        rd = lambda p: pd.read_csv(p, low_memory=False)
        mins = rd(os.path.join("player_data", "player_minutes.csv"))
        rates = rd(os.path.join("player_data", "player_rates.csv"))
        counts = rd(os.path.join("player_data", "team_counts.csv"))
        lam = rd(os.path.join(test, "fixtures.csv"))[["run_label", "match_id", "lambda_home", "lambda_away",
                                                      "p_home", "p_draw", "p_away"]]
        pm = rd(os.path.join(test, "pl_player_matches.csv"))
    else:
        q = lambda s: to_numbers(pd.read_sql(s, eng))
        mins = q(f"SELECT * FROM player_minutes WHERE mode = '{mode}'")
        rates = q(f"SELECT * FROM player_rates WHERE mode = '{mode}'")
        counts = q(f"""SELECT run_label, match_id, stat, side, expected FROM team_counts
                       WHERE mode = '{mode}' AND side IN ('home', 'away')""")
        lam = q("""SELECT l.run_label, l.match_id, l.lambda_home, l.lambda_away, e.p_home, e.p_draw, e.p_away
                   FROM match_lambdas l
                   LEFT JOIN external_predictions e ON e.source = 'ens_dcxg_50'
                        AND e.run_label = l.run_label AND e.match_id = l.match_id""")
        pm = q("SELECT season, gameweek, goals, assists FROM pl_player_matches")
    mins = mins[mins["mode"] == mode]
    rates = rates[rates["mode"] == mode]
    if "mode" in counts.columns:
        counts = counts[counts["mode"] == mode]
    counts = counts[counts["stat"].isin(COUNT_STATS) & counts["side"].isin(["home", "away"])]
    return mins, rates, counts, lam, pm


# ------------------------------------------------------------------ maths
def p_at_least(k, lam):
    """P(X >= k) for Poisson(lam), element-wise."""
    return 1 - poisson.cdf(k - 1, lam)


def calibrate(lh, la, ph, pa):
    """Adjust expected goals so a Poisson model gives your ensemble's home and away win chances."""
    if not (np.isfinite(ph) and np.isfinite(pa)) or lh <= 0 or la <= 0:
        return lh, la
    g = np.arange(11)

    def probs(x):
        a, b = np.exp(x)
        m = np.outer(poisson.pmf(g, a), poisson.pmf(g, b))
        return np.tril(m, -1).sum(), np.triu(m, 1).sum()

    def resid(x):
        h, w = probs(x)
        return [h - ph, w - pa, 0.05 * (x[0] - np.log(lh)), 0.05 * (x[1] - np.log(la))]

    r = least_squares(resid, np.log([lh, la]))
    nh, na = np.exp(r.x)
    return (nh, na) if r.success and 0.2 < nh < 5 and 0.2 < na < 5 else (lh, la)


def mixture(k, intensity_90, p_start, p_sub, m_start, m_sub):
    """Chance of k or more given he plays: start and sub cases mixed."""
    play = p_start + p_sub
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (p_start * p_at_least(k, intensity_90 * m_start / 90) +
               p_sub * p_at_least(k, intensity_90 * m_sub / 90)) / play
    return np.where(play > 0, out, np.nan)


def mixture_cal(k, intensity_90, p_start, p_sub, m_start, m_sub, cal, scale=1.0):
    """Like mixture(), after calibration: his expected count if he plays, m, becomes c x m^g
    (g below 1 pulls extreme players back towards the middle), and counts are negative binomial
    with dispersion r (players have good and bad days beyond Poisson luck).
    scale (Upgrade 24): multiplies the calibrated count, to move it onto the bookmakers' (Opta) scale.
    Returns (chance of k or more given he plays, calibrated expected count if he plays)."""
    r, c, g = cal
    play = p_start + p_sub
    with np.errstate(invalid="ignore", divide="ignore"):
        ls, lb = intensity_90 * m_start / 90, intensity_90 * m_sub / 90
        m = (p_start * ls + p_sub * lb) / play
        f = np.where(m > 0, c * np.power(np.maximum(m, 1e-9), g - 1), 1.0)
        ls, lb = ls * f * scale, lb * f * scale
        tail = lambda lam: 1 - nbinom.cdf(k - 1, r, r / (r + lam))
        out = (p_start * tail(ls) + p_sub * tail(lb)) / play
    return np.where(play > 0, out, np.nan), m * f * scale


def fit_calibration(market, lines, i_model, dd, actual):
    """Fit (r, c, g) by Brier over all lines on 2024-25 players who played; report 2025-26."""
    played = (dd["act_minutes"].fillna(0) > 0).values & actual.notna().values & np.isfinite(i_model.values)
    seasons = dd["season"].values
    cols = [dd[c].values for c in ("p_start", "p_sub", "min_start", "min_sub")]

    def brier(cal, season):
        m = played & (seasons == season)
        tot, n = 0.0, 0
        for k in lines:
            p, _ = mixture_cal(k, i_model.values[m], *[c[m] for c in cols], cal)
            y = (actual.values[m] >= k).astype(float)
            tot += np.nansum((p - y) ** 2); n += np.isfinite(p).sum()
        return tot / max(n, 1)

    obj = lambda v: brier((np.exp(v[0]), np.exp(v[1]), v[2]), "2024-25")
    res = minimize(obj, [np.log(10.0), 0.0, 1.0], method="Nelder-Mead",
                   options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 300})
    cal = [float(min(np.exp(res.x[0]), 1e4)), float(np.exp(res.x[1])), float(res.x[2])]
    none = [1e6, 1.0, 1.0]
    b = {(s, n): brier(x, s) for s in ("2024-25", "2025-26") for n, x in (("off", none), ("on", cal))}
    keep = b[("2025-26", "on")] < b[("2025-26", "off")]      # must also help the season it wasn't fitted on
    print(f"  {market:14s} r {cal[0]:7.1f}  c {cal[1]:.2f}  g {cal[2]:.2f}   Brier (all lines) 2024-25 "
          f"{b[('2024-25', 'off')]:.4f} -> {b[('2024-25', 'on')]:.4f} | 2025-26 "
          f"{b[('2025-26', 'off')]:.4f} -> {b[('2025-26', 'on')]:.4f}   {'kept' if keep else 'not kept'}")
    return cal if keep else none


# ------------------------------------------------------------------ build
def scaling(d, stat, a):
    """League level in full, times a share (a) of this team's and opponent's difference from it.
    Rescaled per run so all players' expected totals add up to the league's expected total."""
    L = d["L_" + stat]
    raw = L * (d["k_" + stat] / L) ** a
    got = (d["w_" + stat] * raw).groupby(d["run_label"]).transform("sum")
    want = d["T_" + stat]
    return raw * np.where(got > 0, want / got, 1.0)


def choose_alpha(d):
    """Pick ALPHA per stat by Brier of '1 or more' on 2024-25 players who played; report 2025-26."""
    is_played = d["act_minutes"].fillna(0) > 0
    played = d[is_played]
    chosen = {}
    print("Choosing how much team/opponent scaling to use (0 = league level only, 1 = full), on 2024-25:")
    for stat, rate in SCALED.items():
        act = played["act_" + stat]
        ok = act.notna()
        res = {}
        for a in ALPHAS:
            i = played[rate].clip(lower=0) * scaling(d, stat, a)[is_played.values]
            p = mixture(1, i.values, played["p_start"].values, played["p_sub"].values,
                        played["min_start"].values, played["min_sub"].values)
            b = (p - (act >= 1).astype(float)) ** 2
            res[a] = {s: float(np.nanmean(b[ok & (played["season"] == s)])) for s in ("2024-25", "2025-26")}
        best = min(ALPHAS, key=lambda a: res[a]["2024-25"])
        chosen[stat] = best
        print(f"  {stat:8s} {best:.2f}   2024-25 Brier {res[best]['2024-25']:.4f} (league only {res[0.0]['2024-25']:.4f}, full {res[1.0]['2024-25']:.4f})"
              f" | 2025-26 {res[best]['2025-26']:.4f} (league only {res[0.0]['2025-26']:.4f}, full {res[1.0]['2025-26']:.4f})")
    return chosen


def choose_alpha_saves(d):
    """Keepers: how much of the opponent's expected shots on target to use (0 = league average),
    by Brier of '3 or more saves' on 2024-25 keepers who played."""
    k = d[(d["position"] == "Goalkeeper") & (d["act_minutes"].fillna(0) > 0) & d["act_saves"].notna()]
    res = {}
    for a in ALPHAS:
        i = k["base_saves"] * (k["team_saves"] / k["base_saves"]) ** a
        p = mixture(3, i.values, k["p_start"].values, k["p_sub"].values, k["min_start"].values, k["min_sub"].values)
        b = (p - (k["act_saves"] >= 3).astype(float)) ** 2
        res[a] = {s: float(np.nanmean(b[(k["season"] == s).values])) for s in ("2024-25", "2025-26")}
    best = min(ALPHAS, key=lambda a: res[a]["2024-25"])
    print(f"  saves    {best:.2f}   2024-25 Brier {res[best]['2024-25']:.4f} (league only {res[0.0]['2024-25']:.4f}, full {res[1.0]['2024-25']:.4f})"
          f" | 2025-26 {res[best]['2025-26']:.4f} (league only {res[0.0]['2025-26']:.4f}, full {res[1.0]['2025-26']:.4f})  [3+ saves]")
    return best


APG_K = 150          # goals of weight given to earlier seasons' assists-per-goal
APG_DEFAULT = 0.75


def assists_per_goal_by_run(pm, run_labels):
    """Assists per goal for each run, from that season's games up to the run, shrunk towards
    earlier seasons. The ratio moves between seasons (2024-25 about 0.74, 2025-26 about 0.94,
    as the data source's assist rules changed), so one pooled number misprices assists."""
    pm = pm.dropna(subset=["goals", "assists"])
    out = {}
    for rl in run_labels:
        season, gw = rl[:7], int(rl.split("GW")[-1])
        prev = pm[pm["season"] < season]
        prior = prev["assists"].sum() / prev["goals"].sum() if prev["goals"].sum() > 0 else APG_DEFAULT
        cur = pm[(pm["season"] == season) & (pm["gameweek"] <= gw)]
        out[rl] = (cur["assists"].sum() + APG_K * prior) / (cur["goals"].sum() + APG_K)
    return pd.Series(out).sort_index(key=lambda i: i.str[:7] + i.str.split("GW").str[-1].str.zfill(2))


SAVES_K = 100        # team-games of weight given to earlier seasons' saves per game
SAVES_DEFAULT = 3.0


def saves_level_by_run(pm, run_labels):
    """Saves per team-game for each run, from that season's games up to the run, shrunk towards
    earlier seasons (no later games used)."""
    pm = pm[pm["saves"].notna()]
    per = pm.groupby(["season", "gameweek", "match_id", "team_id"], as_index=False)["saves"].sum()
    out = {}
    for rl in run_labels:
        season, gw = rl[:7], int(rl.split("GW")[-1])
        prev = per[per["season"] < season]
        prior = prev["saves"].mean() if len(prev) else SAVES_DEFAULT
        cur = per[(per["season"] == season) & (per["gameweek"] <= gw)]
        out[rl] = (cur["saves"].sum() + SAVES_K * prior) / (len(cur) + SAVES_K)
    return pd.Series(out)


def build(mode):
    use_db = not os.environ.get("TEST_DIR")
    eng = pmin.engine() if use_db else None
    mins, rates, counts, lam, pm = load(eng, mode)
    if len(mins) == 0 or len(rates) == 0:
        print(f"No '{mode}' rows in player_minutes / player_rates. Run those first.")
        return None, eng
    apg = assists_per_goal_by_run(pm, mins["run_label"].unique())
    print("League assists per goal, as known before each run (first and last run of each season):")
    for season, grp in apg.groupby(apg.index.str[:7]):
        print(f"  {season}: {grp.iloc[0]:.3f} -> {grp.iloc[-1]:.3f}")

    # one row per player-fixture with minutes (15.2) and rates (15.3)
    acts = ["act_minutes"] + ["act_" + s for s in SCALED]
    keep_r = (["run_label", "match_id", "player_code"] + list(SCALED.values()) +
              ["base_" + r for r in SCALED.values()] + acts)
    keep_r = [c for c in keep_r if c in rates.columns]
    d = mins.merge(rates[keep_r], on=["run_label", "match_id", "player_code"], how="inner")
    for c in acts:
        if c not in d:
            d[c] = np.nan

    # keeper saves that actually happened (not in 15.3's rates)
    pm_all, _, fixtures, _ = pmin.load(eng)
    sv = pm_all[pm_all["match_id"].notna()][["match_id", "player_code", "saves"]].copy()
    sv["match_id"] = sv["match_id"].astype(int)
    sv = sv.groupby(["match_id", "player_code"], as_index=False)["saves"].sum()
    d = d.merge(sv.rename(columns={"saves": "act_saves"}), on=["match_id", "player_code"], how="left")
    d.loc[d["act_minutes"].isna(), "act_saves"] = np.nan

    # team expected goals per fixture (calibrated to your ensemble where it exists)
    lam = lam.drop_duplicates(["run_label", "match_id"])
    calib = [calibrate(r.lambda_home, r.lambda_away, r.p_home, r.p_away) for r in lam.itertuples(index=False)]
    lam["xg_home"] = [c[0] for c in calib]
    lam["xg_away"] = [c[1] for c in calib]
    ens = lam["p_home"].notna().sum()
    print(f"Fixtures with expected goals: {len(lam)} ({ens} matched to your ensemble's chances)")

    sides = mins.drop_duplicates(["run_label", "match_id", "team_id"])[["run_label", "match_id", "team_id"]]
    # which side is home: the team listed first in matches (via team_counts home/away we need ids)
    fx = fixtures[["match_id", "home_team_id", "away_team_id"]]
    sides = sides.merge(fx, on="match_id", how="left")
    sides["side"] = np.where(sides["team_id"] == sides["home_team_id"], "home", "away")
    sides = sides.merge(lam[["run_label", "match_id", "xg_home", "xg_away"]], on=["run_label", "match_id"], how="left")
    sides["team_xg"] = np.where(sides["side"] == "home", sides["xg_home"], sides["xg_away"])
    sides["opp_xg"] = np.where(sides["side"] == "home", sides["xg_away"], sides["xg_home"])
    cpiv = counts.pivot_table(index=["run_label", "match_id", "side"], columns="stat", values="expected").reset_index()
    for c in COUNT_STATS:
        if c not in cpiv:
            cpiv[c] = np.nan
    own = cpiv.rename(columns={c: "team_" + c for c in COUNT_STATS})
    opp = cpiv.rename(columns={c: "opp_" + c for c in COUNT_STATS})
    opp["side"] = opp["side"].map({"home": "away", "away": "home"})
    sides = sides.merge(own, on=["run_label", "match_id", "side"], how="left")
    sides = sides.merge(opp, on=["run_label", "match_id", "side"], how="left")
    missing = sides["team_xg"].isna().sum()
    if missing:
        print(f"  {missing} team-fixtures have no expected goals in match_lambdas (run block 15.5a) - skipped")
    sides = sides[sides["team_xg"].notna()]
    # keeper saves: the opponent's shots on target that aren't goals
    sides["team_saves"] = (sides["opp_sot"] - sides["opp_xg"] * (1 - OWN_GOAL_SHARE)).clip(lower=MIN_SAVES)
    # level: keepers' actual saves per team-game before each run (this season, shrunk to earlier
    # seasons); each fixture's expected saves keeps its shape but is set to that level
    level = saves_level_by_run(pm_all, sides["run_label"].unique())
    run_saves = sides.groupby("run_label")["team_saves"].transform("mean")
    sides["base_saves"] = sides["run_label"].map(level)
    sides["team_saves"] = sides["team_saves"] * sides["base_saves"] / run_saves
    keep_s = ["run_label", "match_id", "team_id", "side", "team_xg", "team_saves", "base_saves"] + \
             ["team_" + c for c in COUNT_STATS] + ["opp_fouls"]
    d = d.merge(sides[keep_s], on=["run_label", "match_id", "team_id"], how="inner")

    # scale each team's players to the team total
    d["m_play"] = np.where(d["p_play"] > 0, d["exp_minutes"] / d["p_play"], 0)   # minutes if he plays
    totals = {"goals": d["team_xg"] * (1 - OWN_GOAL_SHARE),
              "assists": d["team_xg"] * d["run_label"].map(apg),
              "shots": d["team_shots"], "sot": d["team_sot"],
              "fouls": d["team_fouls"], "fouled": d["opp_fouls"], "tackles": d["team_tackles"],
              "yellow": d["team_yellows"], "red": d["team_reds"]}
    for stat, rate in SCALED.items():
        total = totals[stat]
        d = d.copy() if stat == "goals" else d
        w = d[rate].clip(lower=0) * d["exp_minutes"] / 90
        tot_w = w.groupby([d["run_label"], d["match_id"], d["team_id"]]).transform("sum")
        d["k_" + stat] = np.where((tot_w > 0) & total.notna(), total / tot_w, np.nan)   # team/opponent scaling
        # league level for this run: all teams' totals / all players' rates (always applied in full,
        # so ALPHA only controls how much each team and opponent differ from that level)
        n_team = w.groupby([d["run_label"], d["match_id"], d["team_id"]]).transform("size")
        tot_share = pd.Series(np.asarray(total, dtype=float), index=d.index) / n_team   # each team counted once
        run_tot = tot_share.groupby(d["run_label"]).transform("sum")
        run_w = w.groupby(d["run_label"]).transform("sum")
        run_w = w.where(total.notna(), 0).groupby(d["run_label"]).transform("sum")
        d["L_" + stat] = np.where(run_w > 0, run_tot / run_w, 1.0)
        d["k_" + stat] = d["k_" + stat].fillna(d["L_" + stat])     # no team count: league level
        d["w_" + stat] = w
        d["T_" + stat] = run_tot

    # how much of the scaling to use, per stat (chosen on 2024-25 in the backtest)
    if mode == "backtest":
        alpha = choose_alpha(d)
        json.dump(alpha, open(SETTINGS_FILE, "w"), indent=1)
    else:
        alpha = json.load(open(SETTINGS_FILE)) if os.path.exists(SETTINGS_FILE) else {}
        if any(s not in alpha for s in SCALED):
            print("  (no saved scaling for some stats - run 'backtest' first; using full scaling for those)")
    d = d.copy()
    for stat, rate in SCALED.items():
        d["i_" + stat] = d[rate].clip(lower=0) * scaling(d, stat, alpha.get(stat, 1.0))
        d["r_" + stat] = d[rate].clip(lower=0)
        d["b_" + stat] = d["base_" + rate].clip(lower=0) if "base_" + rate in d else d["r_" + stat]
    # saves: keepers only, from the team's expected saves (no player rate)
    gk = (d["position"] == "Goalkeeper").astype(float)
    if mode == "backtest":
        alpha["saves"] = choose_alpha_saves(d)
        json.dump(alpha, open(SETTINGS_FILE, "w"), indent=1)
    d["i_saves"] = d["base_saves"] * (d["team_saves"] / d["base_saves"]) ** alpha.get("saves", 1.0) * gk
    d["r_saves"] = d["b_saves"] = d["base_saves"] * gk

    rows = []
    ps, pb, ms, mb = d["p_start"].values, d["p_sub"].values, d["min_start"].values, d["min_sub"].values
    if mode == "backtest":
        cals = {}
        print("Calibrating each market (r = spread, c = level, g = how far to pull extremes in; fitted on 2024-25):")
    else:
        cals = json.load(open(CAL_FILE)) if os.path.exists(CAL_FILE) else {}
    # Upgrade 24: Opta / your-data scaling from prop_audit.py, upcoming games only
    prop_scale = {}
    if mode != "backtest" and os.path.exists(SCALE_FILE):
        prop_scale = json.load(open(SCALE_FILE))
        print(f"  Opta scaling applied (prop_scaling.json): {prop_scale}")
    for market, (parts, act_parts, lines) in MARKETS.items():
        sel = (d["position"] == "Goalkeeper") if market == "saves" else pd.Series(True, index=d.index)
        dd = d[sel]
        i_model = sum(dd["i_" + p] for p in parts)
        i_rates = sum(dd["r_" + p] for p in parts)
        i_base = sum(dd["b_" + p] for p in parts)
        actual = sum(dd["act_" + p] for p in act_parts)
        s_ps, s_pb, s_ms, s_mb = ps[sel.values], pb[sel.values], ms[sel.values], mb[sel.values]
        if mode == "backtest" and market not in NO_CAL:
            cals[market] = fit_calibration(market, lines, i_model, dd, actual)
        cal = cals.get(market, [1e6, 1.0, 1.0])
        for k in lines:
            prob, exp_cal = mixture_cal(k, i_model.values, s_ps, s_pb, s_ms, s_mb, cal,
                                        prop_scale.get(market, 1.0))
            rows.append(pd.DataFrame({
                "run_label": dd["run_label"], "season": dd["season"], "gameweek": dd["gameweek"], "mode": mode,
                "match_id": dd["match_id"], "team_id": dd["team_id"], "player_code": dd["player_code"],
                "position": dd["position"], "market": market, "line": k,
                "p_start": dd["p_start"], "p_play": dd["p_play"], "minutes_if_plays": dd["m_play"],
                "expected": exp_cal,
                "prob": prob,
                "rates_only_prob": mixture(k, i_rates.values, s_ps, s_pb, s_ms, s_mb),
                "baseline_prob": mixture(k, i_base.values, s_ps, s_pb, s_ms, s_mb),
                "act_minutes": dd["act_minutes"], "actual": actual,
            }))
    if mode == "backtest":
        json.dump(cals, open(CAL_FILE, "w"), indent=1)
    out = pd.concat(rows, ignore_index=True)
    out = out[out["p_play"] > 0]
    return out, eng


def save(eng, df):
    os.makedirs("player_data", exist_ok=True)
    df.to_csv(os.path.join("player_data", "player_markets.csv"), index=False, encoding="utf-8-sig")
    if eng is None:
        return
    from sqlalchemy import text
    text_cols = {"run_label", "season", "mode", "position", "market"}
    for c in df.columns:
        if c not in text_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    with eng.begin() as con:
        exists = con.execute(text("SELECT to_regclass('player_markets')")).scalar()
        if exists:
            con.execute(text("DELETE FROM player_markets WHERE run_label = ANY(:l) AND mode = :m"),
                        {"l": list(df["run_label"].unique()), "m": df["mode"].iloc[0]})
        df.to_sql("player_markets", con, if_exists="append", index=False, method="multi", chunksize=2000)


def main(mode):
    df, eng = build(mode)
    if df is None:
        return
    num = df.select_dtypes("number").columns
    df[num] = df[num].round(5)
    save(eng, df)
    print(f"Saved {len(df)} rows ({df['run_label'].nunique()} gameweeks, "
          f"{df[['match_id', 'player_code']].drop_duplicates().shape[0]} player-fixtures, {df['market'].nunique()} markets) to player_markets.")
    g = df[(df["market"] == "goals") & (df["line"] == 1)]
    chk = g.assign(e=g["expected"] * g["p_play"]).groupby(["run_label", "match_id", "team_id"])["e"].sum()
    print(f"  Check: players' expected goals per team per match average {chk.mean():.2f} "
          f"(should be about 1.4, the league's goals per team)")
    if mode == "next":
        top = g.sort_values("prob", ascending=False).head(10)
        print("  Most likely scorers next gameweek (player code: chance if he plays):")
        print("   " + ", ".join(f"{int(c)}: {p:.0%}" for c, p in zip(top["player_code"], top["prob"])))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("backtest", "next"):
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
