TRUNCATE stg_ratings, stg_sims;


SELECT 'ratings' AS tbl, run_label, COUNT(*) FROM stg_ratings GROUP BY run_label
UNION ALL
SELECT 'sims', run_label, COUNT(*) FROM stg_sims GROUP BY run_label
ORDER BY 2, 1;


SELECT run_label FROM model_runs WHERE run_label IN (SELECT DISTINCT run_label FROM stg_sims);



INSERT INTO model_runs (season_id, run_label)
SELECT s.season_id, r.run_label
FROM (SELECT DISTINCT run_label FROM stg_sims) r
JOIN seasons s ON s.label = LEFT(r.run_label, 7);


INSERT INTO team_ratings (run_id, team_id, att, def)
SELECT m.run_id, a.team_id, r.att::NUMERIC, r.def::NUMERIC
FROM stg_ratings r
JOIN model_runs m   ON m.run_label = r.run_label
JOIN team_aliases a ON a.alias = TRIM(r.team);


INSERT INTO sim_results (run_id, sim_no, team_id, final_position)
SELECT m.run_id, s.sim_no::INT, a.team_id, s.position::INT
FROM stg_sims s
JOIN model_runs m   ON m.run_label = s.run_label
JOIN team_aliases a ON a.alias = TRIM(s.team);



SELECT r.run_label, r.gameweek,
       (SELECT COUNT(*) FROM sim_results  s WHERE s.run_id = r.run_id) AS sims,
       (SELECT COUNT(*) FROM team_ratings t WHERE t.run_id = r.run_id) AS ratings
FROM v_runs r
WHERE r.season = '2025-26'
ORDER BY r.gameweek;


SELECT COUNT(home_goals) FROM matches m JOIN seasons s USING (season_id) WHERE s.label = '2025-26';



SELECT r.gameweek, MAX(s.matches) AS matches,
       MAX(s.rps) FILTER (WHERE s.model = 'home_adv') AS home_adv,
       MAX(s.rps) FILTER (WHERE s.model = 'blend_5')  AS blend_5,
       MAX(s.rps) FILTER (WHERE s.model = 'blend_10') AS blend_10,
       MAX(s.rps) FILTER (WHERE s.model = 'blend_15') AS blend_15
FROM v_scores s
JOIN v_runs r USING (run_label)
WHERE r.season = '2025-26' AND r.gameweek > 0
GROUP BY r.gameweek
ORDER BY r.gameweek;


SELECT CASE WHEN run_gameweek <= 10 THEN '1 early (GW3-10)'
            WHEN run_gameweek <= 22 THEN '2 mid (GW15-22)'
            ELSE '3 late (GW26-34)' END AS phase,
       model, COUNT(*) AS predictions, ROUND(AVG(rps), 4) AS avg_rps
FROM v_scored_predictions
WHERE season = '2025-26' AND run_gameweek > 0
  AND model IN ('home_adv', 'blend_5', 'blend_10', 'blend_15')
GROUP BY 1, 2
ORDER BY 1, avg_rps;



SELECT model, COUNT(*) AS predictions, ROUND(AVG(rps), 4) AS avg_rps
FROM v_scored_predictions
WHERE season = '2025-26' AND run_gameweek > 0
  AND model IN ('home_adv', 'blend_5', 'blend_10', 'blend_15')
GROUP BY model
ORDER BY avg_rps;



