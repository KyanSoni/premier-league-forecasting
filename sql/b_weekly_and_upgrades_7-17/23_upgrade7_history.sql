-- =====================================================================
-- UPGRADE 7: load 2023-24 and 2024-25, then build 2024-25 backtest runs
-- Run each block on its own (highlight, F5) and compare with "expect".
-- =====================================================================

-- 7.3a  The two seasons (expect INSERT 0 2, or 0 1 if 2024-25 already exists)
INSERT INTO seasons (label) VALUES ('2023-24'), ('2024-25')
ON CONFLICT (label) DO NOTHING;

-- 7.3b  Empty staging, then import the six CSVs:
--       results_2023-24.csv and results_2024-25.csv -> stg_results
--       xg_2023-24.csv      and xg_2024-25.csv      -> stg_xg
--       odds_2023-24.csv    and odds_2024-25.csv    -> stg_odds
TRUNCATE stg_results, stg_xg, stg_odds;

-- 7.3c  Counts (expect results 760, xg 760, odds 760)
SELECT 'results' AS tbl, COUNT(*) FROM stg_results
UNION ALL SELECT 'xg', COUNT(*) FROM stg_xg
UNION ALL SELECT 'odds', COUNT(*) FROM stg_odds;

-- 7.3d  Names the database doesn't know yet (Luton, Sheffield United ... expected)
SELECT DISTINCT n AS unknown_name
FROM (SELECT TRIM(home_team) AS n FROM stg_results UNION SELECT TRIM(away_team) FROM stg_results
      UNION SELECT TRIM(home_team) FROM stg_xg   UNION SELECT TRIM(away_team) FROM stg_xg
      UNION SELECT TRIM(home_team) FROM stg_odds UNION SELECT TRIM(away_team) FROM stg_odds) x
WHERE n NOT IN (SELECT alias FROM team_aliases)
ORDER BY 1;

-- 7.3e  For each name listed: is the club already in teams under a longer name?
SELECT team_id, team_name FROM teams ORDER BY team_name;

--  If YES (e.g. teams has 'Luton Town'), add just an alias:
--    INSERT INTO team_aliases (alias, team_id) SELECT 'Luton', team_id FROM teams WHERE team_name = 'Luton Town';
--  If NO, add the club and then the alias:
--    INSERT INTO teams (team_name) VALUES ('Luton Town');
--    INSERT INTO team_aliases (alias, team_id) SELECT 'Luton', team_id FROM teams WHERE team_name = 'Luton Town';
--  Rerun 7.3d until it returns 0 rows.

-- 7.3f  The matches (expect INSERT 0 760)
INSERT INTO matches (season_id, round_no, match_date, venue, home_team_id, away_team_id, home_goals, away_goals)
SELECT s.season_id, r.round_no::INT, r.match_date::DATE, NULLIF(r.venue, ''),
       h.team_id, a.team_id, r.home_goals::INT, r.away_goals::INT
FROM stg_results r
JOIN seasons s      ON s.label = r.season
JOIN team_aliases h ON h.alias = TRIM(r.home_team)
JOIN team_aliases a ON a.alias = TRIM(r.away_team)
ON CONFLICT (season_id, home_team_id, away_team_id)
DO UPDATE SET home_goals = EXCLUDED.home_goals, away_goals = EXCLUDED.away_goals,
              match_date = EXCLUDED.match_date, round_no = EXCLUDED.round_no;

-- 7.3g  Check: 2024-25 rebuilt from matches must equal your stored final table
--       (expect 0 rows: no team whose points or goals differ)
SELECT v.team_name, v.pts, f.pts AS stored_pts, v.gf, f.gf AS stored_gf
FROM v_league_table v
JOIN seasons s      ON s.label = v.season
JOIN teams t        ON t.team_name = v.team_name
JOIN final_tables f ON f.season_id = s.season_id AND f.team_id = t.team_id
WHERE v.season = '2024-25' AND (v.pts <> f.pts OR v.gf <> f.gf OR v.ga <> f.ga);

