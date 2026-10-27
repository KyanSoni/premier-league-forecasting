"""
Upgrade 25: the prop price log. Do the player props and card / corner markets you look at have an
edge, or not? Every price you consider goes in, with the model's chance at the time and what
happened, so the answer builds up week by week.

    python prop_log.py template   writes an empty prop_prices_new.csv to fill in
    python prop_log.py add        reads prop_prices_new.csv, finds each match, player and the model's
                                  chance (from the 'next' player_markets / match_markets rows), saves
                                  the rows to prop_prices and empties the file
    python prop_log.py settle     fills in the result for every logged price whose match is in your
                                  data (run after weekly.py part3, which updates the player data)
    python prop_log.py report     the scoreboard: model vs the bookmaker's price, hit rates, and the
                                  return on the prices the model flagged, with 95% intervals

Log prices BEFORE kick-off (the model's chance is taken from the current 'next' tables, so a row
added after the gameweek's markets were replaced gets no model chance).

One row of prop_prices_new.csv per price, e.g.
  logged_on,bookmaker,home_team,away_team,player,side,market,line,outcome,odds,stake
  2026-10-31,Paddy Power,Arsenal,Liverpool,Declan Rice,,fouls,1,over,8/11,0
  2026-10-31,Paddy Power,Arsenal,Liverpool,,match,corners,9.5,over,5/6,1
Player markets: line = the "or more" number (1 for "1+", 2 for "2+"). Team / match markets: side =
home, away or match and line = the bookmaker's line (9.5). Odds as decimal (1.73) or fractional (8/11,
evs). stake = what you actually bet (0 if you only looked).
"""
import os
import re
import sys
from datetime import date

import numpy as np
import pandas as pd

import quant_tools as qt

NEW_FILE = "prop_prices_new.csv"
COLS = ["logged_on", "bookmaker", "home_team", "away_team", "player", "side", "market", "line",
        "outcome", "odds", "stake"]
SEASON = qt.SEASONS[-1]
FLAG_EDGE = 0.05                 # same rule as the value log: model chance x odds - 1 >= 5%
MIN_SCORED = 150                 # settled prices with a model chance before the Brier verdict
MIN_FLAGGED = 100                # flagged prices before the return verdict
N_BOOT = 5000

PLAYER_MARKETS = {               # what you might type -> player_markets name
    "goals": "goals", "goal": "goals", "to score": "goals", "anytime": "goals", "scorer": "goals",
    "assists": "assists", "assist": "assists",
    "goal or assist": "goal_or_assist", "score or assist": "goal_or_assist", "goal_or_assist": "goal_or_assist",
    "shots": "shots", "shot": "shots",
    "sot": "sot", "shots on target": "sot", "shot on target": "sot",
    "fouls": "fouls", "fouls committed": "fouls", "foul": "fouls",
    "fouled": "fouled", "times fouled": "fouled", "was fouled": "fouled",
    "tackles": "tackles", "tackle": "tackles",
    "carded": "carded", "card": "carded", "to be carded": "carded", "booked": "carded",
    "saves": "saves", "save": "saves",
}
MATCH_MARKETS = {                # team / match markets -> match_markets name
    "corners": "corners", "corner": "corners",
    "fouls": "fouls", "cards": "cards", "card": "cards",
    "booking points": "booking_points", "booking_points": "booking_points", "bp": "booking_points",
    "red": "red", "red card": "red",
}
PLAYER_RESULT = {                # player_markets name -> pl_player_matches column(s)
    "goals": ["goals"], "assists": ["assists"], "goal_or_assist": ["goals", "assists"],
    "shots": ["shots"], "sot": ["shots_on_target"], "fouls": ["fouls_committed"],
    "fouled": ["was_fouled"], "tackles": ["tackles"], "saves": ["saves"],
}
DDL = """
CREATE TABLE IF NOT EXISTS prop_prices (
  id SERIAL PRIMARY KEY,
  logged_on DATE, bookmaker TEXT, match_id INT, kind TEXT,
  player_code INT DEFAULT 0, player_name TEXT, side TEXT DEFAULT '',
  market TEXT, line NUMERIC, outcome TEXT, odds NUMERIC, stake NUMERIC DEFAULT 0,
  model_prob NUMERIC, model_run TEXT, p_play NUMERIC,
  result NUMERIC, won BOOLEAN, void BOOLEAN, settled_at TIMESTAMP,
  UNIQUE (logged_on, bookmaker, match_id, kind, player_code, side, market, line, outcome));
CREATE OR REPLACE VIEW v_prop_log AS
SELECT p.logged_on, m.match_date, ht.team_name || ' v ' || at.team_name AS fixture,
       COALESCE(p.player_name, '') AS player, p.side, p.market, p.line, p.outcome, p.bookmaker, p.odds,
       ROUND(100 / p.odds, 1) AS implied_pct, ROUND(100 * p.model_prob, 1) AS model_pct,
       ROUND(100 * (p.model_prob * p.odds - 1), 1) AS edge_pct,
       CASE WHEN p.model_prob * p.odds - 1 >= 0.05 THEN 'FLAG' ELSE '' END AS flag,
       p.stake, p.result, p.won, p.void,
       CASE WHEN p.void THEN 0 WHEN p.won THEN p.stake * (p.odds - 1) WHEN NOT p.won THEN -p.stake END AS profit
FROM prop_prices p
JOIN matches m ON m.match_id = p.match_id
JOIN teams ht ON ht.team_id = m.home_team_id
JOIN teams at ON at.team_id = m.away_team_id
"""


