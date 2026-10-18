-- =====================================================================
-- WEEKLY PART 2 (Stage E): load the new run (ratings.csv, sims.csv)
-- Run each block on its own. No run label needed except in F1 fixes.
-- =====================================================================

-- E1  Empty staging
TRUNCATE stg_ratings, stg_sims;

-- E2  Now import ratings.csv -> stg_ratings and sims.csv -> stg_sims

-- E3  What arrived (expect ratings 20, sims 20000)
SELECT 'ratings' AS tbl, COUNT(*) FROM stg_ratings
UNION ALL SELECT 'sims', COUNT(*) FROM stg_sims;

-- E4  Drop anything already loaded (expect DELETE 0 twice)
DELETE FROM stg_sims    WHERE run_label IN (SELECT run_label FROM model_runs);
DELETE FROM stg_ratings WHERE run_label IN (SELECT run_label FROM model_runs);

-- E5  Labels match (expect two rows, same label: sims 20000, ratings 20)
SELECT 'sims' AS file, run_label, COUNT(*) FROM stg_sims GROUP BY run_label
UNION ALL
SELECT 'ratings', run_label, COUNT(*) FROM stg_ratings GROUP BY run_label;

-- E6  Create the run (expect INSERT 0 1)
INSERT INTO model_runs (season_id, run_label)
SELECT s.season_id, r.run_label
FROM (SELECT DISTINCT run_label FROM stg_sims) r
JOIN seasons s ON s.label = LEFT(r.run_label, 7);

-- E7  Ratings (expect INSERT 0 20)
INSERT INTO team_ratings (run_id, team_id, att, def)
SELECT m.run_id, a.team_id, r.att::NUMERIC, r.def::NUMERIC
FROM stg_ratings r
JOIN model_runs m   ON m.run_label = r.run_label
JOIN team_aliases a ON a.alias = TRIM(r.team);

-- E8  Simulations (expect INSERT 0 20000)
INSERT INTO sim_results (run_id, sim_no, team_id, final_position)
SELECT m.run_id, s.sim_no::INT, a.team_id, s.position::INT
FROM stg_sims s
JOIN model_runs m   ON m.run_label = s.run_label
JOIN team_aliases a ON a.alias = TRIM(s.team);

-- E9  Every run complete (expect 20000 / 20 and a gameweek number on every row)
SELECT r.run_label, r.gameweek,
       (SELECT COUNT(*) FROM sim_results  s WHERE s.run_id = r.run_id) AS sims,
       (SELECT COUNT(*) FROM team_ratings t WHERE t.run_id = r.run_id) AS ratings
FROM v_runs r
ORDER BY r.season, r.gameweek;

-- E10 Sense-check the new forecast (note the leader's title_pct)
SELECT team_name, title_pct, top4_pct, relegation_pct
FROM v_sim_summary
WHERE run_label = (SELECT run_label FROM v_runs
                   WHERE season = '2026-27' ORDER BY gameweek DESC LIMIT 1)
ORDER BY title_pct DESC
LIMIT 6;

-- ---------------------------------------------------------------
-- AFTER running python dc_model.py (Stage F):
-- F2  Dixon-Coles saved the new run (expect 20 | 20 | 380)
SELECT (SELECT COUNT(*) FROM dc_team_ratings WHERE run_label = r.run_label) AS dc_ratings,
       (SELECT COUNT(*) FROM dc_sim_summary  WHERE run_label = r.run_label) AS dc_sims,
       (SELECT COUNT(*) FROM external_predictions
         WHERE source = 'dixon_coles' AND run_label = r.run_label)         AS dc_predictions
FROM (SELECT run_label FROM v_runs WHERE season = '2026-27'
      ORDER BY gameweek DESC LIMIT 1) r;

-- F3  How the previous run scored now its next games are played
SELECT run_label, model, matches, rps
FROM v_scores
WHERE season = '2026-27' AND model IN ('xg_50', 'blend_15', 'dixon_coles', 'home_adv')
ORDER BY run_label, rps;
