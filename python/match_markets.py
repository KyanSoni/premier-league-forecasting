"""
Upgrade 15.7: team and match markets for every fixture - corners, fouls, cards, booking points,
red cards and 'most corners' / 'most cards' - at every common line, built from 15.4's team counts.

Put this file in the DC upgrade folder (next to team_counts.py) and run in the VS Code terminal:

  python match_markets.py backtest   every gameweek in team_counts (mode 'backtest'); chooses the
                                     spread settings on 2024-25, checks them on 2025-26, saves
                                     everything with what actually happened
  python match_markets.py next       the next gameweek (after 'python team_counts.py next')

Markets (side = home team, away team or the whole match):
  corners           over X.5 for each team and the match
  fouls             over X.5 for each team and the match (referee included, from 15.4)
  cards             yellow + red cards (each card counts 1), over X.5, each team and the match
  booking_points    10 per yellow, 25 per red (a second-yellow red = 10 + 25 = 35), over X.5
  red               at least one red card: each team and the match
  most_corners      home / draw / away (which team wins more corners)
  most_cards        home / draw / away
Bookmakers' settlement rules differ (some count a red as 2 cards, some cap booking points per
player): check the site's rules before comparing prices.

How the chances are worked out:
  team counts       negative binomial around 15.4's expected count. The spread (r) is chosen per
                    stat on 2024-25 from a grid that starts with 15.4's own value
  match totals      either the two teams combined (independent), or one negative binomial around
                    the match total with its own spread - the second allows for whole-match
                    effects (a strict referee, a heated game) that push both teams up together.
                    Chosen per stat on 2024-25 and kept only if 2025-26 agrees
  baseline          the same, around 15.4's baseline (league average with home advantage)
"""
import os
import sys
import json

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

import player_minutes as pmin
from player_minutes import to_numbers

SETTINGS_FILE = "match_markets_settings.json"
MAXN = 80
R_GRID = [None, 40.0, 20.0, 10.0, 6.0, 4.0]       # None = keep 15.4's spread
LINES = {
    "corners":        {"team": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5],
                       "match": [6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5]},
    "fouls":          {"team": [6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5],
                       "match": [16.5, 18.5, 19.5, 20.5, 21.5, 22.5, 23.5, 24.5, 26.5, 28.5]},
    "cards":          {"team": [0.5, 1.5, 2.5, 3.5, 4.5],
                       "match": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5]},
    "booking_points": {"team": [5.5, 15.5, 25.5, 35.5, 45.5],
                       "match": [15.5, 25.5, 35.5, 45.5, 55.5, 65.5, 75.5]},
}
COUNT_STATS = ["corners", "fouls", "yellows", "reds"]


# ------------------------------------------------------------------ data
def load(mode):
    test = os.environ.get("TEST_DIR")
    if test:
        tc = pd.read_csv(os.path.join("player_data", "team_counts.csv"))
        eng = None
    else:
        eng = pmin.engine()
        tc = to_numbers(pd.read_sql(f"""SELECT run_label, season, gameweek, mode, match_id, home_team_id,
                                              away_team_id, stat, referee, ref_factor, side, expected,
                                              base_expected, r, actual
                                       FROM team_counts WHERE mode = '{mode}'
                                         AND stat IN ('corners', 'fouls', 'yellows', 'reds')""", eng))
    tc = tc[(tc["mode"] == mode) & tc["stat"].isin(COUNT_STATS) & tc["side"].isin(["home", "away"])]
    for c in ["expected", "base_expected", "r", "actual", "ref_factor"]:
        tc[c] = pd.to_numeric(tc[c], errors="coerce").astype(float)
    key = ["run_label", "season", "gameweek", "match_id", "home_team_id", "away_team_id"]
    w = tc.pivot_table(index=key, columns=["stat", "side"], values=["expected", "base_expected", "r", "actual"],
                       aggfunc="first")
    w.columns = [f"{v}_{s}_{sd}" for v, s, sd in w.columns]
    w = w.reset_index()
    refs = tc[tc["stat"] == "fouls"].drop_duplicates(["run_label", "match_id"])[["run_label", "match_id", "referee", "ref_factor"]]
    w = w.merge(refs, on=["run_label", "match_id"], how="left")
    return w, eng