# ------------------------------------------------------------------ helpers
def run_sql(sql, eng):
    from sqlalchemy import text
    with eng.begin() as con:
        for stmt in [s for s in sql.split(";") if s.strip()]:
            con.execute(text(stmt))


def to_decimal(o):
    """8/11 -> 1.727, evs -> 2.0, 1.73 -> 1.73."""
    s = str(o).strip().lower()
    if s in ("evs", "evens", "even", "1/1"):
        return 2.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if m:
        return 1 + float(m.group(1)) / float(m.group(2))
    v = float(s)
    if v <= 1:
        raise ValueError(f"odds {o!r} not understood")
    return v


def gw_of(label):
    m = re.search(r"GW(\d+)", str(label))
    return int(m.group(1)) if m else -1


def team_map(eng):
    names = qt.q("SELECT name, team_id FROM v_team_names", eng=eng)
    return {qt.plain(n): int(t) for n, t in names.itertuples(index=False)}


def name_keys(players):
    """The ways a player might be typed: FPL web name, full name, surname, last word of full name."""
    out = []
    for w, f, l in zip(players["web_name"], players["first_name"], players["second_name"]):
        full = qt.plain(f"{f} {l}")
        out.append({qt.plain(w), full, qt.plain(l), full.split()[-1] if full else ""} - {""})
    return out


def parse_date(v):
    s = str(v).strip()
    if s in ("", "nan", "NaT", "None"):
        return date.today()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return pd.to_datetime(s, format=fmt).date()
        except ValueError:
            pass
    raise ValueError(f"date {v!r} not understood (use YYYY-MM-DD)")


# ------------------------------------------------------------------ template / add
def template():
    if os.path.exists(NEW_FILE):
        raise SystemExit(f"{NEW_FILE} already exists; not overwritten.")
    pd.DataFrame(columns=COLS).to_csv(NEW_FILE, index=False)
    print(f"Wrote {NEW_FILE}. One row per price (see the top of prop_log.py for examples).")


