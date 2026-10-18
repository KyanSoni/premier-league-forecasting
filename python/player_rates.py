"""
Upgrade 15.3: each player's per-90 rates, before every gameweek.

Put this file in the DC upgrade folder (next to player_minutes.py) and run in the VS Code terminal:

  python player_rates.py backtest     every gameweek in player_minutes (mode 'backtest'), with what
                                      actually happened in the next match stored alongside for testing
  python player_rates.py next         the next gameweek (run 'python player_minutes.py next' first)
  python player_rates.py form         15.6x test: recent-form weighting and penalty takers. Tries
                                      each setting on 2024-25, confirms on 2025-26, and saves the
                                      winners to player_rates_form.json (used by backtest and next)

Run 'python player_minutes.py backtest' (15.2) before the backtest here: the squads come from it.

Rates stored per 90 minutes: xG, xA, shots, shots on target, fouls committed, fouled, tackles,
yellow cards and red cards. For each one:

    rate = (this season's count + W x last season's count + K x position average)
           / (this season's minutes + W x last season's minutes + K)          (x 90)

  this season       games before the gameweek being predicted
  last season       weighted by W (and the season before by W x W, for cards)
  position average  the league rate for his position, worth K minutes of play: a player with
                    few minutes stays close to it, a regular moves to his own numbers
W and K were chosen per stat on 2024-25 and confirmed on 2025-26 (see the guide, 15.3).

Sources: FPL-Core-Insights per-match stats (2024-25 on), Understat 2023-24 season totals (xG, xA,
shots: the "last season" for 2024-25), FPL per-fixture cards (2022-23 on).

The baseline to beat: his raw rate this season (last season's if he has under 90 minutes,
the position average if neither).

15.6x additions (only used once 'form' has chosen them):
  recent form       this season's games weighted by recency, half-life in days per stat; the
                    weights keep his total minutes the same, so only the balance between recent
                    and older games changes, not how much this season counts against last season
  penalty takers    xG split into open-play xG (his own rate) plus penalties: his share of his
                    team's penalties (from who took them before the cut-off) x the league's
                    penalties per match x 0.76 xG. In 'next', FPL's listed penalty taker is used.
"""
import os
import sys
import json
import urllib.request

import numpy as np
import pandas as pd

import player_minutes as pmin
from player_minutes import to_numbers

# stat: (W = last season's weight, K = minutes of position average), chosen on 2024-25 and
# confirmed on 2025-26. One W for all stats: shots on target, fouls, fouled and tackles have no
# 2023-24 data, so 2024-25 can't choose W for them; 0.4 sits between what xG, xA, shots and
# yellows chose (0.25-0.5). Reds are too rare for a player's own rate: position average only.
SETTINGS = {
    "xg":      (0.4, 200),
    "xa":      (0.4, 300),
    "shots":   (0.4, 200),
    "sot":     (0.4, 300),
    "fouls":   (0.4, 300),
    "fouled":  (0.4, 200),
    "tackles": (0.4, 300),
    "yellow":  (0.4, 600),
    "red":     (0.0, 5000),
}
PM_STATS = {"xg": "xg", "xa": "xa", "shots": "shots", "sot": "shots_on_target",
            "fouls": "fouls_committed", "fouled": "was_fouled", "tackles": "tackles",
            "goals": "goals", "assists": "assists"}
CARD_STATS = {"yellow": "yellow_cards", "red": "red_cards"}
RATE_STATS = list(SETTINGS)
# what each rate is tested against in the next match (xG predicts goals, xA predicts assists)
TEST_TARGET = {"xg": "goals", "xa": "assists", "shots": "shots", "sot": "sot", "fouls": "fouls",
               "fouled": "fouled", "tackles": "tackles", "yellow": "yellow", "red": "red"}


FORM_FILE = "player_rates_form.json"
FORM_GRID = [None, 365, 180, 90, 45]     # half-life in days (None = every game this season equal)
PEN_XG = 0.76                            # xG of one penalty
PEN_W = 1.0                              # weight of last season's penalties when finding the taker
FPL_PEN_SHARE = {1: 0.85, 2: 0.10, 3: 0.05}   # 'next': share by FPL's listed penalty order


def load_form():
    """{'hl': {stat: half-life or None}, 'pens': True/False} from 'form', or all off."""
    if os.path.exists(FORM_FILE):
        f = json.load(open(FORM_FILE))
        return {"hl": {k: f.get("hl", {}).get(k) for k in SETTINGS}, "pens": bool(f.get("pens", False))}
    return {"hl": {k: None for k in SETTINGS}, "pens": False}


