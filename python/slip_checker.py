"""
Upgrade 15.8: bet builder slip checker. A slip's legs are linked (Haaland scoring and City winning
tend to happen together), so multiplying the single chances gives the wrong answer. This script
plays each fixture 20,000 times and counts how often every leg of the slip lands together.

Put this file in the DC upgrade folder (next to player_markets.py) and run in the VS Code terminal:

  python slip_checker.py                  checks the slips in slip.csv for the next gameweek
  python slip_checker.py slip.csv "2025-26 after GW20"
                                          checks slips against a past gameweek's predictions
  python slip_checker.py test             the 15.8 test: three standard bet builders for every
                                          fixture in 2024-25 and 2025-26, joint chance vs the
                                          product of single chances vs what actually happened
  python slip_checker.py example          writes an example slip.csv to fill in

slip.csv - one row per leg (every leg of a slip must be in the same fixture):
  slip   home       away     leg     who        market          line  pick   price
  1      Man City   Arsenal  result                                   home   6.5
  1      Man City   Arsenal  player  Haaland    goals           1     over
  1      Man City   Arsenal  match              cards           3.5   over
  1      Man City   Arsenal  team    Man City   corners         4.5   over
  leg     result (pick home/draw/away), btts (pick yes/no), player, team or match
  market  player: goals, assists, goal_or_assist, shots, sot, fouls, fouled, tackles, carded, red,
          saves.  team / match: goals, corners, fouls, cards, booking_points, red
  line    the number to beat: 1 = 1 or more for players, 3.5 = over 3.5 for team / match
  pick    over (default) or under
  price   the site's decimal odds for the whole slip, on any one row of the slip (optional)
Player legs are worked out given the named players play (bookmakers void a leg if he doesn't);
the chance they all play is shown separately.

How one simulated match is played (all from the saved predictions for that gameweek):
  score             home and away goals from your match model's expected goals (matched to your
                    ensemble's win/draw/loss chances where it has them)
  who plays         each player starts, comes on or doesn't play (15.2), with his minutes
  goals             each team goal (3% are own goals) goes to a player on the pitch, in proportion
                    to his scoring rate x minutes (15.5); assists the same way among team-mates
  shots on target   his goals + extra shots on target, shots = shots on target + extra shots,
                    so a scorer always has a shot on target
  fouls, cards      the team's fouls and cards (15.4, referee included) shared among the players
                    on the pitch by their foul and card rates; fouled = the opponent's fouls
  saves             the opponent's shots on target that weren't goals, to his keeper
  corners           each team's corners (15.4)
Honest limit: corners, fouls and cards don't react to the simulated score.
"""
import os
import sys
import json
import unicodedata

import numpy as np
import pandas as pd

import player_minutes as pmin
from player_minutes import to_numbers
import player_markets as pmk

N_SIMS = 20000
OWN_GOAL = 0.03
PLAYER_MARKETS = ["goals", "assists", "goal_or_assist", "shots", "sot", "fouls", "fouled", "tackles",
                  "carded", "red", "saves"]
TEAM_MARKETS = ["goals", "corners", "fouls", "cards", "booking_points", "red"]
CAL_FILE = "player_markets_calibration.json"


ALIASES = {"man city": "manchester city", "man utd": "manchester united", "man united": "manchester united",
           "spurs": "tottenham", "forest": "nottingham forest", "nottm forest": "nottingham forest",
           "wolverhampton": "wolves", "villa": "aston villa", "palace": "crystal palace",
           "newcastle united": "newcastle", "west ham united": "west ham", "brighton and hove albion": "brighton",
           "leeds united": "leeds", "leicester city": "leicester", "ipswich town": "ipswich"}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = " ".join(s.lower().replace("-", " ").replace(".", "").replace("&", "and").split())
    return ALIASES.get(s, s)