def add(eng):
    if not os.path.exists(NEW_FILE):
        raise SystemExit(f"No {NEW_FILE}. Run: python prop_log.py template")
    s = pd.read_csv(NEW_FILE, dtype=str).dropna(how="all")
    if s.empty:
        raise SystemExit(f"{NEW_FILE} has no rows.")
    missing = [c for c in COLS if c not in s.columns]
    if missing:
        raise SystemExit(f"{NEW_FILE} is missing columns: {', '.join(missing)}")
    s = s.fillna("")
    run_sql(DDL, eng)
    tmap = team_map(eng)
    matches = qt.q("""SELECT m.match_id, m.home_team_id, m.away_team_id, m.match_date
                      FROM matches m JOIN seasons s USING (season_id) WHERE s.label = :s""",
                   {"s": SEASON}, eng=eng)
    mkey = {(int(r.home_team_id), int(r.away_team_id)): (int(r.match_id), pd.to_datetime(r.match_date).date())
            for r in matches.itertuples(index=False)}
    players = qt.q("""SELECT p.player_code, p.web_name, p.first_name, p.second_name, n.team_id
                      FROM pl_players p JOIN v_team_names n ON n.name = p.team
                      WHERE p.season = :s""", {"s": SEASON}, eng=eng)
    rows, problems = [], []
    for i, r in enumerate(s.itertuples(index=False), start=2):
        where = f"row {i} ({r.home_team} v {r.away_team} {r.player} {r.market})"
        try:
            h, a = tmap.get(qt.plain(r.home_team)), tmap.get(qt.plain(r.away_team))
            if h is None or a is None:
                raise ValueError("team name not recognised (add it to team_aliases)")
            if (h, a) not in mkey:
                raise ValueError(f"no {SEASON} match with these home / away teams")
            match_id, mdate = mkey[(h, a)]
            logged_on = parse_date(r.logged_on)
            odds = to_decimal(r.odds)
            stake = float(r.stake) if str(r.stake).strip() else 0.0
            outcome = (str(r.outcome).strip().lower() or "over")
            if outcome not in ("over", "under"):
                raise ValueError("outcome must be over or under")
            mk = str(r.market).strip().lower()
            line = float(r.line)
            if str(r.player).strip():
                market = PLAYER_MARKETS.get(mk)
                if market is None:
                    raise ValueError(f"player market {r.market!r} not known")
                k = int(np.floor(line)) + 1 if line != int(line) else int(line)   # 0.5 -> 1+
                cand = players[players["team_id"].isin([h, a])]
                pn = qt.plain(r.player)
                exact = cand[[pn in keys for keys in name_keys(cand)]]
                hit = exact if len(exact) else cand[[pn.split()[-1] in keys for keys in name_keys(cand)]]
                if len(hit) != 1:
                    raise ValueError(f"player {r.player!r} matched {len(hit)} players (use the FPL name)")
                pc = int(hit.iloc[0]["player_code"])
                prob, run, p_play = model_player(eng, match_id, pc, market, k, outcome)
                rows.append(dict(logged_on=logged_on, bookmaker=r.bookmaker.strip(), match_id=match_id,
                                 kind="player", player_code=pc, player_name=r.player.strip(), side="",
                                 market=market, line=k, outcome=outcome, odds=odds, stake=stake,
                                 model_prob=prob, model_run=run, p_play=p_play))
            else:
                market = MATCH_MARKETS.get(mk)
                side = str(r.side).strip().lower()
                if market is None:
                    raise ValueError(f"team / match market {r.market!r} not known")
                if side not in ("home", "away", "match"):
                    raise ValueError("side must be home, away or match")
                prob, run = model_match(eng, match_id, market, side, line, outcome)
                rows.append(dict(logged_on=logged_on, bookmaker=r.bookmaker.strip(), match_id=match_id,
                                 kind="match", player_code=0, player_name=None, side=side, market=market,
                                 line=line, outcome=outcome, odds=odds, stake=stake, model_prob=prob,
                                 model_run=run, p_play=None))
            if logged_on > mdate:
                problems.append(f"{where}: logged after the match date ({mdate}); saved, but leak_check.py will flag it")
        except Exception as e:                     # noqa: BLE001 - report every bad row, keep the good ones
            problems.append(f"{where}: {e}")
    if rows:
        qt.execute_many("""
            INSERT INTO prop_prices (logged_on, bookmaker, match_id, kind, player_code, player_name, side,
                                     market, line, outcome, odds, stake, model_prob, model_run, p_play)
            VALUES (:logged_on, :bookmaker, :match_id, :kind, :player_code, :player_name, :side,
                    :market, :line, :outcome, :odds, :stake, :model_prob, :model_run, :p_play)
            ON CONFLICT (logged_on, bookmaker, match_id, kind, player_code, side, market, line, outcome)
            DO UPDATE SET odds = EXCLUDED.odds, stake = EXCLUDED.stake,
                          model_prob = COALESCE(EXCLUDED.model_prob, prop_prices.model_prob),
                          model_run = COALESCE(EXCLUDED.model_run, prop_prices.model_run)""", rows, eng=eng)
    no_model = sum(r["model_prob"] is None for r in rows)
    print(f"Saved {len(rows)} prices to prop_prices ({no_model} without a model chance).")
    for p in problems:
        print("  CHECK", p)
    if rows and not problems:
        pd.DataFrame(columns=COLS).to_csv(NEW_FILE, index=False)
        print(f"{NEW_FILE} emptied, ready for the next prices.")
    elif problems:
        print(f"{NEW_FILE} NOT emptied: fix the rows above and run add again (saved rows just update).")


