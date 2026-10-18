DROP VIEW IF EXISTS v_calibration, v_brier, v_match_predictions;


CREATE VIEW v_match_predictions AS
WITH run_avg AS (
  SELECT r.run_id, r.run_label, r.season_id, r.season, r.gameweek,
         CASE WHEN r.gameweek = 0 THEN
              (SELECT SUM(f.gf)::NUMERIC / SUM(f.played)
               FROM final_tables f JOIN seasons s2 USING (season_id)
               WHERE s2.label < r.season
               GROUP BY s2.label
               ORDER BY s2.label DESC
               LIMIT 1)
         ELSE
              (SELECT SUM(m.home_goals + m.away_goals)::NUMERIC / (2 * COUNT(*))
               FROM matches m
               WHERE m.season_id = r.season_id
                 AND m.round_no <= r.gameweek
                 AND m.home_goals IS NOT NULL)
         END AS avg_goals
  FROM v_runs r
),
lambdas AS (
  SELECT ra.run_id, ra.run_label, ra.season, ra.gameweek,
         m.match_id, m.round_no, m.match_date,
         th.team_name AS home_team, ta.team_name AS away_team,
         m.home_goals, m.away_goals,
         hr.att * ar.def * ra.avg_goals AS xg_home,
         ar.att * hr.def * ra.avg_goals AS xg_away
  FROM run_avg ra
  JOIN matches m       ON m.season_id = ra.season_id
  JOIN team_ratings hr ON hr.run_id = ra.run_id AND hr.team_id = m.home_team_id
  JOIN team_ratings ar ON ar.run_id = ra.run_id AND ar.team_id = m.away_team_id
  JOIN teams th        ON th.team_id = m.home_team_id
  JOIN teams ta        ON ta.team_id = m.away_team_id
),
grid AS (
  SELECT l.run_id, l.match_id, g.hg, g.ag,
         EXP(-l.xg_home::FLOAT8) * POWER(l.xg_home::FLOAT8, g.hg) / FACTORIAL(g.hg)::FLOAT8
       * EXP(-l.xg_away::FLOAT8) * POWER(l.xg_away::FLOAT8, g.ag) / FACTORIAL(g.ag)::FLOAT8 AS p
  FROM lambdas l
  CROSS JOIN (SELECT hg, ag FROM generate_series(0, 10) AS hg, generate_series(0, 10) AS ag) g
),
probs AS (
  SELECT run_id, match_id,
         SUM(p) FILTER (WHERE hg > ag) / SUM(p) AS p_home,
         SUM(p) FILTER (WHERE hg = ag) / SUM(p) AS p_draw,
         SUM(p) FILTER (WHERE hg < ag) / SUM(p) AS p_away
  FROM grid
  GROUP BY run_id, match_id
)
SELECT l.season, l.run_label, l.gameweek AS run_gameweek,
       l.round_no, l.match_date, l.home_team, l.away_team,
       ROUND(l.xg_home, 2) AS xg_home,
       ROUND(l.xg_away, 2) AS xg_away,
       ROUND(p.p_home::NUMERIC, 4) AS p_home,
       ROUND(p.p_draw::NUMERIC, 4) AS p_draw,
       ROUND(p.p_away::NUMERIC, 4) AS p_away,
       l.home_goals, l.away_goals,
       CASE WHEN l.home_goals IS NULL THEN NULL
            WHEN l.home_goals > l.away_goals THEN 'H'
            WHEN l.home_goals = l.away_goals THEN 'D'
            ELSE 'A' END AS result,
       (l.round_no > l.gameweek) AS out_of_sample,
       l.run_id, l.match_id
FROM lambdas l
JOIN probs p ON p.run_id = l.run_id AND p.match_id = l.match_id;



CREATE VIEW v_brier AS
WITH s AS (
  SELECT run_label, season, run_gameweek,
         COUNT(*) AS matches_tested,
         AVG(POWER(p_home - (result = 'H')::INT, 2)
           + POWER(p_draw - (result = 'D')::INT, 2)
           + POWER(p_away - (result = 'A')::INT, 2)) AS brier_model,
         1 - (POWER(AVG((result = 'H')::INT), 2)
            + POWER(AVG((result = 'D')::INT), 2)
            + POWER(AVG((result = 'A')::INT), 2)) AS brier_baseline,
         AVG(CASE WHEN (CASE WHEN p_home >= p_draw AND p_home >= p_away THEN 'H'
                             WHEN p_away >= p_draw THEN 'A'
                             ELSE 'D' END) = result
                  THEN 1.0 ELSE 0 END) AS pct_correct_pick
  FROM v_match_predictions
  WHERE result IS NOT NULL AND out_of_sample
  GROUP BY run_label, season, run_gameweek
)
SELECT run_label, season, run_gameweek, matches_tested,
       ROUND(brier_model, 4)    AS brier_model,
       ROUND(brier_baseline, 4) AS brier_baseline,
       ROUND(100 * (1 - brier_model / NULLIF(brier_baseline, 0)), 1) AS improvement_pct,
       ROUND(pct_correct_pick, 3) AS pct_correct_pick
FROM s;



CREATE VIEW v_calibration AS
SELECT run_label, season,
       FLOOR(p_home * 10) / 10 AS band_start,
       (FLOOR(p_home * 10) * 10)::INT || '-' || (FLOOR(p_home * 10) * 10 + 10)::INT || '%' AS band,
       COUNT(*) AS matches,
       ROUND(AVG(p_home), 3) AS avg_predicted,
       ROUND(AVG((result = 'H')::INT), 3) AS actual_rate
FROM v_match_predictions
WHERE result IS NOT NULL AND out_of_sample
GROUP BY run_label, season, FLOOR(p_home * 10);



SELECT run_label, COUNT(*) AS fixtures
FROM v_match_predictions
GROUP BY run_label
ORDER BY run_label;



SELECT MAX(ABS(p_home + p_draw + p_away - 1)) AS worst_rounding_gap
FROM v_match_predictions;




SELECT home_team, away_team, xg_home, xg_away, p_home, p_draw, p_away
FROM v_match_predictions
WHERE run_label = '2026-27 preseason'
  AND home_team = 'Arsenal' AND away_team = 'Coventry City';



  SELECT * FROM v_brier ORDER BY season, run_gameweek;


  SELECT run_label,
       COUNT(*) AS matches,
       ROUND(AVG(POWER(p_home - (result = 'H')::INT, 2)
               + POWER(p_draw - (result = 'D')::INT, 2)
               + POWER(p_away - (result = 'A')::INT, 2)), 4) AS brier
FROM v_match_predictions
WHERE season = '2025-26'
  AND round_no > 22
  AND result IS NOT NULL
GROUP BY run_label
ORDER BY brier;



SELECT band, matches, avg_predicted, actual_rate,
       ROUND(actual_rate - avg_predicted, 3) AS gap
FROM v_calibration
WHERE run_label = '2025-26 preseason'
ORDER BY band_start;