# ------------------------------------------------------------------ data for one run
def load_run(run_label=None):
    """Everything the simulation needs for one gameweek (default: the latest 'next' run)."""
    test = os.environ.get("TEST_DIR")
    eng = None if test else pmin.engine()
    if test:
        rd = lambda p: pd.read_csv(p, low_memory=False)
        pmk_all = rd(os.path.join("player_data", "player_markets.csv"))
        mins = rd(os.path.join("player_data", "player_minutes.csv"))
        tc = rd(os.path.join("player_data", "team_counts.csv"))
        lam = rd(os.path.join(test, "fixtures.csv"))
        names = rd(os.path.join(test, "pl_players.csv"))
        teams = rd(os.path.join(test, "teams.csv"))
        matches = rd(os.path.join(test, "matches.csv"))
    else:
        q = lambda s: to_numbers(pd.read_sql(s, eng))
        if run_label is None:
            run_label = q("SELECT run_label FROM player_markets WHERE mode = 'next' "
                          "ORDER BY season DESC, gameweek DESC LIMIT 1")["run_label"].iloc[0]
        rl = run_label.replace("'", "''")
        pmk_all = q(f"SELECT * FROM player_markets WHERE run_label = '{rl}'")
        mins = q(f"SELECT * FROM player_minutes WHERE run_label = '{rl}'")
        tc = q(f"SELECT * FROM team_counts WHERE run_label = '{rl}' AND side IN ('home', 'away')")
        lam = q(f"""SELECT l.run_label, l.match_id, l.lambda_home, l.lambda_away, e.p_home, e.p_draw, e.p_away
                    FROM match_lambdas l LEFT JOIN external_predictions e ON e.source = 'ens_dcxg_50'
                         AND e.run_label = l.run_label AND e.match_id = l.match_id
                    WHERE l.run_label = '{rl}'""")
        names = q("SELECT season, player_code, web_name, team, position FROM pl_players")
        teams = q("SELECT team_id, team_name FROM teams")
        matches = q("SELECT match_id, home_team_id, away_team_id, home_goals, away_goals FROM matches")
    if run_label is None:
        nx = pmk_all[pmk_all["mode"] == "next"]
        run_label = nx.sort_values(["season", "gameweek"]).iloc[-1]["run_label"]
    mode = "next" if (pmk_all[pmk_all["run_label"] == run_label]["mode"] == "next").any() else "backtest"
    sel = lambda d: d[(d["run_label"] == run_label) & (d["mode"] == mode)] if "mode" in d else d[d["run_label"] == run_label]
    pk, mn, tc = sel(pmk_all), sel(mins), sel(tc)
    tc = tc[tc["side"].isin(["home", "away"])]
    lam = lam[lam["run_label"] == run_label].drop_duplicates("match_id")
    season = run_label[:7]
    return {"run": run_label, "mode": mode, "season": season, "pk": pk, "mins": mn, "tc": tc, "lam": lam,
            "names": names[names["season"] == season], "teams": teams, "matches": matches}


def num(rows, col, default):
    """First value of a column as a float; the default when missing or blank (None/NaN)."""
    if len(rows) == 0 or col not in rows:
        return default
    v = pd.to_numeric(rows[col].iloc[:1], errors="coerce").iloc[0]
    return default if pd.isna(v) else float(v)