def model_player(eng, match_id, pc, market, k, outcome):
    d = qt.q("""SELECT run_label, prob, p_play FROM player_markets
                WHERE mode = 'next' AND match_id = :m AND player_code = :p AND market = :mk AND line = :k""",
             {"m": match_id, "p": pc, "mk": market, "k": k}, eng=eng)
    d = d.dropna(subset=["prob"])
    if d.empty:
        return None, None, None
    r = d.loc[d["run_label"].map(gw_of).idxmax()]
    p = float(r["prob"])
    return (p if outcome == "over" else 1 - p), r["run_label"], float(r["p_play"])


def model_match(eng, match_id, market, side, line, outcome):
    d = qt.q("""SELECT run_label, prob FROM match_markets
                WHERE mode = 'next' AND match_id = :m AND market = :mk AND side = :sd AND line = :l
                  AND outcome = 'over'""", {"m": match_id, "mk": market, "sd": side, "l": line}, eng=eng)
    d = d.dropna(subset=["prob"])
    if d.empty:
        return None, None
    r = d.loc[d["run_label"].map(gw_of).idxmax()]
    p = float(r["prob"])
    return (p if outcome == "over" else 1 - p), r["run_label"]


# ------------------------------------------------------------------ settle
def settle(eng):
    run_sql(DDL, eng)
    open_ = qt.q("""SELECT p.id, p.match_id, p.kind, p.player_code, p.side, p.market, p.line, p.outcome,
                           m.match_date, s.label AS season
                    FROM prop_prices p JOIN matches m USING (match_id) JOIN seasons s USING (season_id)
                    WHERE p.settled_at IS NULL""", eng=eng)
    if open_.empty:
        print("Nothing to settle.")
        return
    ids = [int(x) for x in open_["match_id"].unique()]
    pm = qt.q("""SELECT match_id, player_code, minutes, goals, assists, shots, shots_on_target,
                        fouls_committed, was_fouled, tackles, saves
                 FROM v_player_match_ids WHERE match_id = ANY(:ids)""", {"ids": ids}, eng=eng)
    have_players = set(pm["match_id"].astype(int))
    cards = qt.q("""SELECT season, player_code, match_date, SUM(yellow_cards) AS y, SUM(red_cards) AS r
                    FROM pl_player_fpl WHERE season = ANY(:s) GROUP BY season, player_code, match_date""",
                 {"s": list(open_["season"].unique())}, eng=eng)
    tm = qt.q("""SELECT match_id, home_corners, away_corners, home_fouls, away_fouls, home_yellows,
                        away_yellows, home_reds, away_reds
                 FROM v_team_match_stats WHERE match_id = ANY(:ids)""", {"ids": ids}, eng=eng)
    tm = tm.set_index("match_id")
    out, waiting = [], 0
    for r in open_.itertuples(index=False):
        res, void = None, False
        if r.kind == "player":
            if int(r.match_id) not in have_players:
                waiting += 1
                continue
            p = pm[(pm["match_id"] == r.match_id) & (pm["player_code"] == r.player_code)]
            if p.empty or float(p["minutes"].fillna(0).iloc[0]) <= 0:
                void = True                       # didn't play: void, like the bookmakers' rule
            elif r.market == "carded":
                c = cards[(cards["player_code"] == r.player_code) &
                          (pd.to_datetime(cards["match_date"]).dt.date == pd.to_datetime(r.match_date).date())]
                if c.empty:
                    waiting += 1
                    continue
                res = float(c["y"].iloc[0] or 0) + float(c["r"].iloc[0] or 0)
            else:
                res = float(sum(pd.to_numeric(p[c], errors="coerce").fillna(0).iloc[0]
                                for c in PLAYER_RESULT[r.market]))
            won = None if void else (res >= r.line if r.outcome == "over" else res < r.line)
        else:
            if int(r.match_id) not in tm.index:
                waiting += 1
                continue
            t = tm.loc[int(r.match_id)]
            val = {
                "corners": (t.home_corners, t.away_corners),
                "fouls": (t.home_fouls, t.away_fouls),
                "cards": (t.home_yellows + t.home_reds, t.away_yellows + t.away_reds),
                "booking_points": (10 * t.home_yellows + 25 * t.home_reds, 10 * t.away_yellows + 25 * t.away_reds),
                "red": (t.home_reds, t.away_reds),
            }[r.market]
            res = float({"home": val[0], "away": val[1], "match": val[0] + val[1]}[r.side])
            won = res > r.line if r.outcome == "over" else res < r.line
        out.append({"id": int(r.id), "result": res, "won": None if won is None else bool(won), "void": void})
    if out:
        qt.execute_many("""UPDATE prop_prices SET result = :result, won = CAST(:won AS BOOLEAN), void = CAST(:void AS BOOLEAN), settled_at = now()
                           WHERE id = :id""", out, eng=eng)
    print(f"Settled {len(out)} prices ({sum(o['void'] for o in out)} void: player didn't play); "
          f"{waiting} still waiting for match data (run weekly.py part3, then settle again).")