def prev_season(s):
    y = int(s[:4]) - 1
    return f"{y}-{str(y + 1)[2:]}"


# ------------------------------------------------------------------ data
def load_extra(eng):
    test = os.environ.get("TEST_DIR")
    if test:
        rd = lambda n: pd.read_csv(os.path.join(test, n + ".csv"), low_memory=False)
        fpl, us = rd("pl_player_fpl"), rd("pl_player_understat")
        mins = pd.read_csv(os.path.join("player_data", "player_minutes.csv"))
    else:
        q = lambda s: to_numbers(pd.read_sql(s, eng))
        fpl = q("SELECT season, gameweek, match_date, player_code, minutes, yellow_cards, red_cards FROM pl_player_fpl")
        us = q("SELECT season, player_code, minutes, xg, xa, shots, goals, assists FROM pl_player_understat WHERE player_code IS NOT NULL")
        mins = q("SELECT * FROM player_minutes")
    fpl = fpl[fpl["minutes"] > 0].copy()
    fpl["match_date"] = pd.to_datetime(fpl["match_date"])
    us = us[us["player_code"].notna()].copy()
    us["player_code"] = us["player_code"].astype(int)
    return fpl, us, mins


def season_totals(pm, fpl, us):
    """Full-season totals per player (for 'last season'), by stat: count and minutes."""
    out = {}
    g = pm.groupby(["season", "player_code"])
    t = g[list(PM_STATS.values())].sum()
    t["minutes"] = g["minutes"].sum()
    for k, c in PM_STATS.items():
        out[k] = t[[c, "minutes"]].rename(columns={c: "n", "minutes": "m"})
    # Understat 2023-24 for xG, xA, shots, goals, assists (seasons FPL-Core-Insights doesn't have)
    u = us.groupby(["season", "player_code"])[["minutes", "xg", "xa", "shots", "goals", "assists"]].sum()
    for k in ("xg", "xa", "shots", "goals", "assists"):
        extra = u[[k, "minutes"]].rename(columns={k: "n", "minutes": "m"})
        extra = extra[~extra.index.isin(out[k].index)]
        out[k] = pd.concat([out[k], extra])
    c = fpl.groupby(["season", "player_code"])
    ct = c[["yellow_cards", "red_cards"]].sum()
    ct["minutes"] = c["minutes"].sum()
    for k, col in CARD_STATS.items():
        out[k] = ct[[col, "minutes"]].rename(columns={col: "n", "minutes": "m"})
    if "penalties" in pm.columns:          # open-play xG (Understat seasons: no penalty data, xG as is)
        pen = pd.to_numeric(pm["penalties"], errors="coerce").fillna(0).groupby([pm["season"], pm["player_code"]]).sum()
        npx = out["xg"].copy()
        pen = pen.reindex(npx.index).fillna(0)
        npx["n"] = (npx["n"] - PEN_XG * pen).clip(lower=0)
        out["npxg"] = npx
    return out


def recency(df, age_days, hl, cols):
    """Recency-weighted totals per player: each game weighted 0.5^(age/half-life), then rescaled
    so his minutes stay the same (only the balance between recent and older games changes)."""
    if hl is None or len(df) == 0:
        g = df.groupby("player_code")
        t = g[cols].sum()
        t["minutes"] = g["minutes"].sum()
        return t
    w = np.power(0.5, np.asarray(age_days, dtype=float) / hl)
    d = df[cols + ["minutes", "player_code"]].copy()
    wm = d["minutes"] * w
    scale = d["minutes"].groupby(d["player_code"]).transform("sum") / wm.groupby(d["player_code"]).transform("sum")
    scale = scale.replace([np.inf, -np.inf], np.nan).fillna(1.0)
    for c in cols:
        d[c] = d[c] * w * scale
    g = d.groupby("player_code")
    t = g[cols].sum()
    t["minutes"] = g["minutes"].sum()
    return t


