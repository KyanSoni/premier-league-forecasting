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