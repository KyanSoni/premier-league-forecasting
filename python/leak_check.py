"""
Upgrade 27b: automatic leak check. Did any forecast use information from after the matches it predicts?
(The GW22 run of 2025-26 once held end-of-season ratings: RPS 0.2035 'leaked' vs 0.2176 honest.)

    python leak_check.py

Prints OK or CHECK for each test. Run it after every weekly run (weekly.py check prints the same
list at the end once you add it, see Guide 4). Nothing is changed in the database.

 1. Frozen ratings     a run whose 20 ratings are identical to a LATER run of the same season
 2. Frozen sims        a run whose average finishes are identical to a later run's
 3. Availability       snapshot taken after the first kick-off of the gameweek it was for
 4. Live run timing    a 2026-27 run loaded after games of the round it predicts had kicked off
                       (preseason runs are skipped: they are often loaded later than they were built)
 5. Rescheduled games  a game from a later round played before the run, so already in its ratings,
                       but scored as unseen
 6. xG for unplayed    expected goals stored for a match with no result
 7. Value log timing   prices logged after the match day
 8. Prop log timing    prop prices logged after the match day (Upgrade 25)
"""
import re

import numpy as np
import pandas as pd

import quant_tools as qt

SEASON = qt.SEASONS[-1]


def gw_of(label):
    if "preseason" in str(label).lower():
        return 0
    m = re.search(r"GW(\d+)", str(label))
    return int(m.group(1)) if m else -1


def safe(sql, eng, params=None):
    try:
        return qt.q(sql, params, eng=eng)
    except Exception as e:                       # noqa: BLE001 - a missing table is reported, not fatal
        msg = str(e)
        if "does not exist" in msg:
            return "table or view not set up yet"
        return msg.splitlines()[0][:120]


def report(name, bad, detail=None, note=""):
    if isinstance(bad, str):
        print(f"  SKIP   {name}: {bad}")
        return 0
    n = len(bad)
    print(f"  {'OK    ' if n == 0 else 'CHECK '} {name}: {n} found{(' - ' + note) if n and note else ''}")
    if n and detail:
        print("         " + detail(bad))
    return int(n > 0)


def frozen(df, key, val):
    """Runs whose values equal a later run's in the same season."""
    out = []
    for season, g in df.groupby("season"):
        sig = {}
        for run, r in g.groupby("run_label"):
            sig[run] = (gw_of(run), tuple(r.sort_values(key)[val].astype(float).round(4).to_numpy().ravel()))
        runs = sorted(sig, key=lambda x: sig[x][0])
        for i, a in enumerate(runs):
            for b in runs[i + 1:]:
                if sig[a][1] == sig[b][1] and sig[a][0] < sig[b][0]:
                    out.append(f"{a} = {b}")
    return out