-- 7.3h  Final table for 2023-24 (needed by the 2024-25 pre-season run). Expect INSERT 0 20
INSERT INTO final_tables (season_id, team_id, position, played, won, drawn, lost, gf, ga, gd, pts)
SELECT s.season_id, t.team_id, v.position, v.played, v.won, v.drawn, v.lost, v.gf, v.ga, v.gd, v.pts
FROM v_league_table v
JOIN seasons s ON s.label = v.season
JOIN teams t   ON t.team_name = v.team_name
WHERE v.season = '2023-24'
ON CONFLICT (season_id, team_id) DO NOTHING;

-- 7.3i  xG (expect INSERT 0 760)
INSERT INTO match_xg (match_id, xg_home, xg_away)
SELECT m.match_id, NULLIF(x.xg_home, '')::NUMERIC, NULLIF(x.xg_away, '')::NUMERIC
FROM stg_xg x
JOIN seasons s      ON s.label = x.season
JOIN team_aliases h ON h.alias = TRIM(x.home_team)
JOIN team_aliases a ON a.alias = TRIM(x.away_team)
JOIN matches m      ON m.season_id = s.season_id
                   AND m.home_team_id = h.team_id AND m.away_team_id = a.team_id
WHERE NULLIF(x.xg_home, '') IS NOT NULL
ON CONFLICT (match_id) DO UPDATE SET xg_home = EXCLUDED.xg_home, xg_away = EXCLUDED.xg_away;

-- 7.3j  Bookmaker odds (expect INSERT 0 760)
INSERT INTO external_predictions (source, run_label, match_id, p_home, p_draw, p_away)
SELECT 'bookmaker', 'odds', m.match_id,
       ROUND((1 / o.h) / (1 / o.h + 1 / o.d + 1 / o.a), 4),
       ROUND((1 / o.d) / (1 / o.h + 1 / o.d + 1 / o.a), 4),
       ROUND((1 / o.a) / (1 / o.h + 1 / o.d + 1 / o.a), 4)
FROM (SELECT season, TRIM(home_team) AS home_team, TRIM(away_team) AS away_team,
             NULLIF(odds_h, '')::NUMERIC AS h, NULLIF(odds_d, '')::NUMERIC AS d,
             NULLIF(odds_a, '')::NUMERIC AS a
      FROM stg_odds) o
JOIN seasons s      ON s.label = o.season
JOIN team_aliases h ON h.alias = o.home_team
JOIN team_aliases a ON a.alias = o.away_team
JOIN matches m      ON m.season_id = s.season_id
                   AND m.home_team_id = h.team_id AND m.away_team_id = a.team_id
WHERE o.h IS NOT NULL AND o.d IS NOT NULL AND o.a IS NOT NULL
ON CONFLICT (source, run_label, match_id)
DO UPDATE SET p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away;

-- 7.3k  Everything loaded (expect 380 / 380 / 380 for both seasons)
SELECT s.label, COUNT(*) AS matches, COUNT(x.match_id) AS with_xg,
       COUNT(e.match_id) AS with_odds
FROM matches m
JOIN seasons s ON s.season_id = m.season_id
LEFT JOIN match_xg x ON x.match_id = m.match_id
LEFT JOIN external_predictions e ON e.match_id = m.match_id AND e.source = 'bookmaker'
WHERE s.label IN ('2023-24', '2024-25')
GROUP BY s.label ORDER BY s.label;

-- =====================================================================
-- 7.4  2024-25 backtest runs, rated exactly like your Excel model
--      (no Excel needed: ratings are rebuilt from the matches table)
-- =====================================================================

-- 7.4a  The ten run labels (expect INSERT 0 10)
INSERT INTO model_runs (season_id, run_label)
SELECT s.season_id, l.label
FROM seasons s
CROSS JOIN (VALUES ('2024-25 preseason'), ('2024-25 after GW3'), ('2024-25 after GW5'),
                   ('2024-25 after GW10'), ('2024-25 after GW15'), ('2024-25 after GW18'),
                   ('2024-25 after GW22'), ('2024-25 after GW26'), ('2024-25 after GW30'),
                   ('2024-25 after GW34')) AS l(label)
WHERE s.label = '2024-25'
ON CONFLICT (run_label) DO NOTHING;

