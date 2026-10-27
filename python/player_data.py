"""
Upgrade 15.1: download the player and team match data and load it into prem_sims.

Put this file in the DC upgrade folder (next to dc_model.py) and run it in the VS Code terminal:

  python player_data.py history        once: every season (first run about 5-10 minutes)
  python player_data.py update         weekly: the current season only (about 1 minute)
  python player_data.py referees       writes referees_next.csv for the next gameweek; fill in the
                                       referee column from premierleague.com, save, then run:
  python player_data.py referees load  loads referees_next.csv into fixture_referees

Sources (all free):
  FPL-Core-Insights (GitHub)  2024-25 on : per-match player stats (shots, SoT, xG, xA, fouls,
                                           fouled, tackles, saves, minutes) and team match stats
  vaastav FPL archive (GitHub) 2022-23 to 2025-26 : FPL per-fixture minutes, starts, cards, saves,
                                           xG, xA (tackles from 2025-26); plus Understat 2023-24
                                           season totals (shots, xG, xA) for each player
  FPL-Core-Insights per-gameweek FPL file   2026-27 : the same FPL fields for the current season
  football-data.co.uk          2021-22 on : team corners, fouls, cards, shots and the referee

Every download is cached in player_data_cache/ (past seasons are never downloaded twice) and a
CSV copy of every table goes into player_data/ so you can open it in Excel.
Needs: pandas, sqlalchemy, psycopg2 (already installed for dc_model.py).
"""
import io
import os
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

import pandas as pd

# ------------------------------------------------------------------ settings
CURRENT_SEASON = "2026-27"
FCI_SEASONS = ["2024-25", "2025-26", "2026-27"]               # FPL-Core-Insights
FPL_ARCHIVE_SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]  # vaastav (per fixture)
UNDERSTAT_SEASONS = ["2023-24"]                                # season totals, the year before FCI
FD_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]

FCI_RAW = "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data"
VAA_RAW = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
FD_URL = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"

CACHE = "player_data_cache"
OUT = "player_data"

# FPL / FPL-Core-Insights team names -> the names your model uses (same map as fpl_availability.py)
FPL_TO_MODEL = {
    "Man City": "Manchester City",
    "Man Utd": "Manchester United",
    "Spurs": "Tottenham",
    "Nott'm Forest": "Nottingham Forest",
    "Hull": "Hull City",
    "Coventry City": "Coventry",
    "Ipswich Town": "Ipswich",
    "Leeds United": "Leeds",
    "Newcastle United": "Newcastle",
}
# Understat team names -> FPL names (only used to match Understat players to FPL players)
UNDERSTAT_TO_FPL = {
    "Manchester City": "Man City", "Manchester United": "Man Utd", "Tottenham": "Spurs",
    "Nottingham Forest": "Nott'm Forest", "Newcastle United": "Newcastle",
    "Wolverhampton Wanderers": "Wolves", "Sheffield United": "Sheffield Utd",
}
POSITIONS = {1: "Goalkeeper", 2: "Defender", 3: "Midfielder", 4: "Forward", 5: "Manager"}


# ------------------------------------------------------------------ helpers
def say(msg):
    print(msg, flush=True)


def fci_code(season):        # "2024-25" -> "2024-2025"
    return season[:4] + "-" + str(int(season[:4]) + 1)


def fd_code(season):         # "2024-25" -> "2425"
    return season[2:4] + season[5:7]


def fetch(url, cache_name=None, refresh=False):
    """Download url (bytes). Cached in player_data_cache/ unless refresh=True. None if not found.
    For testing, set LOCAL_MIRROR to a folder holding the two GitHub repos (fci/, vaa/)."""
    mirror = os.environ.get("LOCAL_MIRROR")
    if mirror:
        for base, folder in ((FCI_RAW, "fci/data"), (VAA_RAW, "vaa/data")):
            if url.startswith(base):
                path = os.path.join(mirror, folder, urllib.parse.unquote(url[len(base) + 1:]))
                return open(path, "rb").read() if os.path.exists(path) else None
        if url.startswith("https://www.football-data.co.uk"):
            path = os.path.join(mirror, "fd", url.rsplit("/", 2)[-2] + "_E0.csv")
            return open(path, "rb").read() if os.path.exists(path) else None
    path = os.path.join(CACHE, cache_name) if cache_name else None
    if path and os.path.exists(path) and not refresh:
        return open(path, "rb").read()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (prem_sims player_data.py)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 2:
                raise
            time.sleep(3)
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(3)
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(data)
    return data


