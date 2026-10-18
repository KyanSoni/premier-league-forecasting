INSERT INTO model_variants (variant, description, home_factor, away_factor, blend_k)
SELECT 'blend_' || w.k, 'Home advantage + blend weight ' || w.k,
       v.home_factor, v.away_factor, w.k
FROM model_variants v
CROSS JOIN (VALUES (5), (10), (15)) AS w(k)
WHERE v.variant = 'home_adv';

SELECT vr.variant, t.team_name, ROUND(vr.att, 3) AS att, ROUND(vr.def, 3) AS def
FROM v_variant_ratings vr
JOIN teams t  USING (team_id)
JOIN v_runs r USING (run_id)
WHERE r.run_label = '2026-27 after GW5' AND t.team_name = 'Brighton'
ORDER BY vr.variant;


SELECT run_label, model, matches, brier, rps
FROM v_scores
WHERE model IN ('home_adv', 'blend_5', 'blend_10', 'blend_15')
  AND run_label LIKE '%after GW%'
ORDER BY run_label, rps;