# ------------------------------------------------------------------ maths
def pmf(mu, r):
    k = np.arange(MAXN)
    if not np.isfinite(mu):
        return np.full(MAXN, np.nan)
    mu = max(mu, 1e-6)
    if r is None or r >= 999:
        return poisson.pmf(k, mu)
    return nbinom.pmf(k, r, r / (r + mu))


def over(p, line):
    return float(p[int(np.floor(line)) + 1:].sum())


def bp_pmf(py, pr):
    """Booking points 10 x yellows + 25 x reds, on a 5-point grid (index = points / 5)."""
    out = np.zeros(2 * MAXN + 5 * MAXN)
    for y in range(MAXN):
        if py[y] < 1e-12:
            continue
        for r in range(8):
            out[2 * y + 5 * r] += py[y] * pr[r]
    return out


def bp_over(p5, line):
    idx = int(np.floor(line / 5)) + 1
    return float(p5[idx:].sum())


def team_pmfs(row, stat, prefix, r_team):
    """(home pmf, away pmf) for a stat; prefix 'expected' or 'base_expected'."""
    out = []
    for side in ("home", "away"):
        mu = row[f"{prefix}_{stat}_{side}"]
        r0 = row.get(f"r_{stat}_{side}", np.nan)
        r = r_team if r_team is not None else (r0 if np.isfinite(r0) else None)
        out.append(pmf(mu, r))
    return out


def match_pmf(ph, pa, mu, r_match):
    if r_match is None:
        return np.convolve(ph, pa)[:MAXN]
    return pmf(mu, r_match)


# ------------------------------------------------------------------ markets for one fixture
def fixture_markets(row, st):
    """All markets for one fixture. st = {stat: {'team': r, 'match': r}} for corners, fouls, yellows."""
    rows = []
    act = lambda s, sd: row.get(f"actual_{s}_{sd}", np.nan)
    for prefix, tag in (("expected", "prob"), ("base_expected", "base_prob")):
        d = {}
        for s in ("corners", "fouls", "yellows"):
            ph, pa = team_pmfs(row, s, prefix, st[s]["team"])
            mu = row[f"{prefix}_{s}_home"] + row[f"{prefix}_{s}_away"]
            d[s] = (ph, pa, match_pmf(ph, pa, mu, st[s]["match"]), mu)
        rh, ra = (pmf(row[f"{prefix}_reds_home"], None), pmf(row[f"{prefix}_reds_away"], None))
        rm = np.convolve(rh, ra)[:MAXN]
        # cards = yellows + reds
        yh, ya, ym, _ = d["yellows"]
        d["cards"] = (np.convolve(yh, rh)[:MAXN], np.convolve(ya, ra)[:MAXN], np.convolve(ym, rm)[:MAXN], None)
        d["bp"] = (bp_pmf(yh, rh), bp_pmf(ya, ra), bp_pmf(ym, rm))
        d["reds"] = (rh, ra, rm)
        if prefix == "expected":
            model = d
        else:
            base = d

    def add(market, side, line, outcome, p, b, expected, actual, hit):
        rows.append({"market": market, "side": side, "line": line, "outcome": outcome, "prob": p,
                     "base_prob": b, "expected": expected, "actual": actual, "hit": hit})

    for market, s in (("corners", "corners"), ("fouls", "fouls"), ("cards", "cards")):
        for i, side in enumerate(("home", "away", "match")):
            p, b = model[s][i], base[s][i]
            if s == "cards":
                a = {"home": act("yellows", "home") + act("reds", "home"),
                     "away": act("yellows", "away") + act("reds", "away")}
            else:
                a = {"home": act(s, "home"), "away": act(s, "away")}
            a["match"] = a["home"] + a["away"]
            exp = float((np.arange(MAXN) * p).sum())
            for line in LINES[market]["match" if side == "match" else "team"]:
                add(market, side, line, "over", over(p, line), over(b, line), exp, a[side],
                    float(a[side] > line) if np.isfinite(a[side]) else np.nan)
    # booking points
    for i, side in enumerate(("home", "away", "match")):
        p, b = model["bp"][i], base["bp"][i]
        if side == "match":
            a = 10 * (act("yellows", "home") + act("yellows", "away")) + 25 * (act("reds", "home") + act("reds", "away"))
        else:
            a = 10 * act("yellows", side) + 25 * act("reds", side)
        exp = float((np.arange(len(p)) * 5 * p).sum())
        for line in LINES["booking_points"]["match" if side == "match" else "team"]:
            add("booking_points", side, line, "over", bp_over(p, line), bp_over(b, line), exp, a,
                float(a > line) if np.isfinite(a) else np.nan)
    # red cards
    for i, side in enumerate(("home", "away", "match")):
        a = act("reds", "home") + act("reds", "away") if side == "match" else act("reds", side)
        p, b = model["reds"][i], base["reds"][i]
        add("red", side, 0.5, "over", over(p, 0.5), over(b, 0.5), float((np.arange(MAXN) * p).sum()), a,
            float(a > 0.5) if np.isfinite(a) else np.nan)
    # most corners / most cards
    for market, s in (("most_corners", "corners"), ("most_cards", "cards")):
        def hda(ph, pa):
            j = np.outer(ph, pa)
            return np.tril(j, -1).sum(), np.trace(j), np.triu(j, 1).sum()
        pm_, pb_ = hda(model[s][0], model[s][1]), hda(base[s][0], base[s][1])
        if s == "cards":
            ah, aa = act("yellows", "home") + act("reds", "home"), act("yellows", "away") + act("reds", "away")
        else:
            ah, aa = act(s, "home"), act(s, "away")
        res = None if not (np.isfinite(ah) and np.isfinite(aa)) else ("home" if ah > aa else "draw" if ah == aa else "away")
        for j, o in enumerate(("home", "draw", "away")):
            add(market, "match", np.nan, o, float(pm_[j]), float(pb_[j]), np.nan,
                (ah - aa) if res else np.nan, (float(res == o) if res else np.nan))
    return rows