def fixture_inputs(R, match_id):
    """Per-fixture arrays for the simulation."""
    lam = R["lam"][R["lam"]["match_id"] == match_id]
    if len(lam) == 0:
        return None
    l = lam.iloc[0]
    lh, la = pmk.calibrate(float(l["lambda_home"]), float(l["lambda_away"]),
                           float(l["p_home"]) if pd.notna(l.get("p_home")) else np.nan,
                           float(l["p_away"]) if pd.notna(l.get("p_away")) else np.nan)
    pk = R["pk"][R["pk"]["match_id"] == match_id]
    mn = R["mins"][R["mins"]["match_id"] == match_id]
    if len(pk) == 0:
        return None
    # one row per player: expected count if he plays, for each market (line 1)
    e = pk[pk["line"] == 1].pivot_table(index=["player_code", "team_id", "position"], columns="market",
                                        values="expected", aggfunc="first").reset_index()
    act = pk[pk["line"] == 1].pivot_table(index=["player_code"], columns="market", values="actual", aggfunc="first")
    sv = pk[pk["market"] == "saves"].groupby("player_code")["expected"].first()
    e["saves"] = e["player_code"].map(sv).fillna(0)
    e = e.merge(mn[["player_code", "p_start", "p_sub", "min_start", "min_sub"]].drop_duplicates("player_code"),
                on="player_code", how="left")
    e = e.merge(pk.drop_duplicates("player_code")[["player_code", "minutes_if_plays", "act_minutes"]],
                on="player_code", how="left")
    for c in ["p_start", "p_sub", "min_start", "min_sub", "minutes_if_plays"]:
        e[c] = pd.to_numeric(e[c], errors="coerce").fillna(0.0)
    m = R["matches"][R["matches"]["match_id"] == match_id].iloc[0]
    tc = R["tc"][R["tc"]["match_id"] == match_id]
    counts = {}
    for stat in ("corners", "fouls", "yellows", "reds"):
        for side in ("home", "away"):
            row = tc[(tc["stat"] == stat) & (tc["side"] == side)]
            mu = num(row, "expected", np.nan)
            r = num(row, "r", 1000.0)
            a = num(row, "actual", np.nan)          # blank for upcoming games (mode 'next')
            counts[(stat, side)] = (mu, r, a)
    return {"lh": lh, "la": la, "players": e, "act": act, "home_id": int(m["home_team_id"]),
            "away_id": int(m["away_team_id"]), "counts": counts,
            "act_score": (m.get("home_goals"), m.get("away_goals"))}


# ------------------------------------------------------------------ the simulation
def nb(rng, mu, r, size):
    if not np.isfinite(mu) or mu <= 0:
        return np.zeros(size, dtype=int)
    if r >= 999:
        return rng.poisson(mu, size)
    return rng.negative_binomial(r, r / (r + mu), size)


def allocate(rng, totals, weights):
    """Share each simulation's total count among players in proportion to weights (sims x players)."""
    n, k = weights.shape
    out = np.zeros((n, k), dtype=int)
    wsum = weights.sum(axis=1)
    ok = (totals > 0) & (wsum > 0)
    if not ok.any():
        return out
    p = weights[ok] / wsum[ok, None]
    out[ok] = np.array([rng.multinomial(t, pp) for t, pp in zip(totals[ok], p)])
    return out


