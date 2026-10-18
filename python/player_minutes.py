"""
Upgrade 15.2: expected minutes for every player before every gameweek.

Put this file in the DC upgrade folder (next to player_data.py) and run in the VS Code terminal:

  python player_minutes.py backtest     every gameweek of 2024-25, 2025-26 and 2026-27 that has
                                        already been played, predicted from data before it
                                        (about 2-4 minutes), for testing
  python player_minutes.py next         the next gameweek (after the latest round with results in
                                        your matches table), using FPL's injury and suspension news

How it works, for each team before each gameweek:
  1. The squad = players who appeared for the team in its last 30 league matches
     (and are still at the club this season), so players back from injury are included.
  2. P(start) and P(sub) = how often he started / came on in those matches, each match counting
     70% as much as the one after it (the last 5 matches carry about 83% of the weight).
     Chosen on 2024-25, then confirmed on 2025-26: see 15.2 in the guide.
  3. Next gameweek only: both are multiplied by FPL's chance of playing (injured/suspended = 0).
  4. The team's P(start) values are scaled to add up to 11 (nobody above 97%).
  5. Minutes when he starts = average of his last 8 starts; minutes off the bench = average of
     his last 8 sub appearances (position averages when he has none).
  6. Expected minutes = P(start) x start minutes + P(sub) x sub minutes.
A simple baseline is stored alongside (his average minutes over the team's last 5 matches) so
29_upgrade15 block 15.2 can test whether the method beats it.
"""
import os
import sys
import json
import urllib.request

import numpy as np
import pandas as pd

WINDOW = 30                         # squad and starts from the team's last 30 matches...
RECENCY = 0.7 ** np.arange(WINDOW)  # ...each one counting 70% as much as the one after it
BASELINE_WINDOW = 5                 # baseline: average minutes over the last 5 matches
MAX_START = 0.97
LAST_N_MINUTES = 8
SUB_DEFAULT = 18.0
SEASONS = ["2024-25", "2025-26", "2026-27"]
FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"


