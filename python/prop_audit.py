"""
Upgrade 24: do your player fouls / fouled / shots / shots on target count the same things as the
bookmakers (who settle on Opta)? All weekend Paddy Power priced these 10-25 points above the model.

    python prop_audit.py              # Part A (automatic) and, if opta_sample.csv exists, Part B
    python prop_audit.py --template   # writes an empty opta_sample.csv to fill in for Part B

Part A (automatic): adds up your player rows (FPL-Core-Insights) for each team and match and
compares them with football-data's team totals for the same matches.
Part B (a sample you type in): Opta's player numbers for about 10 matches, from a site that shows
Opta player stats (e.g. FotMob: match > player > stats). It compares them with your player rows.

Part B writes prop_scaling.json (Opta count / your count for each stat). player_markets.py then
multiplies each player's calibrated expected count by it for upcoming games ('next' mode only),
so the published chances are on the bookmakers' scale. Backtests are unchanged.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

import quant_tools as qt

SAMPLE = "opta_sample.csv"
OUT = "prop_scaling.json"
STATS = {"fouls_committed": "fouls", "was_fouled": "fouled", "shots": "shots", "shots_on_target": "sot"}
MIN_ROWS = 100            # matched player rows needed before the scaling is saved
MIN_CHANGE = 0.03         # ignore differences smaller than 3%


def part_a(eng):
    d = qt.q("""
        WITH pp AS (
          SELECT fci_match_id, was_home, SUM(fouls_committed) AS fouls, SUM(was_fouled) AS fouled,
                 SUM(shots) AS shots, SUM(shots_on_target) AS sot
          FROM pl_player_matches WHERE was_home IS NOT NULL GROUP BY fci_match_id, was_home)
        SELECT f.season,
               h.fouls AS p_h_fouls, a.fouls AS p_a_fouls, h.fouled AS p_h_fouled, a.fouled AS p_a_fouled,
               h.shots AS p_h_shots, a.shots AS p_a_shots, h.sot AS p_h_sot, a.sot AS p_a_sot,
               d.home_fouls, d.away_fouls, d.home_shots, d.away_shots, d.home_sot, d.away_sot
        FROM team_match_stats f
        JOIN pp h ON h.fci_match_id = f.fci_match_id AND h.was_home
        JOIN pp a ON a.fci_match_id = f.fci_match_id AND NOT a.was_home
        JOIN v_team_names fh ON fh.name = f.home_team
        JOIN v_team_names fa ON fa.name = f.away_team
        JOIN v_team_match_stats d ON d.season = f.season AND d.home_team_id = fh.team_id
                                 AND d.away_team_id = fa.team_id
        WHERE f.source = 'fci'""", eng=eng)
    if d.empty:
        print("Part A: no matches linked (check player_data.py has been run).")
        return
    rows = []
    for season, g in d.groupby("season"):
        pairs = {"fouls committed": (g.p_h_fouls + g.p_a_fouls, g.home_fouls + g.away_fouls),
                 "fouled (vs opponent fouls)": (g.p_h_fouled + g.p_a_fouled, g.away_fouls + g.home_fouls),
                 "shots": (g.p_h_shots + g.p_a_shots, g.home_shots + g.away_shots),
                 "shots on target": (g.p_h_sot + g.p_a_sot, g.home_sot + g.away_sot)}
        for stat, (mine, fd) in pairs.items():
            ok = mine.notna() & fd.notna()
            rows.append({"season": season, "stat": stat, "matches": int(ok.sum()),
                         "your players per match": round(mine[ok].mean(), 2),
                         "football-data per match": round(fd[ok].mean(), 2),
                         "football-data / yours": round(fd[ok].sum() / max(mine[ok].sum(), 1), 3),
                         "same total %": round(100 * (mine[ok] == fd[ok]).mean(), 1)})
    print("\nPart A: your player rows added up per match vs football-data's team totals")
    print(pd.DataFrame(rows).to_string(index=False))
    print("A ratio near 1.00 means your player data and the team data count the same things. "
          "'fouled' is usually a little lower than fouls (fouls with no player fouled, e.g. handballs).")


def template():
    if os.path.exists(SAMPLE):
        raise SystemExit(f"{SAMPLE} already exists; not overwritten.")
    pd.DataFrame(columns=["match_date", "team", "player", "fouls_committed", "was_fouled", "shots",
                          "shots_on_target"]).to_csv(SAMPLE, index=False)
    print(f"Wrote {SAMPLE}. One row per player who played, e.g.\n"
          "  2026-10-18,Arsenal,Bukayo Saka,1,3,4,2\n"
          "match_date as YYYY-MM-DD; team as your database names it (or the bookmaker's name if it is "
          "in team_aliases); about 10 matches (about 280 rows) from 2026-27.")


def part_b(eng):
    if not os.path.exists(SAMPLE):
        print(f"\nPart B: no {SAMPLE} yet (run with --template to start one). Nothing saved.")
        return
    s = pd.read_csv(SAMPLE)
    if s.empty:
        print(f"\nPart B: {SAMPLE} is empty.")
        return
    s = s.dropna(how="all")
    # YYYY-MM-DD, or UK dd/mm/yyyy if Excel reformatted the dates when saving
    raw = s["match_date"].astype(str).str.strip()
    iso = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
    uk = pd.to_datetime(raw, format="%d/%m/%Y", errors="coerce")
    s["match_date"] = iso.fillna(uk).dt.date
    if s["match_date"].isna().any():
        raise SystemExit("Dates not understood (use YYYY-MM-DD): " + ", ".join(raw[s["match_date"].isna()].unique()))
    for c in STATS:
        s[c] = pd.to_numeric(s[c], errors="coerce").fillna(0)
    names = qt.q("SELECT name, team_id FROM v_team_names", eng=eng)
    al = qt.q("SELECT alias AS name, team_id FROM team_aliases", eng=eng)
    tmap = {qt.plain(n): t for n, t in pd.concat([names, al]).itertuples(index=False)}
    s["team_id"] = s["team"].map(lambda t: tmap.get(qt.plain(t)))
    if s["team_id"].isna().any():
        print("Team names not recognised:", ", ".join(sorted(s.loc[s.team_id.isna(), "team"].unique())))
    mine = qt.q("""SELECT p.match_date, n.team_id, p.player_code, pl.web_name, pl.first_name, pl.second_name,
                          p.fouls_committed, p.was_fouled, p.shots, p.shots_on_target, p.minutes
                   FROM pl_player_matches p
                   JOIN pl_players pl ON pl.season = p.season AND pl.player_code = p.player_code
                   JOIN v_team_names n ON n.name = p.team
                   WHERE p.match_date = ANY(CAST(:d AS DATE[])) AND p.minutes > 0""",
                {"d": [str(x) for x in s["match_date"].unique()]}, eng=eng)
    mine["match_date"] = pd.to_datetime(mine["match_date"]).dt.date
    keys = []
    for r in mine.itertuples(index=False):
        full = qt.plain(f"{r.first_name} {r.second_name}")
        keys.append({qt.plain(r.web_name), full, qt.plain(r.second_name), full.split()[-1] if full else ""})
    mine["keys"] = keys

    matched, missing = [], []
    for r in s.itertuples(index=False):
        cand = mine[(mine["match_date"] == r.match_date) & (mine["team_id"] == r.team_id)]
        pn = qt.plain(r.player)
        hit = cand[cand["keys"].map(lambda k: pn in k or (pn.split() and pn.split()[-1] in k))]
        if len(hit) == 1:
            row = hit.iloc[0]
            matched.append({**{f"opta_{c}": getattr(r, c) for c in STATS}, **{f"mine_{c}": row[c] for c in STATS}})
        else:
            missing.append(f"{r.match_date} {r.team} {r.player}")
    m = pd.DataFrame(matched)
    print(f"\nPart B: {len(m)} of {len(s)} sample rows matched to your player data")
    if missing:
        print("  not matched (check spelling / team / date):", "; ".join(missing[:15]),
              "..." if len(missing) > 15 else "")
    if m.empty:
        return
    rows, scale = [], {}
    for c, market in STATS.items():
        o, y = m[f"opta_{c}"].astype(float), m[f"mine_{c}"].astype(float)
        ratio = o.sum() / max(y.sum(), 1)
        rows.append({"stat": c, "Opta total": int(o.sum()), "your total": int(y.sum()),
                     "Opta / yours": round(ratio, 3), "same number %": round(100 * (o == y).mean(), 1)})
        if abs(ratio - 1) >= MIN_CHANGE:
            scale[market] = round(float(ratio), 3)
    print(pd.DataFrame(rows).to_string(index=False))
    if len(m) < MIN_ROWS:
        print(f"Only {len(m)} matched rows: add more matches (need {MIN_ROWS}+) before saving a scaling.")
        return
    json.dump(scale, open(OUT, "w"), indent=1)
    print(f"Saved {OUT}: {scale or 'no stat differs by 3% or more (nothing to scale)'}")
    print("Next: python player_markets.py next   (then match_markets / dashboard refresh as usual)")


def main():
    if "--template" in sys.argv:
        template()
        return
    eng = qt.engine()
    part_a(eng)
    part_b(eng)


if __name__ == "__main__":
    main()
