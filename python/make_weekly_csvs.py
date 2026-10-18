"""
CSV helper for the prem_sims project (weekly use and Upgrade 7 history loads).

  python make_weekly_csvs.py xg      understat_2026-27.json            -> xg.csv
  python make_weekly_csvs.py odds    E0.csv                            -> odds.csv
  python make_weekly_csvs.py results E0.csv                            -> results.csv   (history seasons only)

Add a season label at the end to override SEASON; the file is then named after it, e.g.
  python make_weekly_csvs.py xg understat_2024-25.json 2024-25      -> xg_2024-25.csv

xg      : Understat season file (getLeagueData response) -> season, home_team, away_team, xg_home, xg_away
odds    : football-data.co.uk season file -> season, home_team, away_team, odds_h, odds_d, odds_a (AvgH/AvgD/AvgA)
results : football-data.co.uk season file -> the results.csv layout (season, round_no, match_date, venue,
          home_team, away_team, home_goals, away_goals). round_no = the later of the two teams' game numbers,
          so 'after GW10' means both teams had played at most 10 games. Use it for past seasons only;
          the current season's results.csv still comes from your Results sheet.

Team names are written the way your team_aliases table already knows them.
No password or database connection is needed.
"""
import csv
import json
import sys
from datetime import datetime

SEASON = "2026-27"     # default season label; change each summer

UNDERSTAT_NAMES = {
    "Manchester City": "Man City",
    "Manchester United": "Man United",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest",
    "Wolverhampton Wanderers": "Wolves",
    "Sheffield United": "Sheffield United",
    "Luton": "Luton",
}


def _write(out, header, rows):
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_xg(path, season, out="xg.csv"):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if "dates" not in data:
        sys.exit("This file has no 'dates' section. Save the getLeagueData response, not the page itself.")
    rows = []
    for m in data["dates"]:
        if not m.get("isResult"):
            continue
        h = UNDERSTAT_NAMES.get(m["h"]["title"], m["h"]["title"])
        a = UNDERSTAT_NAMES.get(m["a"]["title"], m["a"]["title"])
        rows.append([season, h, a, round(float(m["xG"]["h"]), 3), round(float(m["xG"]["a"]), 3)])
    _write(out, ["season", "home_team", "away_team", "xg_home", "xg_away"], rows)
    print(f"{out}: {len(rows)} played matches for {season}")


def _read_fd(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return [r for r in csv.DictReader(f) if r.get("HomeTeam")]


def make_odds(path, season, out="odds.csv"):
    rows = [[season, r["HomeTeam"].strip(), r["AwayTeam"].strip(), r["AvgH"], r["AvgD"], r["AvgA"]]
            for r in _read_fd(path) if r.get("AvgH")]
    _write(out, ["season", "home_team", "away_team", "odds_h", "odds_d", "odds_a"], rows)
    print(f"{out}: {len(rows)} matches with odds for {season}")


def make_results(path, season, out="results.csv"):
    recs = []
    for r in _read_fd(path):
        if r.get("FTHG", "") == "":
            continue
        d = r["Date"]
        dt = datetime.strptime(d, "%d/%m/%Y" if len(d.split("/")[-1]) == 4 else "%d/%m/%y")
        recs.append((dt, r.get("Time", ""), r["HomeTeam"].strip(), r["AwayTeam"].strip(), r["FTHG"], r["FTAG"]))
    recs.sort()
    games = {}
    rows = []
    for dt, _, h, a, hg, ag in recs:
        games[h] = games.get(h, 0) + 1
        games[a] = games.get(a, 0) + 1
        rows.append([season, max(games[h], games[a]), dt.strftime("%Y-%m-%d"), "", h, a, hg, ag])
    _write(out, ["season", "round_no", "match_date", "venue", "home_team", "away_team",
                 "home_goals", "away_goals"], rows)
    print(f"{out}: {len(rows)} results for {season}; highest round_no {max(r[1] for r in rows)}")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4) or sys.argv[1] not in ("xg", "odds", "results"):
        sys.exit("Usage: python make_weekly_csvs.py xg|odds|results <file> [season label]")
    kind = sys.argv[1]
    if len(sys.argv) == 4:                 # season given: name the file after it, e.g. results_2023-24.csv
        season = sys.argv[3]
        out = f"{kind}_{season}.csv"
    else:                                  # weekly use: xg.csv / odds.csv / results.csv
        season = SEASON
        out = f"{kind}.csv"
    {"xg": make_xg, "odds": make_odds, "results": make_results}[kind](sys.argv[2], season, out)