def simulate(F, n=N_SIMS, seed=1):
    """Plays the fixture n times; returns a dict of arrays (sims x players, or sims)."""
    rng = np.random.default_rng(seed)
    P = F["players"]
    cal = json.load(open(CAL_FILE)) if os.path.exists(CAL_FILE) else {}
    k = len(P)
    side = np.where(P["team_id"].values == F["home_id"], 0, 1)
    # who plays and for how long
    u = rng.random((n, k))
    ps, pb = P["p_start"].values, P["p_sub"].values
    starts = u < ps
    subs = (u >= ps) & (u < ps + pb)
    minutes = np.where(starts, P["min_start"].values, np.where(subs, P["min_sub"].values, 0.0))
    plays = minutes > 0
    # per-minute rates (expected if he plays / minutes if he plays)
    mip = np.maximum(P["minutes_if_plays"].values, 1.0)
    rate = {c: (P[c].fillna(0).values / mip if c in P else np.zeros(k))
            for c in ("goals", "assists", "shots", "sot", "fouls", "fouled", "tackles", "carded", "red", "saves")}
    out = {"plays": plays}
    # score
    gh, ga = rng.poisson(F["lh"], n), rng.poisson(F["la"], n)
    out["home_goals"], out["away_goals"] = gh, ga
    goals = np.zeros((n, k), dtype=int)
    assists = np.zeros((n, k), dtype=int)
    for s, g in ((0, gh), (1, ga)):
        mine = side == s
        if not mine.any():
            continue
        own = rng.binomial(g, OWN_GOAL)
        scored = g - own
        w = rate["goals"][mine] * minutes[:, mine]
        gi = allocate(rng, scored, w)
        goals[:, mine] = gi
        # assists: each scored goal is assisted with this team's assists-per-goal, by xA rate x minutes
        apg = min(rate["assists"][mine].sum() / max(rate["goals"][mine].sum(), 1e-9), 0.95)
        n_ast = rng.binomial(scored, apg)
        wa = rate["assists"][mine] * minutes[:, mine]
        assists[:, mine] = allocate(rng, n_ast, wa)
    out["goals"], out["assists"] = goals, assists
    # shots on target and shots: goals + extra
    extra_sot = np.maximum(rate["sot"] - rate["goals"], 0) * minutes
    sot = goals + rng.poisson(extra_sot)
    extra_shots = np.maximum(rate["shots"] - rate["sot"], 0) * minutes
    out["sot"], out["shots"] = sot, sot + rng.poisson(extra_shots)
    # team counts and their share among players
    cnt = F["counts"]
    for stat in ("corners", "fouls", "yellows", "reds"):
        for s, nm in ((0, "home"), (1, "away")):
            mu, r, _ = cnt[(stat, nm)]
            out[f"{stat}_{nm}"] = nb(rng, mu, r, n)
    fouls = np.zeros((n, k), dtype=int)
    fouled = np.zeros((n, k), dtype=int)
    yel = np.zeros((n, k), dtype=int)
    red = np.zeros((n, k), dtype=int)
    for s, nm, opp in ((0, "home", "away"), (1, "away", "home")):
        mine = side == s
        fouls[:, mine] = allocate(rng, out[f"fouls_{nm}"], rate["fouls"][mine] * minutes[:, mine])
        fouled[:, mine] = allocate(rng, out[f"fouls_{opp}"], rate["fouled"][mine] * minutes[:, mine])
        yel[:, mine] = allocate(rng, out[f"yellows_{nm}"], np.maximum(rate["carded"][mine] - rate["red"][mine], 1e-6)
                                * minutes[:, mine] * plays[:, mine])
        red[:, mine] = allocate(rng, out[f"reds_{nm}"], np.maximum(rate["red"][mine], 1e-6) * minutes[:, mine] * plays[:, mine])
    out["fouls"], out["fouled"], out["red"] = fouls, fouled, red
    out["carded"] = yel + red
    r_t = cal.get("tackles", [1e6, 1, 1])[0]
    mu_t = rate["tackles"] * minutes
    out["tackles"] = np.where(mu_t > 0, rng.negative_binomial(r_t, r_t / (r_t + np.maximum(mu_t, 1e-9))), 0) \
        if r_t < 1e5 else rng.poisson(mu_t)
    out["goal_or_assist"] = goals + assists
    # keeper saves: the opponent's shots on target that weren't goals, to the keeper on the pitch
    saves = np.zeros((n, k), dtype=int)
    gk = (P["position"].values == "Goalkeeper")
    for s in (0, 1):
        opp = side == 1 - s
        opp_sot = sot[:, opp].sum(axis=1)
        opp_goals = (ga if s == 0 else gh)
        sv = np.maximum(opp_sot - np.minimum(opp_goals, opp_sot), 0)
        keepers = np.where(gk & (side == s))[0]
        if len(keepers):
            # the keeper who plays most; if two play, the starter gets them
            order = keepers[np.argsort(-P["p_start"].values[keepers])]
            assigned = np.zeros(n, dtype=bool)
            for j in order:
                give = plays[:, j] & ~assigned
                saves[give, j] = sv[give]
                assigned |= give
    out["saves"] = saves
    return out


# ------------------------------------------------------------------ legs
def find_team(R, name, F):
    t = R["teams"]
    ids = {F["home_id"], F["away_id"]}
    cand = t[t["team_id"].isin(ids)]
    nm = norm(name)
    for _, r in cand.iterrows():
        if norm(r["team_name"]) == nm or nm in norm(r["team_name"]) or norm(r["team_name"]) in nm:
            return int(r["team_id"])
    raise ValueError(f"team '{name}' isn't in this fixture")


