CREATE VIEW v_league_table AS
WITH tm AS (
  SELECT season_id, home_team_id AS team_id, home_goals AS gf, away_goals AS ga
  FROM matches WHERE home_goals IS NOT NULL
  UNION ALL
  SELECT season_id, away_team_id, away_goals, home_goals
  FROM matches WHERE home_goals IS NOT NULL
),
agg AS (
  SELECT season_id, team_id,
         COUNT(*) AS played,
         SUM(CASE WHEN gf > ga THEN 1 ELSE 0 END) AS won,
         SUM(CASE WHEN gf = ga THEN 1 ELSE 0 END) AS drawn,
         SUM(CASE WHEN gf < ga THEN 1 ELSE 0 END) AS lost,
         SUM(gf) AS gf, SUM(ga) AS ga, SUM(gf - ga) AS gd,
         SUM(CASE WHEN gf > ga THEN 3 WHEN gf = ga THEN 1 ELSE 0 END) AS pts
  FROM tm
  GROUP BY season_id, team_id
)
SELECT s.label AS season,
       RANK() OVER (PARTITION BY a.season_id ORDER BY pts DESC, gd DESC, gf DESC) AS position,
       t.team_name, played, won, drawn, lost, gf, ga, gd, pts
FROM agg a
JOIN teams t   USING (team_id)
JOIN seasons s USING (season_id);



CREATE VIEW v_sim_summary AS
SELECT r.run_label, r.run_date, t.team_name,
       ROUND(AVG(final_position), 1) AS avg_finish,
       ROUND(100.0 * AVG(CASE WHEN final_position = 1   THEN 1 ELSE 0 END), 1) AS title_pct,
       ROUND(100.0 * AVG(CASE WHEN final_position <= 4  THEN 1 ELSE 0 END), 1) AS top4_pct,
       ROUND(100.0 * AVG(CASE WHEN final_position >= 18 THEN 1 ELSE 0 END), 1) AS relegation_pct
FROM sim_results s
JOIN teams t      USING (team_id)
JOIN model_runs r USING (run_id)
GROUP BY r.run_label, r.run_date, t.team_name;



CREATE VIEW v_predicted_vs_actual AS
SELECT r.run_label, t.team_name,
       ROUND(AVG(s.final_position), 1)              AS predicted_finish,
       f.position                                   AS actual_finish,
       ROUND(AVG(s.final_position) - f.position, 1) AS error
FROM sim_results s
JOIN model_runs r   USING (run_id)
JOIN final_tables f ON f.season_id = r.season_id AND f.team_id = s.team_id
JOIN teams t        ON t.team_id = s.team_id
GROUP BY r.run_label, t.team_name, f.position;



SELECT run_label, ROUND(AVG(ABS(error)), 2) AS avg_places_out
FROM v_predicted_vs_actual




CREATE VIEW v_rating_changes AS
SELECT r.run_label, r.run_date, t.team_name, tr.att, tr.def,
       tr.att - LAG(tr.att) OVER (PARTITION BY tr.team_id, r.season_id ORDER BY r.run_id) AS att_change,
       tr.def - LAG(tr.def) OVER (PARTITION BY tr.team_id, r.season_id ORDER BY r.run_id) AS def_change
FROM team_ratings tr
JOIN model_runs r USING (run_id)
JOIN teams t      USING (team_id);
GROUP BY run_label;