def cur_totals(pm, fpl, season, cutoff, gw, exclude_ids=(), exclude_dates=(), hl=None):
    """This season up to the cutoff, per player, by stat (never the predicted matches).
    hl: half-life in days per stat (None = all games equal)."""
    hl = hl or {}
    out = {}
    cur = pm[(pm["season"] == season) & (pm["match_date"] <= cutoff) & ~pm["match_id"].isin(set(exclude_ids))]
    age = (pd.Timestamp(cutoff) - cur["match_date"]).dt.days.clip(lower=0)
    cols = list(PM_STATS.values())
    cache = {}
    for k, c in PM_STATS.items():
        h = hl.get(k) if k in hl else (hl.get("xg") if k == "goals" else hl.get("xa") if k == "assists" else None)
        if h not in cache:
            cache[h] = recency(cur, age, h, cols)
        out[k] = cache[h][[c, "minutes"]].rename(columns={c: "n", "minutes": "m"})
    if "penalties" in cur.columns:                       # open-play xG = xG - penalties x 0.76
        pc = cur.assign(penalties=pd.to_numeric(cur["penalties"], errors="coerce").fillna(0))
        t = recency(pc, age, hl.get("xg"), ["xg", "penalties"])
        out["npxg"] = pd.DataFrame({"n": (t["xg"] - PEN_XG * t["penalties"]).clip(lower=0), "m": t["minutes"]})
    f = fpl[fpl["season"] == season]
    if f["match_date"].notna().all():
        f = f[(f["match_date"] <= cutoff) & ~f.set_index(["player_code", "match_date"]).index.isin(list(exclude_dates))]
        fage = (pd.Timestamp(cutoff) - f["match_date"]).dt.days.clip(lower=0)
    else:
        f = f[f["gameweek"] <= gw]
        fage = (gw - f["gameweek"]) * 7.0 + 3.0
    for k, col in CARD_STATS.items():
        ct = recency(f, fage, hl.get(k), [col])
        out[k] = ct[[col, "minutes"]].rename(columns={col: "n", "minutes": "m"})
    return out


def penalty_parts(pm, season, cutoff, exclude_ids, teams):
    """Per player: penalties he took (this season to the cut-off + last season), his share of his
    current team's penalties, and the league's penalties per team-minute."""
    if "penalties" not in pm.columns:
        return None
    s1 = prev_season(season)
    h = pm[((pm["season"] == season) & (pm["match_date"] <= cutoff) & ~pm["match_id"].isin(set(exclude_ids)))
           | (pm["season"] == s1)].copy()
    h["penalties"] = pd.to_numeric(h["penalties"], errors="coerce").fillna(0)
    h["wt"] = np.where(h["season"] == season, 1.0, PEN_W)
    h["pw"] = h["penalties"] * h["wt"]
    # league rate: penalties per team-minute (each match has two teams x 90 minutes)
    team_min = h.groupby(["match_id", "team_id"])["minutes"].sum().clip(upper=90 * 11)
    n_team_games = len(team_min)
    lg_per_team_game = h["penalties"].sum() / max(n_team_games, 1)
    # his share of penalties for the team he plays for now
    by = h.groupby(["team_id", "player_code"])["pw"].sum()
    tot = h.groupby("team_id")["pw"].sum()
    share = {}
    for pc, tid in teams.items():
        t = tot.get(tid, 0.0)
        share[pc] = float(by.get((tid, pc), 0.0) / t) if t > 0 else 0.0
    return {"share": share, "lg_per_team_game": lg_per_team_game}


def fpl_penalty_order():
    """FPL's listed penalty order for the next gameweek, by player code (1 = first choice)."""
    try:
        req = urllib.request.Request(pmin.FPL_URL, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=60).read())
        return {e["code"]: e["penalties_order"] for e in data["elements"] if e.get("penalties_order")}
    except Exception as e:                                   # no internet etc: history only
        print(f"  (couldn't read FPL penalty takers: {e}; using who took them before)")
        return {}


def pieces(players_idx, positions, season, cur, tot):
    """For each stat: this-season, last-season, two-seasons-ago counts/minutes and position average."""
    s1, s2 = prev_season(season), prev_season(prev_season(season))
    res = {}
    for k in list(SETTINGS) + ["goals", "assists"] + (["npxg"] if "npxg" in cur else []):
        c = cur[k].reindex(players_idx).fillna(0)
        p1 = tot[k].xs(s1, level=0) if s1 in tot[k].index.get_level_values(0) else pd.DataFrame(columns=["n", "m"])
        p2 = tot[k].xs(s2, level=0) if s2 in tot[k].index.get_level_values(0) else pd.DataFrame(columns=["n", "m"])
        p1 = p1.reindex(players_idx).fillna(0)
        p2 = p2.reindex(players_idx).fillna(0) if k in CARD_STATS else p2.reindex(players_idx).fillna(0) * 0
        # position average from last season plus this season so far (all players)
        allp = pd.concat([cur[k], tot[k].xs(s1, level=0) if s1 in tot[k].index.get_level_values(0) else cur[k].iloc[0:0]])
        allp = allp.join(positions, how="left")
        pos_rate = (allp.groupby("position")["n"].sum() / allp.groupby("position")["m"].sum()).to_dict()
        lg = allp["n"].sum() / max(allp["m"].sum(), 1)
        pr = positions.reindex(players_idx).map(lambda p: pos_rate.get(p, lg)).fillna(lg).values
        res[k] = (c, p1, p2, pr)
    return res


