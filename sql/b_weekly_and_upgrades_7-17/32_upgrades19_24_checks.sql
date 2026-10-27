-- =====================================================================
-- 32  Upgrades 19-24: checks to run in pgAdmin
-- ---------------------------------------------------------------------
-- Nothing here changes data. The new tables/views are made by the Python
-- scripts and weekly.py; these queries let you see the results.
-- =====================================================================

-- 19.4  The main model is now built every week: expect 380 per 2026-27 run
--       (and the same for ens_dcxg_50_cal once CAL_POWER is set)
SELECT source, run_label, COUNT(*) AS predictions
FROM external_predictions
WHERE source IN ('ens_dcxg_50', 'ens_dcxg_50_cal') AND run_label LIKE '2026-27%'
GROUP BY source, run_label ORDER BY run_label, source;

-- 19.5  Calibrated vs uncalibrated vs bookmakers, both test seasons (lower RPS is better)
SELECT model,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2024-25') / SUM(matches) FILTER (WHERE season = '2024-25'))::NUMERIC, 4) AS rps_2024_25,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2025-26') / SUM(matches) FILTER (WHERE season = '2025-26'))::NUMERIC, 4) AS rps_2025_26,
       SUM(matches) AS predictions
FROM v_scores
WHERE season IN ('2024-25', '2025-26') AND run_label LIKE '%after GW%'
  AND model IN ('ens_dcxg_50', 'ens_dcxg_50_cal')
GROUP BY model
UNION ALL
SELECT 'bookmaker', ROUND(AVG(rps) FILTER (WHERE season = '2024-25'), 4),
       ROUND(AVG(rps) FILTER (WHERE season = '2025-26'), 4), COUNT(*)
FROM v_scored_predictions WHERE model = 'bookmaker' AND season IN ('2024-25', '2025-26')
ORDER BY 2;

-- 20.4 / 21.3  This week's logged prices with the new columns: blended chance uses the fitted
--              weight, kelly_stake_pct is the suggested stake (% of your betting bank)
SELECT fixture, bet, flag, model_pct, bookie_fair_pct, blended_pct, taken_odds, edge_pct, kelly_stake_pct
FROM v_value_log
WHERE settled_at IS NULL
ORDER BY edge_pct DESC;

-- 21.4  The closing-line test: exp_roi_close_pct, t_stat and the verdict for each flag group
SELECT flag_group, prices_logged, settled, avg_edge_pct, exp_roi_close_pct, t_stat, verdict,
       beat_close_pct, roi_pct
FROM v_value_summary;

-- 21.5  Last week's settled prices: what each was worth at the close
SELECT fixture, bet, flag, taken_odds, closing_odds, exp_roi_close_pct, won, profit_1u
FROM v_value_log
WHERE settled_at > now() - INTERVAL '10 days'
ORDER BY exp_roi_close_pct DESC;

-- 24.6  After prop_audit.py and player_markets.py next: the upcoming player chances moved
--       (compare with last week's numbers on the Player markets sheet)
SELECT market, line, ROUND(100 * AVG(prob), 1) AS avg_chance_pct, COUNT(*) AS rows
FROM player_markets
WHERE mode = 'next' AND market IN ('fouls', 'fouled', 'shots', 'sot') AND line = 1
GROUP BY market, line ORDER BY market;