# ------------------------------------------------------------------ choosing the spreads
def brier_for(w, stat, side_kind, r, season):
    """Brier of over/under at every line for one stat, team or match side, one season."""
    d = w[w["season"] == season]
    lines = LINES[stat]["match" if side_kind == "match" else "team"]
    tot, n = 0.0, 0
    for row in d.to_dict("records"):
        if side_kind == "team":
            for side in ("home", "away"):
                a = row.get(f"actual_{stat}_{side}")
                if not np.isfinite(a):
                    continue
                r0 = row.get(f"r_{stat}_{side}")
                p = pmf(row[f"expected_{stat}_{side}"], r if r is not None else (r0 if np.isfinite(r0) else None))
                for line in lines:
                    tot += (over(p, line) - float(a > line)) ** 2; n += 1
        else:
            a = row.get(f"actual_{stat}_home") + row.get(f"actual_{stat}_away")
            if not np.isfinite(a):
                continue
            ph, pa = team_pmfs(row, stat, "expected", r["team"])
            p = match_pmf(ph, pa, row[f"expected_{stat}_home"] + row[f"expected_{stat}_away"], r["match"])
            for line in lines:
                tot += (over(p, line) - float(a > line)) ** 2; n += 1
    return tot / max(n, 1)


def choose(w):
    st = {}
    lines_of = {"corners": "corners", "fouls": "fouls", "yellows": "cards"}
    print("Choosing the spread for each stat on 2024-25 (Brier over every line; lower = better):")
    for s in ("corners", "fouls", "yellows"):
        LINES.setdefault(s, LINES[lines_of[s]])
        sc = {r: brier_for(w, s, "team", r, "2024-25") for r in R_GRID}
        rt = min(R_GRID, key=lambda r: sc[r])
        b25 = {r: brier_for(w, s, "team", r, "2025-26") for r in (None, rt)}
        if b25[rt] > b25[None]:
            rt = None
        mopts = [None] + R_GRID[1:]
        sm = {r: brier_for(w, s, "match", {"team": rt, "match": r}, "2024-25") for r in mopts}
        rm = min(mopts, key=lambda r: sm[r])
        m25 = {r: brier_for(w, s, "match", {"team": rt, "match": r}, "2025-26") for r in (None, rm)}
        if m25[rm] > m25[None]:
            rm = None
        st[s] = {"team": rt, "match": rm}
        print(f"  {s:8s} team spread {('15.4 value' if rt is None else rt)!s:10s} (2025-26 {b25[None]:.4f} -> {b25[rt]:.4f})"
              f" | match {'two teams combined' if rm is None else 'own spread ' + str(rm):22s}"
              f" (2025-26 {m25[None]:.4f} -> {m25[rm]:.4f})")
    for s in ("corners", "fouls", "yellows"):
        LINES.pop(s, None) if s == "yellows" else None
    return st


