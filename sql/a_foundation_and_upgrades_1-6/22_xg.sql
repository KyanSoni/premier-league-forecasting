CREATE TABLE IF NOT EXISTS stg_xg (
  season TEXT, home_team TEXT, away_team TEXT, xg_home TEXT, xg_away TEXT
);
TRUNCATE stg_xg;


SELECT DISTINCT n AS unknown_name
FROM (SELECT TRIM(home_team) AS n FROM stg_xg
      UNION SELECT TRIM(away_team) FROM stg_xg) x
WHERE n NOT IN (SELECT alias FROM team_aliases);




INSERT INTO match_xg (match_id, xg_home, xg_away)
SELECT m.match_id, NULLIF(x.xg_home, '')::NUMERIC, NULLIF(x.xg_away, '')::NUMERIC
FROM stg_xg x
JOIN seasons s      ON s.label = x.season
JOIN team_aliases h ON h.alias = TRIM(x.home_team)
JOIN team_aliases a ON a.alias = TRIM(x.away_team)
JOIN matches m      ON m.season_id = s.season_id
                   AND m.home_team_id = h.team_id AND m.away_team_id = a.team_id
WHERE NULLIF(x.xg_home, '') IS NOT NULL
ON CONFLICT (match_id)
DO UPDATE SET xg_home = EXCLUDED.xg_home, xg_away = EXCLUDED.xg_away;




SELECT s.label, COUNT(*) AS played, COUNT(x.match_id) AS with_xg
FROM matches m
JOIN seasons s ON s.season_id = m.season_id
LEFT JOIN match_xg x ON x.match_id = m.match_id
WHERE m.home_goals IS NOT NULL
GROUP BY s.label;





CREATE OR REPLACE VIEW v_team_xg AS
WITH tm AS (
  SELECT m.season_id, m.home_team_id AS team_id, m.home_goals AS gf, m.away_goals AS ga,
         x.xg_home AS xgf, x.xg_away AS xga
  FROM matches m JOIN match_xg x ON x.match_id = m.match_id
  WHERE m.home_goals IS NOT NULL
  UNION ALL
  SELECT m.season_id, m.away_team_id, m.away_goals, m.home_goals, x.xg_away, x.xg_home
  FROM matches m JOIN match_xg x ON x.match_id = m.match_id
  WHERE m.home_goals IS NOT NULL
)
SELECT s.label AS season, t.team_name,
       COUNT(*) AS played,
       SUM(tm.gf) AS gf, ROUND(SUM(tm.xgf), 1) AS xgf,
       SUM(tm.ga) AS ga, ROUND(SUM(tm.xga), 1) AS xga,
       ROUND(SUM(tm.gf) - SUM(tm.xgf), 1) AS goals_above_xg,
       ROUND(SUM(tm.xga) - SUM(tm.ga), 1) AS conceded_below_xg
FROM tm
JOIN teams t   ON t.team_id = tm.team_id
JOIN seasons s ON s.season_id = tm.season_id
GROUP BY s.label, t.team_name;

SELECT * FROM v_team_xg WHERE season = '2026-27' ORDER BY goals_above_xg DESC;






INSERT INTO model_variants (variant, description, home_factor, away_factor, blend_k, xg_weight)
SELECT 'xg_' || (w.x * 100)::INT, 'blend_15 + ' || (w.x * 100)::INT || '% xG',
       v.home_factor, v.away_factor, v.blend_k, w.x
FROM model_variants v
CROSS JOIN (VALUES (0.5), (1.0)) AS w(x)
WHERE v.variant = 'blend_15';



SELECT variant, home_factor, away_factor, blend_k, xg_weight, adopted
FROM model_variants
WHERE variant IN ('blend_15', 'xg_50', 'xg_100')
ORDER BY variant;





SELECT r.run_label, COUNT(*) AS teams, ROUND(AVG(x.att_xg), 3) AS avg_att_xg
FROM v_xg_ratings x JOIN v_runs r USING (run_id)
GROUP BY r.run_label ORDER BY r.run_label;




SELECT run_label, model, matches, brier, rps
FROM v_scores
WHERE model IN ('blend_15', 'xg_50', 'xg_100', 'bookmaker')
  AND run_label LIKE '%after GW%'
ORDER BY run_label, rps;





SELECT model, SUM(matches) AS matches,
       ROUND(SUM(rps * matches) / SUM(matches), 4) AS rps
FROM v_scores
WHERE season = '2025-26' AND run_label LIKE '%after GW%'
  AND model IN ('home_adv', 'blend_15', 'xg_50', 'xg_100', 'bookmaker')
GROUP BY model
ORDER BY rps;

SELECT model, SUM(matches) AS matches,
       ROUND(SUM(model_rps * matches) / SUM(matches), 4)     AS model_rps,
       ROUND(SUM(bookmaker_rps * matches) / SUM(matches), 4) AS bookmaker_rps
FROM v_vs_bookmaker
WHERE season = '2025-26' AND run_label LIKE '%after GW%'
  AND model IN ('blend_15', 'xg_50', 'xg_100')
GROUP BY model
ORDER BY model_rps;



