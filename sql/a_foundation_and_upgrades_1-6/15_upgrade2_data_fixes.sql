WITH tm AS (
  SELECT m.home_team_id AS team_id, m.home_goals AS gf, m.away_goals AS ga
  FROM matches m JOIN seasons s USING (season_id)
  WHERE s.label = '2025-26' AND m.round_no <= 22 AND m.home_goals IS NOT NULL
  UNION ALL
  SELECT m.away_team_id, m.away_goals, m.home_goals
  FROM matches m JOIN seasons s USING (season_id)
  WHERE s.label = '2025-26' AND m.round_no <= 22 AND m.home_goals IS NOT NULL
),
lg AS (SELECT SUM(gf)::NUMERIC / COUNT(*) AS avg_goals FROM tm),
calc AS (
  SELECT tm.team_id, COUNT(*) AS games, SUM(gf) AS gf, SUM(ga) AS ga,
         1 + ((SUM(gf)::NUMERIC / COUNT(*)) / lg.avg_goals - 1) * 0.75 AS att,
         1 + ((SUM(ga)::NUMERIC / COUNT(*)) / lg.avg_goals - 1) * 0.75 AS def
  FROM tm CROSS JOIN lg
  GROUP BY tm.team_id, lg.avg_goals
)
SELECT t.team_name, c.games, c.gf, c.ga,
       ROUND(c.att, 3) AS should_be_att, ROUND(tr.att, 3) AS stored_att,
       ROUND(c.def, 3) AS should_be_def, ROUND(tr.def, 3) AS stored_def
FROM calc c
JOIN teams t USING (team_id)
LEFT JOIN team_ratings tr
       ON tr.team_id = c.team_id
      AND tr.run_id = (SELECT run_id FROM model_runs WHERE run_label = '2025-26 after GW22')
ORDER BY t.team_name;




   DELETE FROM team_ratings
   WHERE run_id = (SELECT run_id FROM model_runs WHERE run_label = '2025-26 after GW22');
   DELETE FROM sim_results
   WHERE run_id = (SELECT run_id FROM model_runs WHERE run_label = '2025-26 after GW22');
   TRUNCATE stg_ratings;
   TRUNCATE stg_sims;




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




   WITH tm AS (
  SELECT m.home_team_id AS team_id, m.home_goals AS gf, m.away_goals AS ga
  FROM matches m JOIN seasons s USING (season_id)
  WHERE s.label = '2025-26' AND m.round_no <= 22 AND m.home_goals IS NOT NULL
  UNION ALL
  SELECT m.away_team_id, m.away_goals, m.home_goals
  FROM matches m JOIN seasons s USING (season_id)
  WHERE s.label = '2025-26' AND m.round_no <= 22 AND m.home_goals IS NOT NULL
),
lg AS (SELECT SUM(gf)::NUMERIC / COUNT(*) AS avg_goals FROM tm),
calc AS (
  SELECT tm.team_id, COUNT(*) AS games, SUM(gf) AS gf, SUM(ga) AS ga,
         1 + ((SUM(gf)::NUMERIC / COUNT(*)) / lg.avg_goals - 1) * 0.75 AS att,
         1 + ((SUM(ga)::NUMERIC / COUNT(*)) / lg.avg_goals - 1) * 0.75 AS def
  FROM tm CROSS JOIN lg
  GROUP BY tm.team_id, lg.avg_goals
)
SELECT t.team_name, c.games, c.gf, c.ga,
       ROUND(c.att, 3) AS should_be_att, ROUND(tr.att, 3) AS stored_att,
       ROUND(c.def, 3) AS should_be_def, ROUND(tr.def, 3) AS stored_def,
       (ROUND(c.att, 4) = ROUND(tr.att, 4) AND ROUND(c.def, 4) = ROUND(tr.def, 4)) AS ok
FROM calc c
JOIN teams t USING (team_id)
LEFT JOIN team_ratings tr
       ON tr.team_id = c.team_id
      AND tr.run_id = (SELECT run_id FROM model_runs WHERE run_label = '2025-26 after GW22')
ORDER BY t.team_name;