def rate(piece, W, K):
    c, p1, p2, pr = piece
    num = c["n"].values + W * p1["n"].values + W * W * p2["n"].values + K * pr
    den = c["m"].values + W * p1["m"].values + W * W * p2["m"].values + K
    return 90 * num / den


def baseline(piece):
    c, p1, p2, pr = piece
    r = np.where(c["m"].values >= 90, c["n"].values / np.maximum(c["m"].values, 1),
                 np.where(p1["m"].values >= 90, p1["n"].values / np.maximum(p1["m"].values, 1), pr))
    return 90 * r


# ------------------------------------------------------------------ run
def build(mode, settings=None, only_seasons=None, keep_pieces=False, form=None, both_pens=False):
    """form: {'hl': {stat: half-life}, 'pens': bool} (default: player_rates_form.json).
    both_pens: also store xg_90 without the penalty split as xg_nopen_90 (for the 'form' test)."""
    settings = settings or SETTINGS
    form = form or load_form()
    use_db = not os.environ.get("TEST_DIR")
    eng = pmin.engine() if use_db else None
    pm, players, matches, tms = pmin.load(eng)
    fpl, us, mins = load_extra(eng)
    mins = mins[mins["mode"] == mode]
    if only_seasons:
        mins = mins[mins["season"].isin(only_seasons)]
    if len(mins) == 0:
        print(f"No '{mode}' rows in player_minutes. Run 'python player_minutes.py {mode}' first.")
        return None
    tot = season_totals(pm, fpl, us)
    dates = tms.groupby(["season", "round_no"])["match_date"].max()
    posmap = players.drop_duplicates(["season", "player_code"]).set_index(["season", "player_code"])["position"]

    # what happened in the predicted match (for testing only)
    act = pm[pm["match_id"].notna()][["match_id", "player_code", "match_date", "season", "minutes"]
                                     + list(PM_STATS.values())].copy()
    act["match_id"] = act["match_id"].astype(int)
    act = act.rename(columns={v: "act_" + k for k, v in PM_STATS.items()}).rename(columns={"minutes": "act_minutes"})
    cards = fpl[fpl["match_date"].notna()][["season", "player_code", "match_date", "yellow_cards", "red_cards"]]
    cards = cards.groupby(["season", "player_code", "match_date"]).sum().reset_index()
    cards = cards.rename(columns={"yellow_cards": "act_yellow", "red_cards": "act_red"})
    # one row per player per match on both sides, so the joins below can't repeat a player
    n = len(act)
    act = act.sort_values("act_minutes", ascending=False).drop_duplicates(["match_id", "player_code"])
    cards["match_date"] = pd.to_datetime(cards["match_date"])
    cards["player_code"] = cards["player_code"].astype(int)
    m = len(cards)
    cards = cards.groupby(["season", "player_code", "match_date"], as_index=False)[["act_yellow", "act_red"]].sum()
    if n - len(act) or m - len(cards):
        print(f"  (removed {n - len(act)} repeated player-match rows and {m - len(cards)} repeated card rows before joining)")

    out, keep = [], []
    cutoffs = {r[0]: r[3] for r in pmin.runs(matches, tms, mode)}
    for (label, season, gw), grp in mins.groupby(["run_label", "season", "gameweek"], sort=False):
        if label not in cutoffs:
            continue
        cutoff = cutoffs[label]
        ids = set(grp["match_id"])
        pred_rows = pm[pm["match_id"].isin(ids)]
        cur = cur_totals(pm, fpl, season, cutoff, gw, ids,
                         set(zip(pred_rows["player_code"], pred_rows["match_date"])), form["hl"])
        idx = pd.Index(grp["player_code"].unique(), name="player_code")
        positions = pd.Series(posmap.xs(season, level=0) if season in posmap.index.get_level_values(0) else {},
                              name="position").reindex(idx).fillna(grp.drop_duplicates("player_code").set_index("player_code")["position"])
        pcs = pieces(idx, positions, season, cur, tot)
        r = pd.DataFrame(index=idx)
        for k in SETTINGS:
            W, K = settings[k]
            r[k + "_90"] = rate(pcs[k], W, K)
            r["base_" + k + "_90"] = baseline(pcs[TEST_TARGET[k]] if k in ("xg", "xa") else pcs[k])
        if (form["pens"] or both_pens) and "npxg" in pcs:
            # penalty takers: open-play xG rate + his share of the team's penalties
            teams = grp.drop_duplicates("player_code").set_index("player_code")["team_id"].to_dict()
            pp = penalty_parts(pm, season, cutoff, ids, teams)
            share = pd.Series(pp["share"]).reindex(idx).fillna(0.0)
            if mode == "next":
                order = fpl_penalty_order()
                if order:
                    listed = pd.Series({pc: FPL_PEN_SHARE.get(order.get(pc), 0.0) for pc in idx})
                    has_list = pd.Series({pc: any(order.get(q) for q, t in teams.items() if t == teams[pc]) for pc in idx})
                    share = share.where(~has_list, listed)
            pen_part = share.values * pp["lg_per_team_game"] * PEN_XG
            with_pens = rate(pcs["npxg"], *settings["xg"]) + pen_part
            if both_pens:
                r["xg_nopen_90"] = r["xg_90"]
                r["xg_pens_90"] = with_pens
            if form["pens"]:
                r["xg_90"] = with_pens
        r["minutes_season"] = pcs["xg"][0]["m"].values
        r = r.reset_index()
        g = grp[["run_label", "season", "gameweek", "mode", "match_id", "team_id", "player_code", "position",
                 "p_start", "p_play", "exp_minutes"]].merge(r, on="player_code", how="left")
        if mode == "backtest":
            n0 = len(g)
            g["player_code"] = g["player_code"].astype(int)
            g = g.merge(act.drop(columns=["season"]), on=["match_id", "player_code"], how="left")
            n1 = len(g)
            played = g["match_date"].notna()
            g = pd.concat([g[played].merge(cards, on=["season", "player_code", "match_date"], how="left"),
                           g[~played]], ignore_index=True)
            if len(g) != n0:
                print(f"  {label}: the joins added {n1 - n0} (actual stats) and {len(g) - n1} (cards) rows")
            g = g.drop(columns=["match_date"])
        else:                                   # same columns, empty, so both modes share one table
            for c in ["act_minutes"] + ["act_" + k for k in PM_STATS] + ["act_yellow", "act_red"]:
                g[c] = np.nan
        out.append(g)
        if keep_pieces:
            keep.append((label, season, idx, pcs, grp))
    df = pd.concat(out, ignore_index=True)
    before = len(df)
    df = df.drop_duplicates(["run_label", "match_id", "player_code"])
    if len(df) != before:
        print(f"  Removed {before - len(df)} duplicate rows (one row per player per match per gameweek)")
    return (df, keep, act, cards) if keep_pieces else (df, eng)