def read_csv(url, cache_name=None, refresh=False, **kw):
    data = fetch(url, cache_name, refresh)
    if data is None or len(data.strip()) == 0:
        return None
    try:
        return pd.read_csv(io.BytesIO(data), **kw)
    except pd.errors.EmptyDataError:
        return None


def quote(path):
    return urllib.parse.quote(path)


def norm(name):
    """'Bruno Guimarães' -> 'bruno guimaraes' for matching names across sources."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace("-", " ").replace("'", "").replace("&#039;", "").split())


def to_date(col):
    """UTC kick-off times (several formats) -> the UK calendar date, as football-data uses."""
    t = pd.to_datetime(col, format="mixed", utc=True)
    return t.dt.tz_convert("Europe/London").dt.date


def model_team(name):
    return FPL_TO_MODEL.get(name, name)


# ------------------------------------------------------------------ FPL-Core-Insights
def fci_season(season, refresh):
    """Per-match player stats, team match stats and (current season) FPL gameweek stats."""
    code = fci_code(season)
    tag = f"fci_{season}"
    if season == "2024-25":
        players = read_csv(f"{FCI_RAW}/{code}/players/players.csv", f"{tag}/players.csv", refresh)
        teams = read_csv(f"{FCI_RAW}/{code}/teams/teams.csv", f"{tag}/teams.csv", refresh)
        pms = read_csv(f"{FCI_RAW}/{code}/playermatchstats/playermatchstats.csv", f"{tag}/pms.csv", refresh)
        matches = read_csv(f"{FCI_RAW}/{code}/matches/matches.csv", f"{tag}/matches.csv", refresh)
        fplgw = None
    else:
        players = read_csv(f"{FCI_RAW}/{code}/players.csv", f"{tag}/players.csv", refresh)
        teams = read_csv(f"{FCI_RAW}/{code}/teams.csv", f"{tag}/teams.csv", refresh)
        pms_parts, match_parts, gw_parts = [], [], []
        for gw in range(1, 39):
            base = f"{FCI_RAW}/{code}/" + quote(f"By Tournament/Premier League/GW{gw}")
            m = read_csv(f"{base}/matches.csv", f"{tag}/GW{gw}_matches.csv", refresh)
            if m is None or len(m) == 0:
                continue
            m = m[m["finished"].astype(str).str.lower() == "true"]
            if len(m) == 0:
                continue                      # future gameweek: nothing played yet
            match_parts.append(m)
            p = read_csv(f"{base}/playermatchstats.csv", f"{tag}/GW{gw}_pms.csv", refresh)
            if p is not None:
                pms_parts.append(p)
            if season == CURRENT_SEASON:
                g = read_csv(f"{base}/player_gameweek_stats.csv", f"{tag}/GW{gw}_fplgw.csv", refresh)
                if g is not None:
                    g["gw"] = gw
                    gw_parts.append(g)
        matches = pd.concat(match_parts, ignore_index=True) if match_parts else None
        pms = pd.concat(pms_parts, ignore_index=True) if pms_parts else None
        fplgw = pd.concat(gw_parts, ignore_index=True) if gw_parts else None
    if matches is None or pms is None or players is None or teams is None:
        say(f"  FPL-Core-Insights {season}: no data found")
        return None, None, None, None

    matches = matches[matches["finished"].astype(str).str.lower() == "true"].drop_duplicates("match_id")
    team_name = {int(r.code): model_team(r["name"]) for _, r in teams.iterrows()}
    team_short = dict(zip(teams["code"].astype(int), teams["name"]))

    # ---- team match stats (backup / cross-check for football-data)
    tm = pd.DataFrame({
        "season": season,
        "gameweek": matches["gameweek"].astype(int),
        "match_date": to_date(matches["kickoff_time"]),
        "home_team": matches["home_team"].astype(int).map(team_name),
        "away_team": matches["away_team"].astype(int).map(team_name),
        "home_goals": matches["home_score"], "away_goals": matches["away_score"],
        "home_shots": matches["home_total_shots"], "away_shots": matches["away_total_shots"],
        "home_sot": matches["home_shots_on_target"], "away_sot": matches["away_shots_on_target"],
        "home_corners": matches["home_corners"], "away_corners": matches["away_corners"],
        "home_fouls": matches["home_fouls_committed"], "away_fouls": matches["away_fouls_committed"],
        "home_yellows": matches["home_yellow_cards"], "away_yellows": matches["away_yellow_cards"],
        "home_reds": matches["home_red_cards"], "away_reds": matches["away_red_cards"],
        "home_saves": matches["home_keeper_saves"], "away_saves": matches["away_keeper_saves"],
        "home_xg": matches["home_expected_goals_xg"], "away_xg": matches["away_expected_goals_xg"],
        "fci_match_id": matches["match_id"],
    })

    # ---- players of the season
    pl = players.copy()
    pl["season"] = season
    pl["team"] = pl["team_code"].map(lambda c: team_name.get(int(c)) if pd.notna(c) else None)
    pl = pl[pl["position"] != "Unknown"]
    pl_out = pl[["season", "player_code", "player_id", "first_name", "second_name", "web_name",
                 "team", "position"]].rename(columns={"player_id": "fpl_id"})

    # ---- per-match player stats, with each player's side worked out
    # 2026-27: the 'tackles' column is empty and 'tackles_won' holds the total (it matches FPL's
    # tackle count), so use whichever column has the data
    if "tackles_won" in pms.columns and pd.to_numeric(pms.get("tackles"), errors="coerce").fillna(0).sum() == 0:
        pms = pms.copy()
        pms["tackles"] = pms["tackles_won"]
        say(f"  FPL-Core-Insights {season}: 'tackles' is empty, using 'tackles_won' (matches FPL's tackle count)")
    p = pms[pms["match_id"].isin(matches["match_id"])].merge(
        players[["player_id", "player_code", "team_code"]], on="player_id", how="left")
    mm = matches.set_index("match_id")
    rows = []
    for r in p.itertuples(index=False):
        m = mm.loc[r.match_id]
        home, away = int(m["home_team"]), int(m["away_team"])
        side = None
        if pd.notna(r.team_code) and int(r.team_code) == home:
            side = "H"
        elif pd.notna(r.team_code) and int(r.team_code) == away:
            side = "A"
        else:   # moved club since: the goals his team conceded tells us which side he was on
            if m["home_score"] != m["away_score"] and pd.notna(r.team_goals_conceded):
                side = "H" if r.team_goals_conceded == m["away_score"] else "A"
        rows.append(side)
    p["side"] = rows
    p = p.merge(tm[["fci_match_id", "gameweek", "match_date", "home_team", "away_team"]],
                left_on="match_id", right_on="fci_match_id")
    p["team"] = p.apply(lambda r: r.home_team if r.side == "H" else (r.away_team if r.side == "A" else None), axis=1)
    p["opponent"] = p.apply(lambda r: r.away_team if r.side == "H" else (r.home_team if r.side == "A" else None), axis=1)
    p["was_home"] = p["side"].map({"H": True, "A": False})
    pm = pd.DataFrame({
        "season": season, "gameweek": p["gameweek"], "match_date": p["match_date"],
        "fci_match_id": p["match_id"], "player_code": p["player_code"], "team": p["team"],
        "opponent": p["opponent"], "was_home": p["was_home"],
        "minutes": p["minutes_played"],
        "started": None, "goals": p["goals"], "assists": p["assists"], "xg": p["xg"], "xa": p["xa"],
        "shots": p["total_shots"], "shots_on_target": p["shots_on_target"],
        "fouls_committed": p["fouls_committed"], "was_fouled": p["was_fouled"],
        "tackles": p["tackles"], "saves": p["saves"],
        # penalties taken (scored + missed): who takes them, for 15.6x
        "penalties": pd.to_numeric(p.get("penalties_scored"), errors="coerce").fillna(0)
                     + pd.to_numeric(p.get("penalties_missed"), errors="coerce").fillna(0),
    })
    unmatched = pm["player_code"].isna().sum()
    pm = pm[pm["player_code"].notna() & (pm["minutes"].fillna(0) > 0)]
    pm["player_code"] = pm["player_code"].astype(int)
    # Upgrade 24: from 2026-27 FPL-Core-Insights leaves these blank instead of 0 for players who
    # played; a blank was dropped from the backtests, so store 0
    for c in ("goals", "assists", "shots", "shots_on_target", "fouls_committed", "was_fouled", "tackles", "saves"):
        pm[c] = pd.to_numeric(pm[c], errors="coerce").fillna(0)

    # ---- current season FPL gameweek stats (cards etc.)
    fpl = None
    if fplgw is not None:
        ids = dict(zip(players["player_id"], players["player_code"]))
        teamof = dict(zip(players["player_id"], players["team_code"]))
        fpl = pd.DataFrame({
            "season": season, "gameweek": fplgw["gw"], "fixture": None, "match_date": None,
            "player_code": fplgw["id"].map(ids),
            "team": fplgw["id"].map(teamof).map(lambda c: team_name.get(int(c)) if pd.notna(c) else None),
            "opponent": None, "was_home": None,
            "minutes": fplgw["minutes"], "starts": fplgw["starts"],
            "goals": fplgw["goals_scored"], "assists": fplgw["assists"],
            "xg": fplgw["expected_goals"], "xa": fplgw["expected_assists"],
            "saves": fplgw["saves"], "yellow_cards": fplgw["yellow_cards"],
            "red_cards": fplgw["red_cards"], "tackles": fplgw.get("tackles"),
        })
        fpl = fpl[fpl["player_code"].notna() & (fpl["minutes"].fillna(0) > 0)]
        fpl["player_code"] = fpl["player_code"].astype(int)

    say(f"  FPL-Core-Insights {season}: {len(tm)} matches, {len(pm)} player-match rows"
        f" ({unmatched} rows with no player id dropped, {pm['team'].isna().sum()} with no side)")
    return pm, tm, pl_out, fpl


# ------------------------------------------------------------------ vaastav FPL archive
def fpl_archive_season(season, refresh):
    tag = f"vaa_{season}"
    gw = read_csv(f"{VAA_RAW}/{season}/gws/merged_gw.csv", f"{tag}/merged_gw.csv", refresh)
    raw = read_csv(f"{VAA_RAW}/{season}/players_raw.csv", f"{tag}/players_raw.csv", refresh)
    teams = read_csv(f"{VAA_RAW}/{season}/teams.csv", f"{tag}/teams.csv", refresh)
    if gw is None or raw is None or teams is None:
        say(f"  FPL archive {season}: not found")
        return None, None
    code = dict(zip(raw["id"], raw["code"]))
    tname = dict(zip(teams["id"], teams["name"]))
    df = pd.DataFrame({
        "season": season, "gameweek": gw["GW"], "fixture": gw["fixture"],
        "match_date": to_date(gw["kickoff_time"]),
        "player_code": gw["element"].map(code),
        "team": gw["team"].map(model_team),
        "opponent": gw["opponent_team"].map(tname).map(model_team),
        "was_home": gw["was_home"].astype(str).str.lower() == "true",
        "minutes": gw["minutes"], "starts": gw.get("starts"),
        "goals": gw["goals_scored"], "assists": gw["assists"],
        "xg": gw.get("expected_goals"), "xa": gw.get("expected_assists"),
        "saves": gw["saves"], "yellow_cards": gw["yellow_cards"], "red_cards": gw["red_cards"],
        "tackles": gw.get("tackles"),
    })
    df = df[df["player_code"].notna() & (df["minutes"] > 0)]
    df["player_code"] = df["player_code"].astype(int)
    players = pd.DataFrame({
        "season": season, "player_code": raw["code"], "fpl_id": raw["id"],
        "first_name": raw["first_name"], "second_name": raw["second_name"], "web_name": raw["web_name"],
        "team": raw["team"].map(tname).map(model_team), "position": raw["element_type"].map(POSITIONS),
    })
    players = players[players["position"] != "Manager"]
    say(f"  FPL archive {season}: {len(df)} player-fixture rows, {df['yellow_cards'].sum():.0f} yellows,"
        f" {df['red_cards'].sum():.0f} reds")
    return df, players


def understat_season(season, fpl_players, refresh):
    """Understat season totals, matched to FPL player codes by name (and club where possible)."""
    us = read_csv(f"{VAA_RAW}/{season}/understat/understat_player.csv", f"vaa_{season}/understat_player.csv", refresh)
    if us is None:
        say(f"  Understat {season}: not found")
        return None
    fp = fpl_players.copy()
    fp["full"] = (fp["first_name"] + " " + fp["second_name"]).map(norm)
    fp["web"] = fp["web_name"].map(norm)
    fp["second"] = fp["second_name"].map(norm)
    model_to_fpl = {v: k for k, v in FPL_TO_MODEL.items()}
    fp["fpl_team"] = fp["team"].map(lambda t: model_to_fpl.get(t, t))
    by_full = fp.drop_duplicates("full").set_index("full")["player_code"]
    by_web = fp.drop_duplicates("web").set_index("web")["player_code"]

    codes = []
    for r in us.itertuples(index=False):
        n = norm(r.player_name)
        clubs = {UNDERSTAT_TO_FPL.get(t.strip(), t.strip()) for t in str(r.team_title).split(",")}
        c = by_full.get(n)
        if c is None:
            c = by_web.get(n)
        if c is None:   # last name + club, e.g. 'Gabriel Magalhães' vs 'Gabriel dos Santos Magalhães'
            last = n.split(" ")[-1]
            cand = fp[(fp["full"].str.endswith(" " + last) | (fp["web"] == last)) & fp["fpl_team"].isin(clubs)]
            if len(cand) == 1:
                c = cand["player_code"].iloc[0]
        codes.append(c)
    us["player_code"] = codes
    out = pd.DataFrame({
        "season": season, "understat_id": us["id"], "player_name": us["player_name"],
        "teams": us["team_title"], "player_code": us["player_code"], "games": us["games"],
        "minutes": us["time"], "goals": us["goals"], "xg": us["xG"], "assists": us["assists"],
        "xa": us["xA"], "shots": us["shots"], "key_passes": us["key_passes"],
        "yellow_cards": us["yellow_cards"], "red_cards": us["red_cards"],
    })
    matched = out.loc[out["player_code"].notna(), "minutes"].sum() / out["minutes"].sum()
    say(f"  Understat {season}: {len(out)} players, {matched:.1%} of minutes matched to FPL players")
    return out


# ------------------------------------------------------------------ football-data.co.uk
def football_data_season(season, refresh):
    df = read_csv(FD_URL.format(code=fd_code(season)), f"fd_{season}_E0.csv", refresh, encoding="latin-1")
    if df is None:
        say(f"  football-data {season}: not found")
        return None
    df = df[df["HomeTeam"].notna()]
    need = ["Referee", "HS", "AS", "HST", "AST", "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        say(f"  football-data {season}: WARNING missing columns {missing}")
    g = lambda c: df[c] if c in df.columns else None
    out = pd.DataFrame({
        "season": season,
        "match_date": pd.to_datetime(df["Date"], dayfirst=True).dt.date,
        "home_team": df["HomeTeam"].str.strip(), "away_team": df["AwayTeam"].str.strip(),
        "referee": g("Referee").str.strip() if "Referee" in df.columns else None,
        "home_goals": df["FTHG"], "away_goals": df["FTAG"],
        "home_shots": g("HS"), "away_shots": g("AS"), "home_sot": g("HST"), "away_sot": g("AST"),
        "home_fouls": g("HF"), "away_fouls": g("AF"), "home_corners": g("HC"), "away_corners": g("AC"),
        "home_yellows": g("HY"), "away_yellows": g("AY"), "home_reds": g("HR"), "away_reds": g("AR"),
    })
    say(f"  football-data {season}: {len(out)} matches, {out['referee'].nunique()} referees")
    return out


def add_fpl_side(pm, fpl):
    """FPL knows each player's club and whether he started in every fixture, so use it where it
    can be matched: by date (past seasons, per fixture) or by gameweek (current season)."""
    pm = pm.copy()
    pm["started"] = pd.NA
    if fpl is None:
        pm["started"] = pm["minutes"] >= 60
        return pm
    cols = ["team", "opponent", "was_home", "starts"]
    by_date = fpl[fpl["match_date"].notna()].drop_duplicates(["season", "player_code", "match_date"])
    a = pm.merge(by_date[["season", "player_code", "match_date"] + cols].rename(columns={c: "f_" + c for c in cols}),
                 on=["season", "player_code", "match_date"], how="left")
    one_fixture = fpl[fpl["match_date"].isna()].groupby(["season", "gameweek", "player_code"]).filter(lambda g: len(g) == 1)
    b = a.merge(one_fixture[["season", "gameweek", "player_code", "team", "starts"]]
                .rename(columns={"team": "g_team", "starts": "g_starts"}),
                on=["season", "gameweek", "player_code"], how="left")
    starts = b["f_starts"].fillna(b["g_starts"])
    b["started"] = (starts > 0).where(starts.notna(), b["minutes"] >= 60)
    fpl_team = b["f_team"].fillna(b["g_team"])
    # if FPL's club is one of the two sides, trust it (it handles players who moved club)
    use = fpl_team.notna() & ((fpl_team == b["team"]) | (fpl_team == b["opponent"]))
    flip = use & (fpl_team == b["opponent"])
    b.loc[flip, ["team", "opponent"]] = b.loc[flip, ["opponent", "team"]].values
    b.loc[flip, "was_home"] = ~b.loc[flip, "was_home"].astype(bool)
    known = b["f_was_home"].notna()
    b.loc[known, "was_home"] = b.loc[known, "f_was_home"].astype(bool)
    b.loc[known, "team"] = b.loc[known, "f_team"]
    b.loc[known, "opponent"] = b.loc[known, "f_opponent"]
    return b[pm.columns]


# ------------------------------------------------------------------ database
DDL = """
CREATE TABLE IF NOT EXISTS pl_players (
  season TEXT, player_code INT, fpl_id INT, first_name TEXT, second_name TEXT, web_name TEXT,
  team TEXT, position TEXT, PRIMARY KEY (season, player_code));