# ------------------------------------------------------------------ run
def main(mode):
    w, eng = load(mode)
    if len(w) == 0:
        print(f"No '{mode}' rows in team_counts. Run 'python team_counts.py {mode}' first.")
        return
    if mode == "backtest":
        st = choose(w)
        json.dump(st, open(SETTINGS_FILE, "w"), indent=1)
    else:
        default = {s: {"team": None, "match": None} for s in ("corners", "fouls", "yellows")}
        st = json.load(open(SETTINGS_FILE)) if os.path.exists(SETTINGS_FILE) else default
    out = []
    for row in w.to_dict("records"):
        for m in fixture_markets(row, st):
            m.update({k: row[k] for k in ("run_label", "season", "gameweek", "match_id", "home_team_id",
                                          "away_team_id", "referee", "ref_factor")})
            out.append(m)
    df = pd.DataFrame(out)
    df.insert(3, "mode", mode)
    cols = ["run_label", "season", "gameweek", "mode", "match_id", "home_team_id", "away_team_id", "referee",
            "ref_factor", "market", "side", "line", "outcome", "expected", "prob", "base_prob", "actual", "hit"]
    df = df[cols]
    num = ["gameweek", "match_id", "home_team_id", "away_team_id", "ref_factor", "line", "expected", "prob",
           "base_prob", "actual", "hit"]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float).round(5)
    save(eng, df)
    print(f"Saved {len(df)} rows ({df['match_id'].nunique()} fixtures, {df['run_label'].nunique()} gameweeks) to match_markets.")
    if mode == "backtest":
        t = df[df["hit"].notna() & df["season"].isin(["2024-25", "2025-26"])]
        t = t.assign(b=(t["prob"] - t["hit"]) ** 2, bb=(t["base_prob"] - t["hit"]) ** 2)
        g = t.groupby(["market", "season"])[["b", "bb"]].mean().round(4).unstack("season")
        print("  Brier, all lines (model / baseline):")
        for mk, r in g.iterrows():
            print(f"    {mk:15s} 2024-25 {r[('b', '2024-25')]:.4f} / {r[('bb', '2024-25')]:.4f}"
                  f"   2025-26 {r[('b', '2025-26')]:.4f} / {r[('bb', '2025-26')]:.4f}")
    else:
        m = df[(df["market"] == "cards") & (df["side"] == "match") & (df["line"] == 3.5)]
        print("  Over 3.5 cards next gameweek: " + ", ".join(
            f"{int(r.home_team_id)}-{int(r.away_team_id)} {r.prob:.0%}" for r in m.itertuples()))


def save(eng, df):
    os.makedirs("player_data", exist_ok=True)
    df.to_csv(os.path.join("player_data", "match_markets.csv"), index=False, encoding="utf-8-sig")
    if eng is None:
        return
    from sqlalchemy import text
    with eng.begin() as con:
        exists = con.execute(text("SELECT to_regclass('match_markets')")).scalar()
        if exists:
            con.execute(text("DELETE FROM match_markets WHERE run_label = ANY(:l) AND mode = :m"),
                        {"l": list(df["run_label"].unique()), "m": df["mode"].iloc[0]})
        df.to_sql("match_markets", con, if_exists="append", index=False, method="multi", chunksize=2000)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("backtest", "next"):
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
