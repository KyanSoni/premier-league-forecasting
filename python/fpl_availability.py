"""
Upgrade 14: player availability from the official Fantasy Premier League API.

  python fpl_availability.py "2026-27 after GW6"

Downloads https://fantasy.premierleague.com/api/bootstrap-static/ (free, no login) and, for each
team, works out how much of its usual attacking and defensive quality is unavailable for the next
gameweek. Writes:
  availability.csv                        -> paste into the model's Availability sheet (A1)
  snapshots/availability_<date>.csv       -> dated copy, also loaded into the database for testing

How "missing" is measured
  regulars   = the 14 players with most minutes this season (price order before anyone has played)
  importance = minutes share x price relative to the league average for the same position
  available  = chance_of_playing_next_round / 100 (status a = 1, d = 0.5, i/s/u/n = 0 when blank)
  players_out lists every squad player below 100% (with their chance), regulars marked *
  missing_att = importance-weighted share of forwards + midfielders unavailable
  missing_def = same for goalkeepers + defenders + half of midfielders
  att_factor  = 1 - BETA x missing_att      (multiplies the team's attack next gameweek)
  def_factor  = 1 + BETA x missing_def      (multiplies its defence rating: higher = concedes more)
"""
import csv
import json
import os
import sys
import urllib.request
from datetime import date

BETA = 0.3            # starting guess; tested by Upgrade 14's SQL after a few runs
REGULARS = 14
URL = "https://fantasy.premierleague.com/api/bootstrap-static/"

# FPL short names -> the team names in your model (Team Strength updated column A)
FPL_TO_MODEL = {
    "Man City": "Manchester City",
    "Man Utd": "Manchester United",
    "Spurs": "Tottenham",
    "Nott'm Forest": "Nottingham Forest",
    "Hull": "Hull City",
    # the model uses the short names for these four (checked in 'Fixture sims updated' D/E)
    "Coventry City": "Coventry",
    "Ipswich Town": "Ipswich",
    "Leeds United": "Leeds",
    "Newcastle United": "Newcastle",
}


def main():
    run_label = sys.argv[1] if len(sys.argv) > 1 else ""
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    teams = {t["id"]: FPL_TO_MODEL.get(t["name"], t["name"]) for t in data["teams"]}
    players = data["elements"]

    # league average price per position
    pos_avg = {}
    for p in players:
        pos_avg.setdefault(p["element_type"], []).append(p["now_cost"])
    pos_avg = {k: sum(v) / len(v) for k, v in pos_avg.items()}

    def available(p):
        c = p.get("chance_of_playing_next_round")
        if c is not None:
            return c / 100
        return {"a": 1.0, "d": 0.5}.get(p.get("status"), 0.0)

    rows = []
    for tid, tname in sorted(teams.items(), key=lambda x: x[1]):
        squad = [p for p in players if p["team"] == tid]
        played = sum(p["minutes"] for p in squad) > 0
        squad.sort(key=lambda p: (p["minutes"] if played else p["now_cost"]), reverse=True)
        regs = squad[:REGULARS]
        tot_min = sum(p["minutes"] for p in regs) or 1
        att_w = att_miss = def_w = def_miss = 0.0
        out = []
        for p in regs:
            share = p["minutes"] / tot_min if played else 1 / REGULARS
            imp = share * p["now_cost"] / pos_avg[p["element_type"]]
            miss = 1 - available(p)
            et = p["element_type"]            # 1 GK, 2 DEF, 3 MID, 4 FWD
            wa = 1.0 if et in (3, 4) else 0.0
            wd = 1.0 if et in (1, 2) else (0.5 if et == 3 else 0.0)
            att_w += imp * wa; att_miss += imp * wa * miss
            def_w += imp * wd; def_miss += imp * wd * miss
        # list EVERY squad player who is not fully available (regulars marked *), most doubtful first;
        # only the regulars count towards the factors, because fringe players barely change team strength
        reg_ids = {p["id"] for p in regs}
        for p in sorted(squad, key=available):
            a = available(p)
            if a < 1:
                star = "*" if p["id"] in reg_ids else ""
                out.append(f"{p['web_name']}{star} {int(round(a * 100))}% ({p.get('news') or p.get('status')})")
        ma = att_miss / att_w if att_w else 0
        md = def_miss / def_w if def_w else 0
        rows.append([tname, round(ma, 3), round(md, 3), round(1 - BETA * ma, 3), round(1 + BETA * md, 3),
                     "; ".join(out), run_label, date.today().isoformat()])

    header = ["team", "missing_att", "missing_def", "att_factor", "def_factor", "players_out", "run_label", "taken_on"]
    os.makedirs("snapshots", exist_ok=True)
    for path in ("availability.csv", os.path.join("snapshots", f"availability_{date.today().isoformat()}.csv")):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:   # -sig so Excel shows accents (Fábio, Ekitiké) correctly
            w = csv.writer(f); w.writerow(header); w.writerows(rows)
    print(f"availability.csv: {len(rows)} teams for run '{run_label}'")
    if not run_label:
        print("WARNING: no run label given. Run it as: python fpl_availability.py \"2026-27 after GW6\"")
    print("Team names written (must match your model's Team Strength column A):")
    print("  " + ", ".join(r[0] for r in rows))
    print("\nAll teams, hardest hit first (* = one of the 14 regulars, which count towards the factors):")
    for r in sorted(rows, key=lambda r: -(r[1] + r[2])):
        print(f"  {r[0]:20s} att x{r[3]:.3f}  def x{r[4]:.3f}  {r[5] or '(nobody out)'}")


if __name__ == "__main__":
    main()