# ------------------------------------------------------------------ data
def to_numbers(df):
    """Postgres NUMERIC columns arrive as Decimal objects; turn every numeric column into floats."""
    for c in df.columns:
        if df[c].dtype == object:
            vals = df[c].dropna()
            sample = list(vals.head(200))
            if any(isinstance(v, (bool, np.bool_)) for v in sample):
                continue                      # true/false columns (was_home, started) stay true/false
            if len(vals) and all(isinstance(v, (int, float)) or type(v).__name__ == "Decimal" for v in sample):
                df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def as_bool(v):
    """True/False from True, 'true', 't', 1, 1.0 ...; None when unknown."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("true", "t", "1", "1.0", "yes"):
        return True
    if s in ("false", "f", "0", "0.0", "no"):
        return False
    return None


def engine():
    from sqlalchemy import create_engine
    url = os.environ.get("PREM_DB_URL")
    if not url:
        import dc_model
        url = dc_model.DB_URL
    return create_engine(url)


def load(eng):
    """Raw tables. With TEST_DIR set, the same tables are read from CSV files (for testing)."""
    test = os.environ.get("TEST_DIR")
    if test:
        rd = lambda n: pd.read_csv(os.path.join(test, n + ".csv"), low_memory=False)
        pm, players, names = rd("pl_player_matches"), rd("pl_players"), rd("v_team_names")
        matches, tms = rd("matches"), rd("v_team_match_stats")
    else:
        q = lambda s: to_numbers(pd.read_sql(s, eng))
        pm = q("SELECT * FROM pl_player_matches")
        players = q("SELECT season, player_code, web_name, team, position FROM pl_players")
        names = q("SELECT name, team_id FROM v_team_names")
        matches = q("""SELECT m.match_id, s.label AS season, m.round_no, m.home_team_id, m.away_team_id,
                              m.home_goals, m.away_goals
                       FROM matches m JOIN seasons s USING (season_id)
                       WHERE s.label >= '2024-25'""")
        tms = q("SELECT season, match_id, round_no, match_date FROM v_team_match_stats WHERE match_id IS NOT NULL")

    name_id = dict(zip(names["name"], names["team_id"]))
    pm = pm.copy()
    pm["match_date"] = pd.to_datetime(pm["match_date"])
    pm["team_id"] = pm["team"].map(name_id)
    pm["opp_id"] = pm["opponent"].map(name_id)
    pm["started"] = pm["started"].map(as_bool).fillna(False).astype(bool)
    pm["was_home"] = pm["was_home"].map(as_bool)
    pm = pm[pm["was_home"].notna()].copy()   # a handful of rows with no side can't be matched
    pm["was_home"] = pm["was_home"].astype(bool)
    pm = pm[pm["team_id"].notna() & (pm["minutes"] > 0)]
    pm["team_id"] = pm["team_id"].astype(int)

    # link each player row to your match_id (season + home team + away team)
    key = matches.set_index(["season", "home_team_id", "away_team_id"])["match_id"]
    home = np.where(pm["was_home"], pm["team_id"], pm["opp_id"])
    away = np.where(pm["was_home"], pm["opp_id"], pm["team_id"])
    idx = pd.MultiIndex.from_arrays([pm["season"], home, away])
    pm["match_id"] = key.reindex(idx).values

    players = players.copy()
    players["team_id"] = players["team"].map(name_id)
    tms = tms.copy()
    tms["match_date"] = pd.to_datetime(tms["match_date"])
    return pm, players, matches, tms


# ------------------------------------------------------------------ the method
def position_defaults(pm, players):
    pos = players.drop_duplicates(["season", "player_code"]).set_index(["season", "player_code"])["position"]
    p = pm.join(pos, on=["season", "player_code"])
    starts = p[p["started"]].groupby("position")["minutes"].mean()
    return starts.to_dict()


def predict_round(pm, players, fixtures, season, cutoff, start_default, availability=None):
    """Expected minutes for every player in the given fixtures, from data up to 'cutoff'."""
    hist = pm[(pm["season"] < season) | ((pm["season"] == season) & (pm["match_date"] <= cutoff))]
    hist = hist[~hist["match_id"].isin(set(fixtures["match_id"]))]   # never the predicted matches
    cur_players = players[players["season"] == season]
    at_club = set(zip(cur_players["team_id"], cur_players["player_code"]))
    this_season = hist[hist["season"] == season]
    played_for = set(zip(this_season["team_id"], this_season["player_code"]))
    pos_of = cur_players.drop_duplicates("player_code").set_index("player_code")["position"]
    hist_sorted = hist.sort_values("match_date")
    # a player belongs to the club he most recently played for (handles transfers between clubs)
    latest_club = hist_sorted.groupby("player_code")["team_id"].last()

    # per player: last 8 starts and last 8 sub appearances (any club)
    st = hist_sorted[hist_sorted["started"]].groupby("player_code")["minutes"].apply(lambda s: s.tail(LAST_N_MINUTES).mean())
    sb = hist_sorted[~hist_sorted["started"]].groupby("player_code")["minutes"].apply(lambda s: s.tail(LAST_N_MINUTES).mean())

    out = []
    for fx in fixtures.itertuples(index=False):
        for team_id in (fx.home_team_id, fx.away_team_id):
            th = hist[hist["team_id"] == team_id]
            last = (th[["season", "match_date"]].drop_duplicates()
                    .sort_values("match_date", ascending=False).head(WINDOW).reset_index(drop=True))
            if len(last) == 0:
                continue
            w = pd.Series(RECENCY[:len(last)], index=pd.MultiIndex.from_frame(last))
            win = th.merge(last, on=["season", "match_date"])
            win = win[[(((team_id, c) in at_club) or ((team_id, c) in played_for))
                       and latest_club.get(c) == team_id for c in win["player_code"]]]
            if len(win) == 0:
                continue
            win["w"] = w.reindex(pd.MultiIndex.from_frame(win[["season", "match_date"]])).values
            tot = w.sum()
            g = win.groupby("player_code")
            p_start = g.apply(lambda d: d.loc[d["started"], "w"].sum() / tot)
            p_sub = g.apply(lambda d: d.loc[~d["started"], "w"].sum() / tot)
            last5 = last.head(BASELINE_WINDOW)
            b5 = win.merge(last5, on=["season", "match_date"])
            base = b5.groupby("player_code")["minutes"].sum() / len(last5)   # absences count as 0
            df = pd.DataFrame({"p_start": p_start, "p_sub": p_sub})
            df["baseline_minutes"] = base.reindex(df.index).fillna(0)
            bs = b5[b5["started"]].groupby("player_code").size() / len(last5)  # started share, last 5
            df["baseline_p_start"] = bs.reindex(df.index).fillna(0)
            df["availability"] = 1.0
            if availability is not None:
                df["availability"] = df.index.map(lambda c: availability.get(c, 1.0))
            df["p_start"] *= df["availability"]
            df["p_sub"] *= df["availability"]
            # scale starts to 11 per team. Each player's cap is MAX_START x his FPL chance, so the
            # rescale hands a doubtful player's starts to fit team-mates instead of giving them back
            # to him (fix after GW6: Isak at 25% had been scaled back up to a 94% start).
            cap = df["availability"] * MAX_START
            df["p_start"] = np.minimum(df["p_start"], cap)
            for _ in range(20):
                s = df["p_start"].sum()
                if s <= 0 or abs(s - 11) < 1e-6:
                    break
                free = df["p_start"] < cap - 1e-9
                need = 11 - df.loc[~free, "p_start"].sum()
                have = df.loc[free, "p_start"].sum()
                if have <= 0:
                    break
                df.loc[free, "p_start"] = np.minimum(df.loc[free, "p_start"] * need / have, cap[free])
            df["p_sub"] = np.maximum(np.minimum(df["p_sub"], df["availability"] - df["p_start"]), 0)
            pos = df.index.map(lambda c: pos_of.get(c, "Midfielder"))
            df["position"] = pos
            df["min_start"] = [st.get(c, start_default.get(p, 80.0)) for c, p in zip(df.index, pos)]
            df["min_sub"] = [sb.get(c, SUB_DEFAULT) for c in df.index]
            df["p_play"] = (df["p_start"] + df["p_sub"]).clip(upper=1)
            df["exp_minutes"] = df["p_start"] * df["min_start"] + df["p_sub"] * df["min_sub"]
            df["match_id"] = fx.match_id
            df["team_id"] = team_id
            out.append(df.reset_index())
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def fpl_availability():
    """FPL chance of playing next round, by player code (1.0 = fit)."""
    req = urllib.request.Request(FPL_URL, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())
    av = {}
    for e in data["elements"]:
        c = e.get("chance_of_playing_next_round")
        if e["status"] in ("i", "s", "u", "n") and c is None:
            c = 0
        av[e["code"]] = 1.0 if c is None else c / 100.0
    return av


def runs(matches, tms, mode):
    """(run_label, season, gw, cutoff_date, fixtures of gw+1) for backtest or the next gameweek.
    Cutoff = the day before the predicted round's first kick-off, so only matches already played
    are used (a postponed game from an earlier round, played later, never leaks in)."""
    played_dates = tms.dropna(subset=["match_id"]).set_index("match_id")["match_date"]
    latest = tms["match_date"].max()
    out = []
    for s in SEASONS:
        ms = matches[matches["season"] == s]
        scored = ms[ms["home_goals"].notna()]
        if len(scored) == 0:
            continue
        last_round = int(scored["round_no"].max())
        if mode == "backtest":
            gws = range(1, last_round)                # predict rounds 2..last_round (results known)
        else:
            if s != SEASONS[-1]:
                continue
            gws = [last_round]                        # predict the next, unplayed round
        for g in gws:
            fx = ms[ms["round_no"] == g + 1]
            if len(fx) == 0:
                continue
            d = played_dates.reindex(fx["match_id"]).dropna()
            if mode == "backtest":
                if len(d) == 0:
                    continue
                cutoff = d.min() - pd.Timedelta(days=1)
            else:
                cutoff = latest
            out.append((f"{s} after GW{g}", s, g, cutoff, fx))
    return out


def save(eng, df, labels):
    os.makedirs("player_data", exist_ok=True)
    df.to_csv(os.path.join("player_data", "player_minutes.csv"), index=False, encoding="utf-8-sig")
    if eng is None:
        return
    from sqlalchemy import text
    with eng.begin() as con:
        con.execute(text("""CREATE TABLE IF NOT EXISTS player_minutes (
            run_label TEXT, season TEXT, gameweek INT, mode TEXT, match_id INT, team_id INT,
            player_code INT, position TEXT, availability NUMERIC, p_start NUMERIC, p_sub NUMERIC,
            p_play NUMERIC, min_start NUMERIC, min_sub NUMERIC, exp_minutes NUMERIC,
            baseline_minutes NUMERIC, baseline_p_start NUMERIC, PRIMARY KEY (run_label, match_id, player_code))"""))
        con.execute(text("DELETE FROM player_minutes WHERE run_label = ANY(:l)"), {"l": list(labels)})
        df.to_sql("player_minutes", con, if_exists="append", index=False, method="multi", chunksize=2000)


def main(mode):
    use_db = not os.environ.get("TEST_DIR")
    eng = engine() if use_db else None
    pm, players, matches, tms = load(eng)
    start_default = position_defaults(pm, players)
    av = None
    if mode == "next":
        try:
            av = fpl_availability()
            print(f"FPL availability: {sum(v < 1 for v in av.values())} players doubtful or out")
        except Exception as e:
            print(f"Could not read FPL availability ({e}); using 100% for everyone")
    rl = runs(matches, tms, mode)
    if not rl:
        print("No gameweeks to predict. Are the results in your matches table?")
        return
    parts = []
    for label, s, g, cutoff, fx in rl:
        r = predict_round(pm, players, fx, s, cutoff, start_default, av)
        if len(r):
            r.insert(0, "mode", mode)
            r.insert(0, "gameweek", g)
            r.insert(0, "season", s)
            r.insert(0, "run_label", label)
            parts.append(r)
        print(f"  {label}: {len(fx)} fixtures, {len(r)} players, "
              f"{r['p_start'].sum() / max(len(fx) * 2, 1):.1f} expected starters per team" if len(r) else f"  {label}: none")
    df = pd.concat(parts, ignore_index=True)
    df = df.sort_values("exp_minutes", ascending=False).drop_duplicates(["run_label", "match_id", "player_code"])
    cols = ["run_label", "season", "gameweek", "mode", "match_id", "team_id", "player_code", "position",
            "availability", "p_start", "p_sub", "p_play", "min_start", "min_sub", "exp_minutes", "baseline_minutes", "baseline_p_start"]
    df = df[cols].round(4)
    save(eng, df, df["run_label"].unique())
    print(f"Saved {len(df)} rows for {df['run_label'].nunique()} gameweeks to player_minutes.")
    if mode == "next":
        top = df.sort_values("exp_minutes", ascending=False)
        doubt = df[df["availability"] < 1]
        print(f"Players flagged by FPL in these fixtures: {len(doubt)}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("backtest", "next"):
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