def save(eng, df):
    os.makedirs("player_data", exist_ok=True)
    df.to_csv(os.path.join("player_data", "player_rates.csv"), index=False, encoding="utf-8-sig")
    if eng is None:
        return
    from sqlalchemy import text
    labels = list(df["run_label"].unique())
    text_cols = {"run_label", "season", "mode", "position"}
    for c in df.columns:                      # every other column is a number
        if c not in text_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    with eng.begin() as con:
        con.execute(text("DROP TABLE IF EXISTS player_rates_new"))
        df.head(0).to_sql("player_rates_new", con, index=False)
        exists = con.execute(text("SELECT to_regclass('player_rates')")).scalar()
        if exists:
            con.execute(text("DROP TABLE player_rates_new"))
            con.execute(text("DELETE FROM player_rates WHERE run_label = ANY(:l)"), {"l": labels})
        else:
            con.execute(text("ALTER TABLE player_rates_new RENAME TO player_rates"))
        df.to_sql("player_rates", con, if_exists="append", index=False, method="multi", chunksize=2000)


def score(df, col, target):
    """Brier of 'his chance of 1 or more' (Poisson, his actual minutes) per season, as in 15.3b."""
    num = lambda c: pd.to_numeric(df[c], errors="coerce").astype(float)
    mins, act, r = num("act_minutes"), num("act_" + target), num(col)
    ok = (mins.fillna(0) > 0) & act.notna() & r.notna()
    m = r[ok].clip(lower=0) * mins[ok] / 90
    b = ((1 - np.exp(-m)) - (act[ok] >= 1).astype(float)) ** 2
    t = df[ok]
    return b.groupby(t["season"]).mean().to_dict()


