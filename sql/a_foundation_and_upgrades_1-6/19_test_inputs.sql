INSERT INTO promoted_inputs (season_label, team_id, champ_att, champ_def)
SELECT '2025-26', p.team_id, p.champ_att, p.champ_def
FROM promoted_history p JOIN teams t USING (team_id)
WHERE t.team_name IN ('Leeds United', 'Burnley', 'Sunderland')
  AND p.season_label = '2024-25';


  SELECT pi.season_label, t.team_name, ROUND(pi.champ_att, 3) AS att, ROUND(pi.champ_def, 3) AS def
FROM promoted_inputs pi JOIN teams t USING (team_id)
ORDER BY 1, 2;


DELETE FROM promoted_inputs;

INSERT INTO promoted_inputs (season_label, team_id, champ_att, champ_def)
SELECT '2025-26', team_id, champ_att, champ_def
FROM promoted_history WHERE season_label = '2024-25';

INSERT INTO promoted_inputs (season_label, team_id, champ_att, champ_def)
SELECT '2026-27', team_id, champ_att, champ_def
FROM promoted_history WHERE season_label = '2025-26';




INSERT INTO promoted_inputs (season_label, team_id, champ_att, champ_def)
SELECT '2025-26', p.team_id, p.champ_att, p.champ_def
FROM promoted_history p JOIN teams t USING (team_id)
WHERE t.team_name IN ('Leeds United', 'Burnley', 'Sunderland')
  AND p.season_label = '2024-25';



  ROLLBACK;


  DELETE FROM promoted_inputs;


  SELECT COUNT(*) FROM promoted_inputs;


  SELECT season_label, team_id, COUNT(*)
FROM promoted_history
WHERE season_label IN ('2024-25', '2025-26')
GROUP BY 1, 2
HAVING COUNT(*) > 1;


INSERT INTO promoted_inputs (season_label, team_id, champ_att, champ_def)
SELECT '2025-26', team_id, champ_att, champ_def
FROM promoted_history WHERE season_label = '2024-25';


INSERT INTO promoted_inputs (season_label, team_id, champ_att, champ_def)
SELECT '2026-27', team_id, champ_att, champ_def
FROM promoted_history WHERE season_label = '2025-26';


SELECT pi.season_label, t.team_name, ROUND(pi.champ_att, 3) AS att, ROUND(pi.champ_def, 3) AS def
FROM promoted_inputs pi JOIN teams t USING (team_id)
ORDER BY 1, 2;