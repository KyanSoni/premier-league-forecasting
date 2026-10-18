DROP VIEW IF EXISTS v_model_accuracy, v_position_probs, v_result_split,
                    v_sim_summary, v_predicted_vs_actual, v_rating_changes, v_runs;

-- 1. Helper: season and gameweek for each run (must be created first)
CREATE VIEW v_runs AS
SELECT r.run_id, r.season_id, s.label AS season, r.run_label, r.run_date,
       CASE WHEN r.run_label ILIKE '%preseason%' THEN 0
            ELSE SUBSTRING(r.run_label FROM 'GW([0-9]+)')::INT END AS gameweek
FROM model_runs r JOIN seasons s USING (season_id);

-- 2. Simulation summary
CREATE VIEW v_sim_summary AS
SELECT r.season, r.gameweek, r.run_label, r.run_date, t.team_name,
       ROUND(AVG(final_position), 1) AS avg_finish,
       ROUND(100.0 * AVG(CASE WHEN final_position = 1   THEN 1 ELSE 0 END), 1) AS title_pct,
       ROUND(100.0 * AVG(CASE WHEN final_position <= 4  THEN 1 ELSE 0 END), 1) AS top4_pct,
       ROUND(100.0 * AVG(CASE WHEN final_position >= 18 THEN 1 ELSE 0 END), 1) AS relegation_pct
FROM sim_results s
JOIN v_runs r USING (run_id)
JOIN teams t  USING (team_id)
GROUP BY r.season, r.gameweek, r.run_label, r.run_date, t.team_name;

-- 3. Predicted vs actual finish
CREATE VIEW v_predicted_vs_actual AS
SELECT r.season, r.gameweek, r.run_label, t.team_name,
       ROUND(AVG(s.final_position), 1)                   AS predicted_finish,
       f.position                                        AS actual_finish,
       ROUND(AVG(s.final_position) - f.position, 1)      AS error,
       ROUND(ABS(AVG(s.final_position) - f.position), 1) AS abs_error
FROM sim_results s
JOIN v_runs r       USING (run_id)
JOIN final_tables f ON f.season_id = r.season_id AND f.team_id = s.team_id
JOIN teams t        ON t.team_id = s.team_id
GROUP BY r.season, r.gameweek, r.run_label, t.team_name, f.position;

-- 4. Rating changes, now ordered by gameweek
CREATE VIEW v_rating_changes AS
SELECT r.season, r.gameweek, r.run_label, r.run_date, t.team_name, tr.att, tr.def,
       ROUND(tr.att - LAG(tr.att) OVER w, 3) AS att_change,
       ROUND(tr.def - LAG(tr.def) OVER w, 3) AS def_change
FROM team_ratings tr
JOIN v_runs r USING (run_id)
JOIN teams t  USING (team_id)
WINDOW w AS (PARTITION BY tr.team_id, r.season ORDER BY r.gameweek);

-- 5. Accuracy score per run (built on view 3)
CREATE VIEW v_model_accuracy AS
SELECT season, gameweek, run_label,
       ROUND(AVG(abs_error), 2)                        AS avg_places_out,
       SUM(CASE WHEN abs_error <= 2 THEN 1 ELSE 0 END) AS teams_within_2_places
FROM v_predicted_vs_actual
GROUP BY season, gameweek, run_label;

-- 6. Finishing position probabilities (replaces the Analysis sheets)
CREATE VIEW v_position_probs AS
SELECT r.season, r.gameweek, r.run_label, t.team_name, s.final_position,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY s.run_id, s.team_id), 1) AS pct
FROM sim_results s
JOIN v_runs r USING (run_id)
JOIN teams t  USING (team_id)
GROUP BY r.season, r.gameweek, r.run_label, t.team_name, s.run_id, s.team_id, s.final_position;

-- 7. Home vs away results
CREATE VIEW v_result_split AS
SELECT s.label AS season,
       COUNT(*) FILTER (WHERE home_goals > away_goals) AS home_wins,
       COUNT(*) FILTER (WHERE home_goals = away_goals) AS draws,
       COUNT(*) FILTER (WHERE home_goals < away_goals) AS away_wins,
       ROUND(AVG(home_goals), 2) AS avg_home_goals,
       ROUND(AVG(away_goals), 2) AS avg_away_goals
FROM matches m JOIN seasons s USING (season_id)
WHERE home_goals IS NOT NULL
GROUP BY s.label;