CREATE TABLE IF NOT EXISTS pl_player_matches (
  season TEXT, gameweek INT, match_date DATE, fci_match_id TEXT, player_code INT, team TEXT,
  opponent TEXT, was_home BOOLEAN, minutes INT, started BOOLEAN, goals INT, assists INT,
  xg NUMERIC, xa NUMERIC, shots INT, shots_on_target INT, fouls_committed INT, was_fouled INT,
  tackles INT, saves INT, PRIMARY KEY (fci_match_id, player_code));
ALTER TABLE pl_player_matches ADD COLUMN IF NOT EXISTS penalties INT;
CREATE TABLE IF NOT EXISTS pl_player_fpl (
  season TEXT, gameweek INT, fixture INT, match_date DATE, player_code INT, team TEXT,
  opponent TEXT, was_home BOOLEAN, minutes INT, starts INT, goals INT, assists INT,
  xg NUMERIC, xa NUMERIC, saves INT, yellow_cards INT, red_cards INT, tackles INT);
CREATE TABLE IF NOT EXISTS pl_player_understat (
  season TEXT, understat_id INT, player_name TEXT, teams TEXT, player_code INT, games INT,
  minutes INT, goals INT, xg NUMERIC, assists INT, xa NUMERIC, shots INT, key_passes INT,
  yellow_cards INT, red_cards INT, PRIMARY KEY (season, understat_id));