# ------------------------------------------------------------------ report
def cluster_boot(values, clusters, rng):
    """Mean and 95% interval, redrawing whole matches (prices in one match are linked)."""
    g = pd.DataFrame({"v": values, "c": clusters}).groupby("c")["v"].agg(["sum", "count"])
    s, n = g["sum"].to_numpy(), g["count"].to_numpy()
    if len(g) < 5:
        return s.sum() / n.sum(), np.nan, np.nan
    idx = rng.integers(0, len(g), size=(N_BOOT, len(g)))
    means = s[idx].sum(1) / n[idx].sum(1)
    return s.sum() / n.sum(), *np.percentile(means, [2.5, 97.5])


def report(eng):
    run_sql(DDL, eng)
    d = qt.q("""SELECT p.*, m.match_date FROM prop_prices p JOIN matches m USING (match_id)""", eng=eng)
    for c in ("won", "void"):                     # true / false / empty, whatever the driver returns
        d[c] = d[c].map(lambda v: None if pd.isna(v) else str(v).strip().lower() in ("true", "t", "1"))
    print(f"\nprop_prices: {len(d)} prices logged, {int(d['settled_at'].notna().sum())} settled, "
          f"{int((d['void'] == True).sum())} void, {int(d['model_prob'].isna().sum())} without a model chance")
    s = d[(d["won"].notna()) & (d["void"] != True) & d["model_prob"].notna()].copy()
    if s.empty:
        print("Nothing settled with a model chance yet.")
        return
    rng = np.random.default_rng(2026)
    s["y"] = s["won"].astype(bool).astype(float)
    s["implied"] = 1 / s["odds"].astype(float)
    s["model"] = s["model_prob"].astype(float)
    s["edge"] = s["model"] * s["odds"].astype(float) - 1
    s["group"] = np.where(s["kind"] == "player", "player " + s["market"], s["side"] + " " + s["market"])
    s["bs_model"] = (s["model"] - s["y"]) ** 2
    s["bs_book"] = (s["implied"].clip(upper=0.999) - s["y"]) ** 2

    print("\n1. How well each side priced them (Brier: lower is better). The bookmaker's chance is 1 / odds,")
    print("   margin included, so it is slightly too high; beating it here is the minimum, not proof of an edge.")
    rows = []
    for grp, g in [("ALL", s)] + list(s.groupby("group")):
        diff, lo, hi = cluster_boot((g["bs_model"] - g["bs_book"]).to_numpy(), g["match_id"].to_numpy(), rng)
        rows.append({"market": grp, "prices": len(g), "model %": round(100 * g["model"].mean(), 1),
                     "bookmaker %": round(100 * g["implied"].mean(), 1), "happened %": round(100 * g["y"].mean(), 1),
                     "Brier model": round(g["bs_model"].mean(), 4), "Brier bookmaker": round(g["bs_book"].mean(), 4),
                     "model - bookmaker": round(diff, 4),
                     "95% interval": "" if np.isnan(lo) else f"{lo:+.4f} to {hi:+.4f}"})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n2. Is the model's chance right? (all settled prices, by model chance)")
    bins = [0, 0.2, 0.4, 0.6, 0.8, 1.01]
    s["band"] = pd.cut(s["model"], bins, labels=["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"], right=False)
    cal = s.groupby("band", observed=True).agg(prices=("y", "size"), model=("model", "mean"),
                                               bookmaker=("implied", "mean"), happened=("y", "mean"))
    print((cal.assign(model=lambda t: (100 * t["model"]).round(1), bookmaker=lambda t: (100 * t["bookmaker"]).round(1),
                      happened=lambda t: (100 * t["happened"]).round(1))).to_string())

    print(f"\n3. The prices the model flagged (model chance x odds - 1 >= {FLAG_EDGE:.0%}), 1 unit each")
    f = s[s["edge"] >= FLAG_EDGE]
    rows = []
    for grp, g in [("ALL flagged", f)] + list(f.groupby("group")):
        if g.empty:
            continue
        profit = np.where(g["y"] == 1, g["odds"].astype(float) - 1, -1.0)
        roi, lo, hi = cluster_boot(profit, g["match_id"].to_numpy(), rng)
        rows.append({"market": grp, "flagged": len(g), "avg edge %": round(100 * g["edge"].mean(), 1),
                     "won %": round(100 * g["y"].mean(), 1), "needed %": round(100 * g["implied"].mean(), 1),
                     "return %": round(100 * roi, 1),
                     "95% interval": "" if np.isnan(lo) else f"{100 * lo:+.1f} to {100 * hi:+.1f}"})
    print(pd.DataFrame(rows).to_string(index=False) if rows else "  none flagged yet")
    staked = d[(d["stake"].astype(float) > 0) & d["won"].notna()]
    if len(staked):
        pr = np.where(staked["void"] == True, 0, np.where(staked["won"] == True,
                      staked["stake"].astype(float) * (staked["odds"].astype(float) - 1), -staked["stake"].astype(float)))
        print(f"\nYour actual bets: {len(staked)} settled, staked {staked['stake'].astype(float).sum():.2f}, "
              f"profit {pr.sum():+.2f}")

    print("\nVerdict")
    all_row = rows[0] if rows else None
    if len(s) < MIN_SCORED:
        print(f"- Pricing: {len(s)} of the {MIN_SCORED} settled prices needed. Keep logging.")
    else:
        diff, lo, hi = cluster_boot((s["bs_model"] - s["bs_book"]).to_numpy(), s["match_id"].to_numpy(), rng)
        if hi < 0:
            print("- Pricing: the model prices these markets better than the bookmaker's odds (interval below 0).")
        elif lo > 0:
            print("- Pricing: the bookmaker prices these markets better than the model (interval above 0).")
        else:
            print("- Pricing: no clear difference between the model and the bookmaker yet.")
    if all_row is None or all_row["flagged"] < MIN_FLAGGED:
        print(f"- Flagged prices: {0 if all_row is None else all_row['flagged']} of the {MIN_FLAGGED} needed "
              "before the return means anything. Stakes: zero or token.")
    else:
        roi, lo, hi = cluster_boot(np.where(f["y"] == 1, f["odds"].astype(float) - 1, -1.0), f["match_id"].to_numpy(), rng)
        if lo > 0:
            print("- Flagged prices: positive return, interval above 0. An edge: consider small Kelly stakes (Upgrade 21 rules).")
        elif hi < 0:
            print("- Flagged prices: losing, interval below 0. No edge in these markets: stop betting them.")
        else:
            print("- Flagged prices: the interval still includes 0. No proven edge: keep stakes token.")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "template":
        return template()
    if cmd not in ("add", "settle", "report"):
        raise SystemExit("Usage: python prop_log.py template | add | settle | report")
    eng = qt.engine()
    {"add": add, "settle": settle, "report": report}[cmd](eng)


if __name__ == "__main__":
    main()
