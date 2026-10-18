SELECT run_gameweek AS gameweek,
       COUNT(*) FILTER (WHERE model = 'home_adv') AS matches,
       ROUND(AVG(rps) FILTER (WHERE model = 'home_adv'), 4) AS home_adv,
       ROUND(AVG(rps) FILTER (WHERE model = 'blend_5'),  4) AS blend_5,
       ROUND(AVG(rps) FILTER (WHERE model = 'blend_10'), 4) AS blend_10,
       ROUND(AVG(rps) FILTER (WHERE model = 'blend_15'), 4) AS blend_15
FROM v_scored_predictions
WHERE season = '2025-26' AND run_gameweek > 0
  AND model IN ('home_adv', 'blend_5', 'blend_10', 'blend_15')
GROUP BY run_gameweek
ORDER BY run_gameweek;




SELECT CASE WHEN run_gameweek <= 10 THEN '1 early (GW3-10)'
            WHEN run_gameweek <= 22 THEN '2 mid (GW15-22)'
            ELSE '3 late (GW26-34)' END AS phase,
       model, COUNT(*) AS predictions, ROUND(AVG(rps), 4) AS avg_rps
FROM v_scored_predictions
WHERE season = '2025-26' AND run_gameweek > 0
  AND model IN ('home_adv', 'blend_5', 'blend_10', 'blend_15')
GROUP BY 1, model
ORDER BY 1, avg_rps;




SELECT model, COUNT(*) AS predictions, ROUND(AVG(rps), 4) AS avg_rps
FROM v_scored_predictions
WHERE season = '2025-26' AND run_gameweek > 0
  AND model IN ('home_adv', 'blend_5', 'blend_10', 'blend_15')
GROUP BY model
ORDER BY avg_rps;