-- =====================================================================
-- UPGRADE 14: availability snapshots and their test
-- =====================================================================

-- 14.5a  Tables (once)
CREATE TABLE IF NOT EXISTS stg_availability (
  team TEXT, missing_att TEXT, missing_def TEXT, att_factor TEXT, def_factor TEXT,
  players_out TEXT, run_label TEXT, taken_on TEXT
);
CREATE TABLE IF NOT EXISTS availability_snapshots (
  run_label TEXT, team_id INT REFERENCES teams(team_id),
  missing_att NUMERIC, missing_def NUMERIC, att_factor NUMERIC, def_factor NUMERIC,
  players_out TEXT, taken_on DATE,
  PRIMARY KEY (run_label, team_id)
);

-- 14.5b  Each run: TRUNCATE, then import availability.csv into stg_availability (csv, UTF8, Header on)
TRUNCATE stg_availability;

-- 14.5c  Name check: every team in the file must be known to the database (expect 0 rows).
--        If any are listed, add an alias, e.g.
--        INSERT INTO team_aliases (alias, team_id) SELECT 'Coventry', team_id FROM teams WHERE team_name = 'Coventry City';
SELECT DISTINCT TRIM(team) AS unknown_team FROM stg_availability
WHERE TRIM(team) NOT IN (SELECT alias FROM team_aliases UNION SELECT team_name FROM teams);

-- 14.5d  Store the snapshot (expect INSERT 0 20)
INSERT INTO availability_snapshots
SELECT s.run_label, a.team_id, s.missing_att::NUMERIC, s.missing_def::NUMERIC,
       s.att_factor::NUMERIC, s.def_factor::NUMERIC, s.players_out, s.taken_on::DATE
FROM stg_availability s
JOIN (SELECT alias AS name, team_id FROM team_aliases
      UNION SELECT team_name, team_id FROM teams) a ON a.name = TRIM(s.team)
ON CONFLICT (run_label, team_id) DO UPDATE
SET missing_att = EXCLUDED.missing_att, missing_def = EXCLUDED.missing_def,
    att_factor = EXCLUDED.att_factor, def_factor = EXCLUDED.def_factor,
    players_out = EXCLUDED.players_out, taken_on = EXCLUDED.taken_on;

-- 14.5e  Check it (expect 20 rows for the run, with the hardest-hit teams at the top)
SELECT s.run_label, t.team_name, s.att_factor, s.def_factor, s.players_out
FROM availability_snapshots s JOIN teams t USING (team_id)
WHERE s.run_label = (SELECT MAX(run_label) FROM availability_snapshots)
ORDER BY s.att_factor, s.def_factor DESC;

-- 14.6  The test view: next-gameweek predictions from your exported ratings,
--       with and without the availability factors. beta can be changed by
--       scaling (factor - 1): here 1.0 = as saved (BETA in the script).
CREATE OR REPLACE VIEW v_availability_test AS
WITH lam AS (
  SELECT r.run_label, m.match_id, m.home_goals, m.away_goals,
         hr.att * ar.def * ra.avg_goals * v.home_factor AS lh0,
         ar.att * hr.def * ra.avg_goals * v.away_factor AS la0,
         hr.att * COALESCE(ha.att_factor, 1) * ar.def * COALESCE(aa.def_factor, 1)
               * ra.avg_goals * v.home_factor AS lh1,
         ar.att * COALESCE(aa.att_factor, 1) * hr.def * COALESCE(ha.def_factor, 1)
               * ra.avg_goals * v.away_factor AS la1
  FROM v_runs r
  JOIN v_run_avg ra    ON ra.run_id = r.run_id
  JOIN model_variants v ON v.variant = 'home_adv'
  JOIN matches m       ON m.season_id = r.season_id AND m.round_no = r.gameweek + 1
                      AND m.home_goals IS NOT NULL
  JOIN team_ratings hr ON hr.run_id = r.run_id AND hr.team_id = m.home_team_id
  JOIN team_ratings ar ON ar.run_id = r.run_id AND ar.team_id = m.away_team_id
  LEFT JOIN availability_snapshots ha ON ha.run_label = r.run_label AND ha.team_id = m.home_team_id
  LEFT JOIN availability_snapshots aa ON aa.run_label = r.run_label AND aa.team_id = m.away_team_id
  WHERE r.run_label IN (SELECT run_label FROM availability_snapshots)
),
grid AS (
  SELECT l.run_label, l.match_id, l.home_goals, l.away_goals, k.k AS version, g.hg, g.ag,
         EXP(-lh) * POWER(lh, g.hg) / FACTORIAL(g.hg) * EXP(-la) * POWER(la, g.ag) / FACTORIAL(g.ag) AS p
  FROM lam l
  CROSS JOIN (VALUES (0), (1)) AS k(k)
  CROSS JOIN LATERAL (SELECT CASE WHEN k.k = 0 THEN l.lh0 ELSE l.lh1 END::FLOAT8 AS lh,
                             CASE WHEN k.k = 0 THEN l.la0 ELSE l.la1 END::FLOAT8 AS la) x
  CROSS JOIN (SELECT hg, ag FROM generate_series(0, 10) hg, generate_series(0, 10) ag) g
),
pr AS (
  SELECT run_label, match_id, version, home_goals, away_goals,
         SUM(p) FILTER (WHERE hg > ag) / SUM(p) AS ph,
         SUM(p) FILTER (WHERE hg = ag) / SUM(p) AS pd
  FROM grid GROUP BY run_label, match_id, version, home_goals, away_goals
)
SELECT run_label, CASE version WHEN 0 THEN 'without availability' ELSE 'with availability' END AS model,
       COUNT(*) AS matches,
       ROUND(AVG(0.5 * (POWER(ph - (home_goals > away_goals)::INT, 2)
                      + POWER(ph + pd - (home_goals >= away_goals)::INT, 2)))::NUMERIC, 4) AS rps
FROM pr GROUP BY run_label, version;

-- 14.7  The verdict across all runs so far (lower RPS = better)
SELECT model, SUM(matches) AS matches, ROUND(SUM(rps * matches) / SUM(matches), 4) AS rps
FROM v_availability_test GROUP BY model ORDER BY rps;