def form_test():
    """15.6x: choose a recent-form half-life per stat, and whether to split out penalties."""
    seasons = ["2024-25", "2025-26"]
    res = {}
    for hl in FORM_GRID:
        print(f"Building rates with half-life {hl or 'none (all games equal)'} ...")
        out = build("backtest", only_seasons=seasons, both_pens=True,
                    form={"hl": {k: hl for k in SETTINGS}, "pens": False})
        if out is None:
            return
        res[hl] = out[0]
    chosen, lines = {}, []
    print("\nRecent form: Brier of '1 or more' (lower = better), chosen on 2024-25, kept only if 2025-26 also improves")
    for k in SETTINGS:
        tgt = TEST_TARGET[k]
        sc = {hl: score(res[hl], k + "_90", tgt) for hl in FORM_GRID}
        best = min(FORM_GRID, key=lambda h: sc[h].get("2024-25", 9))
        keep = best is not None and sc[best].get("2025-26", 9) < sc[None].get("2025-26", 9)
        chosen[k] = best if keep else None
        row = "  ".join(f"{(str(h) + 'd') if h else 'equal'}: {sc[h].get('2024-25', np.nan):.4f}/{sc[h].get('2025-26', np.nan):.4f}"
                        for h in FORM_GRID)
        print(f"  {k:8s} {row}   -> {(str(chosen[k]) + ' days') if chosen[k] else 'all games equal'}")
    # penalties, at xG's chosen half-life
    d = res[chosen["xg"]]
    pens_on = False
    if "xg_pens_90" in d.columns:
        a, b = score(d, "xg_nopen_90", "goals"), score(d, "xg_pens_90", "goals")
        pens_on = all(b[s_] < a[s_] for s_ in seasons if s_ in a and s_ in b)
        print(f"\nPenalty takers (goals from xG): without {a.get('2024-25'):.4f}/{a.get('2025-26'):.4f}"
              f"  with {b.get('2024-25'):.4f}/{b.get('2025-26'):.4f}   -> {'used' if pens_on else 'not used'}")
        d = d.assign(xg_pens_90=pd.to_numeric(d["xg_pens_90"], errors="coerce"),
                     xg_nopen_90=pd.to_numeric(d["xg_nopen_90"], errors="coerce"))
        top = d[d["season"] == "2025-26"].assign(diff=d["xg_pens_90"] - d["xg_nopen_90"])
        top = top.sort_values("diff", ascending=False).drop_duplicates("player_code").head(8)
        print("  Biggest penalty boosts, 2025-26 (player code: xG per 90 without -> with):")
        print("   " + ", ".join(f"{int(r.player_code)}: {r.xg_nopen_90:.2f}->{r.xg_pens_90:.2f}" for r in top.itertuples()))
    else:
        print("\nPenalty takers: no 'penalties' column yet - rerun 'python player_data.py history' first.")
    json.dump({"hl": chosen, "pens": pens_on}, open(FORM_FILE, "w"), indent=1)
    print(f"\nSaved to {FORM_FILE}. Now rerun: python player_rates.py backtest, then python player_markets.py backtest")


def main(mode):
    if mode == "form":
        form_test()
        return
    f = load_form()
    on = {k: v for k, v in f["hl"].items() if v}
    print("Recent form: " + (", ".join(f"{k} {v} days" for k, v in on.items()) if on else "off (all games equal)")
          + f" | penalty takers: {'on' if f['pens'] else 'off'}")
    res = build(mode)
    if res is None:
        return
    df, eng = res
    num = df.select_dtypes("number").columns
    df[num] = df[num].round(5)
    save(eng, df)
    print(f"Saved {len(df)} player rows for {df['run_label'].nunique()} gameweeks to player_rates.")
    if mode == "backtest":
        played = df[df["act_minutes"].fillna(0) > 0]
        print(f"  {len(played)} of them played in the predicted match (these are the ones tested)")
    top = df.sort_values("xg_90", ascending=False).drop_duplicates("player_code").head(5)
    print("  Highest xG per 90 in the latest rows:", ", ".join(f"{c}: {v:.2f}" for c, v in zip(top["player_code"], top["xg_90"])))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("backtest", "next", "form"):
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