def find_player(R, name, F):
    P = F["players"].merge(R["names"][["player_code", "web_name"]].drop_duplicates("player_code"),
                           on="player_code", how="left")
    nm = norm(name)
    hit = P[P["web_name"].map(norm) == nm]
    if len(hit) == 0:
        hit = P[P["web_name"].map(norm).str.contains(nm, regex=False, na=False)]
    if len(hit) == 0:
        raise ValueError(f"player '{name}' isn't in either squad for this fixture")
    if len(hit) > 1:
        raise ValueError(f"'{name}' matches several players: {', '.join(hit['web_name'])} - be more exact")
    return int(np.where(F["players"]["player_code"].values == hit["player_code"].iloc[0])[0][0])


def leg_outcome(R, F, S, leg):
    """(sims-long True/False for the leg, player index or None)."""
    kind = str(leg.get("leg", "")).strip().lower()
    market = str(leg.get("market", "") if pd.notna(leg.get("market")) else "").strip().lower()
    pick = str(leg.get("pick", "over") if pd.notna(leg.get("pick")) else "over").strip().lower()
    line = float(leg["line"]) if pd.notna(leg.get("line")) and str(leg.get("line")).strip() != "" else None
    gh, ga = S["home_goals"], S["away_goals"]
    if kind == "result":
        return {"home": gh > ga, "draw": gh == ga, "away": gh < ga}[pick], None
    if kind == "btts":
        both = (gh > 0) & (ga > 0)
        return (both if pick in ("yes", "over") else ~both), None
    if kind == "player":
        j = find_player(R, leg["who"], F)
        x = S[market][:, j]
        res = x >= line if pick == "over" else x < line
        return res, j
    if kind in ("team", "match"):
        sides = ["home", "away"]
        if kind == "team":
            tid = find_team(R, leg["who"], F)
            sides = ["home" if tid == F["home_id"] else "away"]
        def tot(stat):
            return sum(S[f"{stat}_{s}"] for s in sides)
        if market == "goals":
            x = sum({"home": gh, "away": ga}[s] for s in sides)
        elif market == "cards":
            x = tot("yellows") + tot("reds")
        elif market == "booking_points":
            x = 10 * tot("yellows") + 25 * tot("reds")
        elif market == "red":
            x = tot("reds"); line = 0.5 if line is None else line
        else:
            x = tot(market)
        return (x > line if pick == "over" else x < line), None
    raise ValueError(f"unknown leg type '{kind}'")


def check_slip(R, legs, n=N_SIMS):
    """Joint chance of a slip, the single chances, and the chance its named players all play."""
    home = find_fixture(R, legs.iloc[0]["home"], legs.iloc[0]["away"])
    F = fixture_inputs(R, home)
    if F is None:
        raise ValueError("no predictions for this fixture in this gameweek")
    S = simulate(F, n)
    hits, singles, players = [], [], []
    for _, leg in legs.iterrows():
        h, j = leg_outcome(R, F, S, leg)
        hits.append(h)
        if j is not None:
            players.append(j)
    cond = np.ones(n, dtype=bool)
    for j in set(players):
        cond &= S["plays"][:, j]
    for h in hits:
        singles.append(h[cond].mean() if cond.any() else np.nan)
    joint = np.logical_and.reduce(hits)[cond].mean() if cond.any() else np.nan
    return {"joint": joint, "singles": singles, "product": float(np.prod(singles)),
            "all_play": cond.mean(), "match_id": home}


def find_fixture(R, home, away):
    t = R["teams"].copy()
    t["n"] = t["team_name"].map(norm)
    def tid(x):
        nm = norm(x)
        hit = t[(t["n"] == nm) | t["n"].str.contains(nm, regex=False)]
        if len(hit) == 0:
            raise ValueError(f"unknown team '{x}'")
        return set(hit["team_id"])
    hs, as_ = tid(home), tid(away)
    m = R["matches"]
    ids = set(R["pk"]["match_id"].unique())
    f = m[m["home_team_id"].isin(hs) & m["away_team_id"].isin(as_) & m["match_id"].isin(ids)]
    if len(f) == 0:
        raise ValueError(f"{home} v {away} isn't in the {R['run']} predictions")
    return int(f["match_id"].iloc[0])


