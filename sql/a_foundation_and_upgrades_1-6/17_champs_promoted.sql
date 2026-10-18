SELECT clubs,
       ROUND(att_slope::NUMERIC, 3) AS att_slope, ROUND(att_intercept::NUMERIC, 3) AS att_intercept,
       ROUND(att_corr::NUMERIC, 3)  AS att_corr,
       ROUND(def_slope::NUMERIC, 3) AS def_slope, ROUND(def_intercept::NUMERIC, 3) AS def_intercept,
       ROUND(def_corr::NUMERIC, 3)  AS def_corr
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

SELECT * FROM v_promoted_check ORDER BY season_label DESC;