def main():
    eng = qt.engine()
    checks = 0
    print("Leak check")

    r = safe("""SELECT r.season, r.run_label, tr.team_id, tr.att, tr.def
                FROM team_ratings tr JOIN v_runs r USING (run_id)""", eng)
    bad = r if isinstance(r, str) else frozen(r, ["team_id"], ["att", "def"])
    checks += report("1. Frozen ratings", bad, lambda b: "; ".join(b[:6]))

    s = safe("SELECT season, run_label, team_name, avg_finish FROM v_sim_summary", eng)
    bad = s if isinstance(s, str) else frozen(s, ["team_name"], ["avg_finish"])
    checks += report("2. Frozen sims", bad, lambda b: "; ".join(b[:6]))

    a = safe("""SELECT a.run_label, MIN(a.taken_on) AS taken_on, r.season, r.gameweek
                FROM availability_snapshots a JOIN v_runs r USING (run_label)
                GROUP BY a.run_label, r.season, r.gameweek""", eng)
    if not isinstance(a, str):
        first = qt.q("""SELECT s.label AS season, m.round_no, MIN(m.match_date) AS first_ko
                        FROM matches m JOIN seasons s USING (season_id) GROUP BY s.label, m.round_no""", eng=eng)
        a["round_no"] = a["gameweek"] + 1
        a = a.merge(first, on=["season", "round_no"], how="left")
        a = a[pd.to_datetime(a["taken_on"]) > pd.to_datetime(a["first_ko"])]
    checks += report("3. Availability after kick-off", a,
                     lambda b: "; ".join(f"{x.run_label} taken {x.taken_on}, first game {x.first_ko}"
                                         for x in b.head(6).itertuples()))

    lr = safe("""SELECT r.run_label, r.run_date, r.gameweek, m.match_id, m.match_date
                 FROM v_runs r JOIN matches m ON m.season_id = r.season_id AND m.round_no = r.gameweek + 1
                 WHERE r.season = :s AND r.gameweek > 0 AND m.match_date < r.run_date""", eng, {"s": SEASON})
    checks += report("4. Live run loaded after kick-off", lr,
                     lambda b: "; ".join(f"{x.run_label} loaded {x.run_date}, match {x.match_id} on {x.match_date}"
                                         for x in b.drop_duplicates("run_label").head(6).itertuples()),
                     note="those matches' scores flatter that run")

    sp = safe("""WITH cut AS (
                   SELECT r.run_label, r.season_id, r.gameweek, MAX(m.match_date) AS cutoff
                   FROM v_runs r JOIN matches m ON m.season_id = r.season_id AND m.round_no <= r.gameweek
                   WHERE r.gameweek > 0 GROUP BY r.run_label, r.season_id, r.gameweek)
                 SELECT c.run_label, COUNT(*) AS n
                 FROM cut c JOIN matches m ON m.season_id = c.season_id AND m.round_no > c.gameweek
                                          AND m.match_date < c.cutoff AND m.home_goals IS NOT NULL
                 GROUP BY c.run_label ORDER BY c.run_label""", eng)
    checks += report("5. Rescheduled games scored as unseen", sp,
                     lambda b: "; ".join(f"{x.run_label}: {x.n}" for x in b.head(6).itertuples()),
                     note="games from a later round played before the run, so already in its ratings; "
                          "harmless if only a handful, but they flatter that run's score")

    xg = safe("""SELECT x.match_id FROM match_xg x JOIN matches m USING (match_id)
                 WHERE m.home_goals IS NULL AND (x.xg_home IS NOT NULL OR x.xg_away IS NOT NULL)""", eng)
    checks += report("6. xG for unplayed matches", xg, lambda b: ", ".join(map(str, b["match_id"].head(10))))

    vl = safe("""SELECT v.match_id, v.market, v.logged_at, m.match_date FROM value_log v
                 JOIN matches m USING (match_id) WHERE v.logged_at::DATE > m.match_date""", eng)
    checks += report("7. Value log after match day", vl,
                     lambda b: "; ".join(f"match {x.match_id} {x.market} logged {x.logged_at}" for x in b.head(6).itertuples()))
    same = safe("""SELECT COUNT(*) AS n FROM value_log v JOIN matches m USING (match_id)
                   WHERE v.logged_at::DATE = m.match_date""", eng)
    if not isinstance(same, str) and int(same["n"].iloc[0]):
        print(f"         note: {int(same['n'].iloc[0])} value-log prices logged on match day; fine if before kick-off")

    pp = safe("""SELECT p.id, p.logged_on, m.match_date FROM prop_prices p JOIN matches m USING (match_id)
                 WHERE p.logged_on > m.match_date""", eng)
    checks += report("8. Prop log after match day", pp,
                     lambda b: "; ".join(f"row {x.id} logged {x.logged_on}, match {x.match_date}" for x in b.head(6).itertuples()))

    print(f"\n{'All clear.' if checks == 0 else f'{checks} test(s) need a look (CHECK above).'}")


if __name__ == "__main__":
    main()