CREATE TABLE IF NOT EXISTS team_match_stats (
  source TEXT, season TEXT, gameweek INT, match_date DATE, home_team TEXT, away_team TEXT,
  referee TEXT, home_goals INT, away_goals INT, home_shots INT, away_shots INT, home_sot INT,
  away_sot INT, home_fouls INT, away_fouls INT, home_corners INT, away_corners INT,
  home_yellows INT, away_yellows INT, home_reds INT, away_reds INT, home_saves INT,
  away_saves INT, home_xg NUMERIC, away_xg NUMERIC, fci_match_id TEXT);
CREATE TABLE IF NOT EXISTS fixture_referees (
  season TEXT, gameweek INT, home_team TEXT, away_team TEXT, referee TEXT,
  PRIMARY KEY (season, home_team, away_team));
"""


def engine():
    from sqlalchemy import create_engine
    url = os.environ.get("PREM_DB_URL")
    if not url:
        import dc_model                      # same connection as your other scripts
        url = dc_model.DB_URL
    return create_engine(url)


def save(eng, table, df, seasons, extra_where="", csv_name=None):
    """Replace the given seasons of a table with df, and write a CSV copy."""
    os.makedirs(OUT, exist_ok=True)
    df = df.copy()
    for c in ("started", "was_home"):        # true/false columns stay true/false
        if c in df.columns:
            df[c] = df[c].astype("boolean")
    for c in df.columns:
        if str(df[c].dtype) == "boolean":
            continue                     # whole-number columns stored as 90 rather than 90.0
        if df[c].dtype == object and c not in ("match_date",):
            num = pd.to_numeric(df[c], errors="coerce")
            if num.notna().sum() == df[c].notna().sum() and df[c].notna().any():
                df[c] = num
        if df[c].dtype.kind == "f" and c not in ("xg", "xa", "home_xg", "away_xg"):
            v = df[c].dropna()
            if len(v) == 0 or (v == v.round()).all():
                df[c] = df[c].round().astype("Int64")
    name = csv_name or table
    df.to_csv(os.path.join(OUT, f"{name}.csv" if len(seasons) > 1 else f"{name}_{seasons[0]}.csv"),
              index=False, encoding="utf-8-sig")
    if eng is None:
        return
    from sqlalchemy import text
    with eng.begin() as con:
        con.execute(text(f"DELETE FROM {table} WHERE season = ANY(:s) {extra_where}"), {"s": list(seasons)})
        df.to_sql(table, con, if_exists="append", index=False, method="multi", chunksize=2000)
    say(f"  -> {table}: {len(df)} rows for {', '.join(seasons)}")


# ------------------------------------------------------------------ commands
def run(mode, use_db=True):
    refresh_current = True
    if mode == "history":
        fci_s, fpl_s, us_s, fd_s = FCI_SEASONS, FPL_ARCHIVE_SEASONS, UNDERSTAT_SEASONS, FD_SEASONS
    else:                                    # update: current season only
        fci_s, fpl_s, us_s, fd_s = [CURRENT_SEASON], [], [], [CURRENT_SEASON]

    eng = engine() if use_db else None
    if eng is not None:
        from sqlalchemy import text
        with eng.begin() as con:
            for stmt in DDL.split(";"):
                if stmt.strip():
                    con.execute(text(stmt))

    pm_all, tm_all, pl_all, fpl_all = [], [], [], []
    say("FPL-Core-Insights (per-match player stats):")
    for s in fci_s:
        pm, tm, pl, fpl = fci_season(s, refresh=(s == CURRENT_SEASON and refresh_current))
        if pm is not None:
            pm_all.append(pm); tm_all.append(tm); pl_all.append(pl)
        if fpl is not None:
            fpl_all.append(fpl)

    if fpl_s:
        say("FPL archive (cards, minutes, starts):")
    arch_players = {}
    for s in fpl_s:
        df, pl = fpl_archive_season(s, refresh=False)
        if df is not None:
            fpl_all.append(df)
            arch_players[s] = pl

    us_all = []
    if us_s:
        say("Understat season totals:")
    for s in us_s:
        if s in arch_players:
            u = understat_season(s, arch_players[s], refresh=False)
            if u is not None:
                us_all.append(u)

    say("football-data.co.uk (team stats and referees):")
    fd_all = []
    for s in fd_s:
        df = football_data_season(s, refresh=(s == CURRENT_SEASON))
        if df is not None:
            fd_all.append(df)

    # players: FCI seasons first, archive seasons where FCI has none
    players = pd.concat(pl_all + [p for s, p in arch_players.items() if s not in fci_s], ignore_index=True) \
        if (pl_all or arch_players) else None

    say("Saving:")
    if players is not None:
        save(eng, "pl_players", players.drop_duplicates(["season", "player_code"]),
             sorted(players["season"].unique()))
    fpl = pd.concat(fpl_all, ignore_index=True) if fpl_all else None
    if pm_all:
        pm = pd.concat(pm_all, ignore_index=True).drop_duplicates(["fci_match_id", "player_code"])
        pm = add_fpl_side(pm, fpl)
        save(eng, "pl_player_matches", pm, sorted(pm["season"].unique()))
    if fpl is not None:
        save(eng, "pl_player_fpl", fpl, sorted(fpl["season"].unique()))
    if us_all:
        save(eng, "pl_player_understat", pd.concat(us_all, ignore_index=True), us_s)
    if tm_all:
        tm = pd.concat(tm_all, ignore_index=True)
        tm.insert(0, "source", "fci")
        save(eng, "team_match_stats", tm, sorted(tm["season"].unique()), "AND source = 'fci'", "team_match_stats_fci")
    if fd_all:
        fd = pd.concat(fd_all, ignore_index=True)
        fd.insert(0, "source", "football-data")
        save(eng, "team_match_stats", fd, sorted(fd["season"].unique()), "AND source = 'football-data'",
             "team_match_stats_football_data")

    # quick checks printed for you
    say("Checks:")
    if pm_all:
        for s, g in pm.groupby("season"):
            say(f"  {s}: {g['fci_match_id'].nunique()} matches, {g['player_code'].nunique()} players,"
                f" {g['goals'].sum()} player goals, {g['fouls_committed'].sum()} fouls, {g['was_fouled'].sum()} fouled")
    if fd_all:
        for s_, g in fd.groupby("season"):
            say(f"  football-data {s_}: {len(g)} matches, {g['referee'].notna().sum()} with a referee,"
                f" {(g['home_corners'] + g['away_corners']).mean():.1f} corners,"
                f" {(g['home_fouls'] + g['away_fouls']).mean():.1f} fouls and"
                f" {(g['home_yellows'] + g['away_yellows']).mean():.2f} yellows per match")
    say("Done. Next: run the 15.1 checks in 29_upgrade15_data.sql in pgAdmin.")


def referees(load=False):
    """Make referees_next.csv for the next gameweek, or load it once filled in."""
    path = "referees_next.csv"
    if not load:
        code = fci_code(CURRENT_SEASON)
        for gw in range(1, 39):
            base = f"{FCI_RAW}/{code}/" + quote(f"By Tournament/Premier League/GW{gw}")
            m = read_csv(f"{base}/matches.csv", None, True)
            if m is None or len(m) == 0:
                continue
            if (m["finished"].astype(str).str.lower() != "true").any():
                teams = read_csv(f"{FCI_RAW}/{code}/teams.csv", None, True)
                tn = {int(r.code): model_team(r["name"]) for _, r in teams.iterrows()}
                m = m[m["finished"].astype(str).str.lower() != "true"]
                out = pd.DataFrame({"season": CURRENT_SEASON, "gameweek": gw,
                                    "home_team": m["home_team"].astype(int).map(tn),
                                    "away_team": m["away_team"].astype(int).map(tn), "referee": ""})
                out.to_csv(path, index=False, encoding="utf-8-sig")
                say(f"Wrote {path} for GW{gw} ({len(out)} fixtures). Fill in the referee column from the"
                    f" premierleague.com 'Match officials for Matchweek {gw}' page (full names are fine),"
                    f" save, then run: python player_data.py referees load")
                return
        say("No unplayed gameweek found.")
        return
    df = pd.read_csv(path, encoding="utf-8-sig")
    df = df[df["referee"].notna() & (df["referee"].astype(str).str.strip() != "")]

    def short(n):   # 'Anthony Taylor' -> 'A Taylor', the spelling football-data uses
        parts = str(n).strip().split()
        return n.strip() if len(parts) < 2 or len(parts[0]) <= 2 else parts[0][0] + " " + " ".join(parts[1:])
    df["referee"] = df["referee"].map(short)
    from sqlalchemy import text
    eng = engine()
    with eng.begin() as con:
        con.execute(text(DDL.split(";")[-2]))   # fixture_referees
        for r in df.itertuples(index=False):
            con.execute(text("""INSERT INTO fixture_referees VALUES (:s, :g, :h, :a, :r)
                                ON CONFLICT (season, home_team, away_team)
                                DO UPDATE SET referee = EXCLUDED.referee, gameweek = EXCLUDED.gameweek"""),
                        {"s": r.season, "g": int(r.gameweek), "h": r.home_team, "a": r.away_team, "r": r.referee})
    say(f"Loaded {len(df)} referees into fixture_referees: " + ", ".join(sorted(df['referee'].unique())))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("history", "update", "referees"):
        print(__doc__)
        sys.exit(1)
    if args[0] == "referees":
        referees(load=len(args) > 1 and args[1] == "load")
    else:
        run(args[0], use_db="--no-db" not in args)
