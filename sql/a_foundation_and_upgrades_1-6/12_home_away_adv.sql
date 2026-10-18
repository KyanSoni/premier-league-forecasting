SELECT COUNT(*) AS matches,
       ROUND(AVG(home_goals), 3) AS avg_home_goals,
       ROUND(AVG(away_goals), 3) AS avg_away_goals,
       ROUND(AVG(home_goals) / ((AVG(home_goals) + AVG(away_goals)) / 2), 3) AS home_factor,
       ROUND(AVG(away_goals) / ((AVG(home_goals) + AVG(away_goals)) / 2), 3) AS away_factor
FROM matches m JOIN seasons s USING (season_id)
WHERE s.label = '2025-26' AND m.home_goals IS NOT NULL;


INSERT INTO model_variants (variant, description, home_factor, away_factor)
VALUES ('home_adv', 'Home advantage from 2025-26 results', 1.110, 0.890);


SELECT run_label, model, matches, brier, rps, pct_fav_correct
FROM v_scores
WHERE model IN ('base', 'home_adv')
ORDER BY run_label, model;