-- 7.4b  In-season ratings for the nine 'after GW' runs (expect INSERT 0 180)
INSERT INTO team_ratings (run_id, team_id, att, def)
WITH tm AS (
  SELECT r.run_id, m.home_team_id AS team_id, m.home_goals AS gf, m.away_goals AS ga
  FROM v_runs r JOIN matches m ON m.season_id = r.season_id AND m.round_no <= r.gameweek
  WHERE r.season = '2024-25' AND r.gameweek > 0 AND m.home_goals IS NOT NULL
  UNION ALL
  SELECT r.run_id, m.away_team_id, m.away_goals, m.home_goals
  FROM v_runs r JOIN matches m ON m.season_id = r.season_id AND m.round_no <= r.gameweek
  WHERE r.season = '2024-25' AND r.gameweek > 0 AND m.home_goals IS NOT NULL
),
lg AS (SELECT run_id, AVG(gf)::NUMERIC AS avg_goals FROM tm GROUP BY run_id)
SELECT tm.run_id, tm.team_id,
       ROUND(1 + ((AVG(tm.gf) / lg.avg_goals) - 1) * 0.75, 6),
       ROUND(1 + ((AVG(tm.ga) / lg.avg_goals) - 1) * 0.75, 6)
FROM tm JOIN lg USING (run_id)
GROUP BY tm.run_id, tm.team_id, lg.avg_goals
ON CONFLICT (run_id, team_id) DO NOTHING;

-- 7.4c  Pre-season ratings: 2023-24 final table for the 17 clubs that stayed up,
--       Championship regression + 25% reversion for the 3 promoted clubs (expect INSERT 0 20)
INSERT INTO team_ratings (run_id, team_id, att, def)
WITH run AS (SELECT run_id FROM v_runs WHERE run_label = '2024-25 preseason'),
     prev AS (SELECT f.* FROM final_tables f JOIN seasons s USING (season_id) WHERE s.label = '2023-24'),
     lg AS (SELECT SUM(gf)::NUMERIC / SUM(played) AS avg_goals FROM prev),
     cur_teams AS (
       SELECT DISTINCT home_team_id AS team_id FROM matches m JOIN seasons s USING (season_id)
       WHERE s.label = '2024-25')
SELECT run.run_id, c.team_id,
       ROUND(CASE WHEN p.team_id IS NOT NULL
                  THEN 1 + ((p.gf::NUMERIC / p.played / lg.avg_goals) - 1) * 0.75
                  ELSE 1 + ((f.att_slope * ph.champ_att + f.att_intercept) - 1) * 0.75 END::NUMERIC, 6),
       ROUND(CASE WHEN p.team_id IS NOT NULL
                  THEN 1 + ((p.ga::NUMERIC / p.played / lg.avg_goals) - 1) * 0.75
                  ELSE 1 + ((f.def_slope * ph.champ_def + f.def_intercept) - 1) * 0.75 END::NUMERIC, 6)
FROM cur_teams c
CROSS JOIN run CROSS JOIN lg CROSS JOIN v_promoted_fit f
LEFT JOIN prev p ON p.team_id = c.team_id
LEFT JOIN promoted_history ph ON ph.team_id = c.team_id AND ph.season_label = '2023-24'
ON CONFLICT (run_id, team_id) DO NOTHING;

-- 7.4d  Every 2024-25 run has 20 ratings and no blanks (expect 10 rows, 20 / 20)
SELECT r.run_label, COUNT(*) AS teams, COUNT(tr.att) AS with_att
FROM v_runs r JOIN team_ratings tr USING (run_id)
WHERE r.season = '2024-25'
GROUP BY r.run_label, r.gameweek ORDER BY r.gameweek;

-- 7.4e  Promoted clubs got sensible ratings (expect Leicester, Ipswich, Southampton about 0.7-0.85 att, 1.1-1.3 def)
SELECT t.team_name, tr.att, tr.def
FROM team_ratings tr JOIN v_runs r USING (run_id) JOIN teams t USING (team_id)
WHERE r.run_label = '2024-25 preseason'
ORDER BY tr.att;

-- =====================================================================
-- 7.6  Results over both seasons (after rerunning python dc_model.py)
-- =====================================================================
SELECT model, season, SUM(matches) AS matches,
       ROUND(SUM(rps * matches) / SUM(matches), 4) AS rps
FROM v_scores
WHERE run_label LIKE '%after GW%' AND season IN ('2024-25', '2025-26')
  AND model IN ('home_adv', 'blend_15', 'xg_50', 'dixon_coles')
GROUP BY ROLLUP (model, season)
HAVING model IS NOT NULL
ORDER BY model, season NULLS LAST;