# ------------------------------------------------------------------ commands
def run_slips(path, run_label):
    R = load_run(run_label)
    slips = pd.read_csv(path, encoding="utf-8-sig")
    print(f"Gameweek: {R['run']} ({R['mode']}), {N_SIMS:,} simulations per fixture\n")
    rows = []
    for sid, legs in slips.groupby("slip", sort=False):
        try:
            res = check_slip(R, legs)
        except ValueError as e:
            print(f"Slip {sid}: {e}\n")
            continue
        price = pd.to_numeric(legs.get("price"), errors="coerce").dropna()
        price = float(price.iloc[0]) if len(price) else np.nan
        fixture = f"{legs.iloc[0]['home']} v {legs.iloc[0]['away']}"
        print(f"Slip {sid}: {fixture}")
        details = []
        for (_, leg), p in zip(legs.iterrows(), res["singles"]):
            desc = " ".join(str(leg[c]) for c in ("leg", "who", "market", "line", "pick")
                            if c in leg and pd.notna(leg[c]) and str(leg[c]).strip() != "")
            print(f"   {desc:45s} {p:6.1%}")
            details.append(f"{desc} ({p:.0%})")
        fair = 1 / res["joint"] if res["joint"] > 0 else np.inf
        print(f"   Chance all legs land: {res['joint']:.1%}  (fair odds {fair:.2f})"
              f"   | single chances multiplied: {res['product']:.1%}")
        if res["all_play"] < 0.999:
            print(f"   Chance every named player plays: {res['all_play']:.0%} (player legs are void if he doesn't)")
        if np.isfinite(price):
            implied = 1 / price
            print(f"   Site price {price:.2f} = {implied:.1%}.  Your edge: {res['joint'] * price - 1:+.1%} "
                  f"({'model thinks it is value' if res['joint'] > implied else 'no value at this price'})")
        print()
        rows.append({"run_label": R["run"], "slip": sid, "fixture": fixture, "legs_detail": " + ".join(details),
                     "match_id": res["match_id"], "legs": len(legs),
                     "fair_odds": round(fair, 2) if np.isfinite(fair) else np.nan,
                     "joint_prob": res["joint"], "product_of_singles": res["product"],
                     "all_named_play": res["all_play"], "price": price,
                     "implied_prob": 1 / price if np.isfinite(price) else np.nan,
                     "edge": res["joint"] * price - 1 if np.isfinite(price) else np.nan})
    if rows:
        df = pd.DataFrame(rows)
        os.makedirs("player_data", exist_ok=True)
        df.to_csv(os.path.join("player_data", "slip_results.csv"), index=False, encoding="utf-8-sig")
        if not os.environ.get("TEST_DIR"):
            eng = pmin.engine()
            df.to_sql("slip_results", eng, if_exists="replace", index=False)
        print("Saved to slip_results (the dashboard's Slip sheet reads it).")


