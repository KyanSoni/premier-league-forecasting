SELECT t.team_name, p.season_label, p.champ_att, p.epl_att
FROM promoted_history p JOIN teams t USING (team_id)
WHERE t.team_name IN ('Leeds United', 'Burnley', 'Sunderland')
ORDER BY p.season_label DESC;



INSERT INTO promoted_inputs (season_label, team_id, champ_att, champ_def)
SELECT '2025-26', p.team_id, p.champ_att, p.champ_def
FROM promoted_history p JOIN teams t USING (team_id)
WHERE t.team_name IN ('Leeds United', 'Burnley', 'Sunderland')
  AND p.season_label = '2024-25';



  UPDATE promoted_history p
SET epl_att = (42.0/38) / (1045.0/760)
FROM teams t
WHERE t.team_id = p.team_id AND t.team_name = 'Sunderland' AND p.season_label = '2024-25';



UPDATE promoted_history SET epl_att = NULL, epl_def = NULL WHERE season_label = '2025-26';



SELECT season_label, COUNT(*) AS clubs, COUNT(epl_att) AS with_epl
FROM promoted_history GROUP BY season_label ORDER BY season_label DESC LIMIT 3;









CREATE OR REPLACE VIEW v_promoted_fit AS
SELECT REGR_COUNT(epl_att, champ_att)     AS clubs,
       REGR_SLOPE(epl_att, champ_att)     AS att_slope,
       REGR_INTERCEPT(epl_att, champ_att) AS att_intercept,
       CORR(epl_att, champ_att)           AS att_corr,
       REGR_SLOPE(epl_def, champ_def)     AS def_slope,
       REGR_INTERCEPT(epl_def, champ_def) AS def_intercept,
       CORR(epl_def, champ_def)           AS def_corr
FROM promoted_history;


SELECT
  (SELECT clubs FROM v_promoted_fit)                                   AS clubs_in_view,
  (SELECT ROUND(att_slope::NUMERIC, 3) FROM v_promoted_fit)            AS att_slope,
  (SELECT COUNT(*) FROM promoted_history)                              AS total_rows,
  (SELECT COUNT(epl_att) FROM promoted_history)                        AS rows_with_epl,
  (SELECT COUNT(*) FROM promoted_history WHERE season_label = '2025-26' AND epl_att IS NULL) AS blanked_2025_26,
  (SELECT ROUND(MAX(epl_att), 3) FROM promoted_history p JOIN teams t USING (team_id)
    WHERE t.team_name LIKE 'Sunderland%' AND p.season_label = '2024-25') AS sunderland_att;




	SELECT clubs,
       ROUND(att_slope::NUMERIC, 3)     AS att_slope,
       ROUND(att_intercept::NUMERIC, 3) AS att_intercept,
       ROUND(att_corr::NUMERIC, 3)      AS att_corr,
       ROUND(def_slope::NUMERIC, 3)     AS def_slope,
       ROUND(def_intercept::NUMERIC, 3) AS def_intercept,
       ROUND(def_corr::NUMERIC, 3)      AS def_corr
FROM v_promoted_fit;




CREATE OR REPLACE VIEW v_promoted_check AS
SELECT t.team_name, p.season_label,
       p.champ_att, p.epl_att,
       ROUND((f.att_slope * p.champ_att + f.att_intercept)::NUMERIC, 3) AS predicted_epl_att,
       p.champ_def, p.epl_def,
       ROUND((f.def_slope * p.champ_def + f.def_intercept)::NUMERIC, 3) AS predicted_epl_def
FROM promoted_history p
JOIN teams t USING (team_id)
CROSS JOIN v_promoted_fit f;




SELECT team_name, season_label,
       ROUND(epl_att, 3) AS actual_att, predicted_epl_att,
       ROUND(epl_def, 3) AS actual_def, predicted_epl_def
FROM v_promoted_check
ORDER BY season_label DESC, team_name;









INSERT INTO model_variants (variant, description, home_factor, away_factor, promoted_reg)
SELECT 'promoted_reg', 'Home advantage + regression ratings for promoted clubs',
       home_factor, away_factor, TRUE
FROM model_variants WHERE variant = 'home_adv';



SELECT r.run_label, t.team_name, vr.variant, ROUND(vr.att, 3) AS att, ROUND(vr.def, 3) AS def
FROM v_variant_ratings vr
JOIN teams t USING (team_id)
JOIN v_runs r USING (run_id)
WHERE r.gameweek = 0
  AND vr.variant IN ('home_adv', 'promoted_reg')
  AND team_id IN (SELECT team_id FROM promoted_inputs)
ORDER BY r.run_label, t.team_name, vr.variant;













SELECT variant FROM model_variants WHERE adopted;



SELECT t.team_name, p.season_label, ROUND(p.epl_att, 3) AS epl_att, ROUND(p.epl_def, 3) AS epl_def
FROM promoted_history p JOIN teams t USING (team_id)
WHERE p.season_label = '2023-24'
ORDER BY t.team_name;



UPDATE promoted_history p
SET epl_att = (v.gf / 38.0) / (1115.0/760),
    epl_def = (v.ga / 38.0) / (1115.0/760)
FROM (VALUES ('Leicester%', 33, 80), ('Ipswich%', 36, 82), ('Southampton%', 26, 86)) AS v(name, gf, ga),
     teams t
WHERE t.team_id = p.team_id
  AND t.team_name LIKE v.name
  AND p.season_label = '2023-24';




  SELECT t.team_name, p.season_label, ROUND(p.epl_att, 3) AS epl_att, ROUND(p.epl_def, 3) AS epl_def
FROM promoted_history p JOIN teams t USING (team_id)
WHERE p.season_label = '2023-24'
ORDER BY t.team_name;





SELECT clubs,
       ROUND(att_slope::NUMERIC, 3)     AS att_slope,
       ROUND(att_intercept::NUMERIC, 3) AS att_intercept,
       ROUND(att_corr::NUMERIC, 3)      AS att_corr,
       ROUND(def_slope::NUMERIC, 3)     AS def_slope,
       ROUND(def_intercept::NUMERIC, 3) AS def_intercept,
       ROUND(def_corr::NUMERIC, 3)      AS def_corr
FROM v_promoted_fit;




SELECT run_label, model, matches, brier, rps
FROM v_scores
WHERE model IN ('home_adv', 'promoted_reg') AND run_label LIKE '%preseason'
ORDER BY run_label, rps;




SELECT sp.run_label, sp.model, COUNT(*) AS matches, ROUND(AVG(sp.rps), 4) AS rps
FROM v_scored_predictions sp
JOIN matches m ON m.match_id = sp.match_id
JOIN seasons s ON s.season_id = m.season_id
JOIN promoted_inputs pi ON pi.season_label = s.label
                       AND pi.team_id IN (m.home_team_id, m.away_team_id)
WHERE sp.model IN ('home_adv', 'promoted_reg') AND sp.run_label LIKE '%preseason'
GROUP BY sp.run_label, sp.model
ORDER BY sp.run_label, rps;



