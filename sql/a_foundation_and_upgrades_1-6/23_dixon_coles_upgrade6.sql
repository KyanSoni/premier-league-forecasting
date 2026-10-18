SELECT model, SUM(matches) AS matches,
       ROUND(SUM(rps * matches) / SUM(matches), 4) AS rps
FROM v_scores
WHERE season = '2025-26'
  AND run_label IN (SELECT run_label FROM v_scores
                    WHERE model = 'dixon_coles' AND season = '2025-26')
  AND model IN ('dixon_coles', 'blend_15', 'xg_50')
GROUP BY model
ORDER BY rps;




SELECT model, matches, rps
FROM v_scores
WHERE run_label = '2026-27 preseason'
  AND model IN ('dixon_coles', 'blend_15', 'xg_50', 'bookmaker')
ORDER BY rps;





SELECT team_name, attack, defence, home_adv, rho
FROM dc_team_ratings
WHERE run_label = '2026-27 after GW5'
ORDER BY attack DESC;





CREATE OR REPLACE VIEW v_title_compare AS
SELECT s.season, s.run_label, s.team_name,
       s.title_pct      AS model_title_pct,      d.title_pct      AS dc_title_pct,
       s.top4_pct       AS model_top4_pct,       d.top4_pct       AS dc_top4_pct,
       s.relegation_pct AS model_relegation_pct, d.relegation_pct AS dc_relegation_pct
FROM v_sim_summary s
JOIN dc_sim_summary d ON d.run_label = s.run_label AND d.team_name = s.team_name;




SELECT * FROM v_title_compare
WHERE run_label = '2026-27 after GW5'
ORDER BY dc_title_pct DESC;