def test(n=4000, every=1):
    """Three standard bet builders on every fixture of 2024-25 and 2025-26."""
    test_env = os.environ.get("TEST_DIR")
    if test_env:
        runs = sorted(pd.read_csv(os.path.join("player_data", "player_markets.csv"),
                                  usecols=["run_label", "season", "mode"]).query("mode == 'backtest'")
                      ["run_label"].unique())
    else:
        eng = pmin.engine()
        runs = list(pd.read_sql("SELECT DISTINCT run_label FROM player_markets WHERE mode = 'backtest'", eng)["run_label"])
    runs = [r for r in runs if r[:7] in ("2024-25", "2025-26")][::every]
    rows = []
    print(f"Testing on {len(runs)} gameweeks ({n:,} simulations per fixture) ...")
    for i, rl in enumerate(runs):
        R = load_run(rl)
        for mid in R["pk"]["match_id"].unique():
            F = fixture_inputs(R, mid)
            if F is None or pd.isna(F["act_score"][0]):
                continue
            P = F["players"]
            S = simulate(F, n, seed=int(mid))
            hg, ag = F["act_score"]
            act = F["act"]
            # the home side's likeliest scorer (of players likely to start)
            hp = P[(P["team_id"] == F["home_id"]) & (P["p_start"] > 0.6)]
            if len(hp) == 0:
                continue
            j = int(hp["goals"].idxmax())
            pc = P.loc[j, "player_code"]
            played = pd.notna(P.loc[j, "act_minutes"]) and P.loc[j, "act_minutes"] > 0
            cond = S["plays"][:, j]
            g, s = S["goals"][cond, j], S["sot"][cond, j]
            win, gh_, ga_ = S["home_goals"][cond] > S["away_goals"][cond], S["home_goals"][cond], S["away_goals"][cond]
            cards = (S["yellows_home"] + S["yellows_away"] + S["reds_home"] + S["reds_away"])[cond]
            a_g = act.loc[pc, "goals"] if pc in act.index else np.nan
            a_s = act.loc[pc, "sot"] if pc in act.index else np.nan
            ycards = sum(F["counts"][(st, sd)][2] for st in ("yellows", "reds") for sd in ("home", "away"))
            combos = {
                "home win + their likeliest scorer scores": (win & (g >= 1), [win, g >= 1],
                                                             (hg > ag) and a_g >= 1 if played else None),
                "likeliest scorer scores + 2 or more shots on target": ((g >= 1) & (s >= 2), [g >= 1, s >= 2],
                                                                        (a_g >= 1 and a_s >= 2) if played else None),
                "over 2.5 goals + over 3.5 cards": (((gh_ + ga_) > 2.5) & (cards > 3.5), [(gh_ + ga_) > 2.5, cards > 3.5],
                                                    ((hg + ag) > 2.5 and ycards > 3.5) if np.isfinite(ycards) else None),
            }
            for name, (joint, parts, outcome) in combos.items():
                if outcome is None or cond.sum() == 0:
                    continue
                rows.append({"season": rl[:7], "combo": name, "joint": joint.mean(),
                             "product": float(np.prod([p.mean() for p in parts])), "hit": float(outcome)})
        if (i + 1) % 10 == 0:
            print(f"  {i + 1} of {len(runs)} gameweeks done", flush=True)
    df = pd.DataFrame(rows)
    df["b_joint"] = (df["joint"] - df["hit"]) ** 2
    df["b_prod"] = (df["product"] - df["hit"]) ** 2
    g = df.groupby(["combo", "season"]).agg(slips=("hit", "size"), actual=("hit", "mean"), joint=("joint", "mean"),
                                           product=("product", "mean"), brier_joint=("b_joint", "mean"),
                                           brier_product=("b_prod", "mean"))
    print("\nBet builder test: chance from the simulation (joint) vs single chances multiplied (product)")
    for (combo, season), r in g.iterrows():
        print(f"  {combo:52s} {season}  {int(r.slips):4d} slips | actual {r.actual:5.1%}  joint {r.joint:5.1%}"
              f"  product {r['product']:5.1%} | Brier joint {r.brier_joint:.4f}  product {r.brier_product:.4f}")
    os.makedirs("player_data", exist_ok=True)
    df.to_csv(os.path.join("player_data", "slip_test.csv"), index=False, encoding="utf-8-sig")
    if not test_env:
        df.to_sql("slip_test", pmin.engine(), if_exists="replace", index=False)


EXAMPLE = """slip,home,away,leg,who,market,line,pick,price
1,Man City,Arsenal,result,,,,home,
1,Man City,Arsenal,player,Haaland,goals,1,over,4.50
2,Man City,Arsenal,match,,goals,2.5,over,
2,Man City,Arsenal,match,,cards,3.5,over,
2,Man City,Arsenal,team,Man City,corners,4.5,over,3.75
"""


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "test":
        test(every=int(a[1]) if len(a) > 1 else 1)
    elif a and a[0] == "example":
        open("slip.csv", "w", encoding="utf-8").write(EXAMPLE)
        print("Wrote slip.csv - change the teams, players and lines to this week's fixtures, then run: python slip_checker.py")
    elif a and a[0] in ("-h", "help"):
        print(__doc__)
    else:
        path = a[0] if a else "slip.csv"
        if not os.path.exists(path):
            print(f"No {path} yet. Run 'python slip_checker.py example' to make one.")
            sys.exit(1)
        run_slips(path, a[1] if len(a) > 1 else None)
