-- =====================================================================
-- UPGRADES 11 and 12: compare season simulations and benchmark models
-- =====================================================================

-- 11.1  Check what the script stored (expect 4 sources x 22 runs, 20 teams each = 440 rows per source)
SELECT source, COUNT(DISTINCT run_label) AS runs, COUNT(*) AS team_rows
FROM dyn_sim_summary GROUP BY source ORDER BY source;

-- 11.2  Sanity check: in every run the title chances add up to 100 and relegation to 300
--       (expect 0 rows: anything listed is a broken run)
SELECT source, run_label, ROUND(SUM(title_pct), 1) AS title_total, ROUND(SUM(relegation_pct), 1) AS releg_total
FROM dyn_sim_summary GROUP BY source, run_label
HAVING ABS(SUM(title_pct) - 100) > 1 OR ABS(SUM(relegation_pct) - 300) > 1;

-- 11.3  The main test: which amount of uncertainty forecasts the final table best?
--       Both seasons, lower = better in every column. Decide on position_rps.
SELECT s.source, s.season, COUNT(*) AS runs,
       ROUND(AVG(s.title_brier), 4)      AS title_brier,
       ROUND(AVG(s.top4_brier), 4)       AS top4_brier,
       ROUND(AVG(s.relegation_brier), 4) AS relegation_brier,
       ROUND(AVG(s.avg_places_out), 2)   AS places_out,
       ROUND(AVG(p.position_rps), 4)     AS position_rps
FROM v_season_scores s
JOIN v_position_rps p USING (source, run_label)
WHERE s.source LIKE 'dyn_%' AND s.season IN ('2024-25', '2025-26')
GROUP BY s.source, s.season
ORDER BY s.season, position_rps;

-- 11.4  Against your old Excel sims and Dixon-Coles on the same 2025-26 runs
SELECT s.source, COUNT(*) AS runs,
       ROUND(AVG(s.title_brier), 4)      AS title_brier,
       ROUND(AVG(s.top4_brier), 4)       AS top4_brier,
       ROUND(AVG(s.relegation_brier), 4) AS relegation_brier,
       ROUND(AVG(s.avg_places_out), 2)   AS places_out
FROM v_season_scores s
WHERE s.season = '2025-26'
  AND s.run_label IN (SELECT run_label FROM v_season_scores WHERE source = 'excel')
  AND s.run_label IN (SELECT run_label FROM v_season_scores WHERE source = 'dixon_coles')
  AND (s.source IN ('excel', 'dixon_coles') OR s.source LIKE 'dyn_%')
GROUP BY s.source ORDER BY top4_brier;

-- 11.5  Calibration of top-4 calls, fixed (dyn_0) vs uncertain (dyn_10), both seasons.
--       Compare with Upgrade 8: avg_forecast_pct should be closer to actual_pct.
WITH ev AS (
  SELECT d.source, d.top4_pct, (f.position <= 4)::INT AS hit
  FROM dyn_sim_summary d
  JOIN v_runs r       ON r.run_label = d.run_label
  JOIN seasons se     ON se.label = r.season
  JOIN teams t        ON t.team_name = d.team_name
  JOIN final_tables f ON f.season_id = se.season_id AND f.team_id = t.team_id
  WHERE d.source IN ('dyn_0', 'dyn_10')
)
SELECT source, FLOOR(top4_pct::NUMERIC / 20) * 20 AS band_from_pct, COUNT(*) AS forecasts,
       ROUND(AVG(top4_pct)::NUMERIC, 1) AS avg_forecast_pct, ROUND(100.0 * AVG(hit), 1) AS actual_pct
FROM ev GROUP BY source, 2 ORDER BY source, 2;

-- 11.6  How different are the live 2026-27 forecasts? (newest run; fixed vs 10% uncertainty)
SELECT a.team_name, a.title_pct AS fixed_title, b.title_pct AS uncertain_title,
       a.top4_pct AS fixed_top4, b.top4_pct AS uncertain_top4,
       a.relegation_pct AS fixed_releg, b.relegation_pct AS uncertain_releg
FROM dyn_sim_summary a
JOIN dyn_sim_summary b ON b.run_label = a.run_label AND b.team_name = a.team_name AND b.source = 'dyn_10'
WHERE a.source = 'dyn_0'
  AND a.run_label = (SELECT run_label FROM v_runs WHERE season = '2026-27' ORDER BY gameweek DESC LIMIT 1)
ORDER BY a.title_pct DESC, a.top4_pct DESC;

-- 11.7  Tidy up after deciding: keep dyn_0 and the winner, delete the rest (change the names)
-- DELETE FROM dyn_sim_summary    WHERE source IN ('dyn_5', 'dyn_15');
-- DELETE FROM dyn_position_probs WHERE source IN ('dyn_5', 'dyn_15');

-- 12.1  After python benchmarks.py: rows stored per run (expect 380 each; skipped runs are missing)
SELECT source, COUNT(DISTINCT run_label) AS runs, COUNT(*) AS predictions
FROM external_predictions
WHERE source IN ('pi_ratings', 'dc_xg')
GROUP BY source ORDER BY source;

-- 12.2  Sanity check: home + draw + away should add to 1 (expect 0 rows)
SELECT source, run_label, match_id, p_home + p_draw + p_away AS total
FROM external_predictions
WHERE source IN ('pi_ratings', 'dc_xg') AND ABS(p_home + p_draw + p_away - 1) > 0.002
LIMIT 20;

-- 12.3  The benchmark table: every model on exactly the same matches, both seasons
--       (runs where all of them have predictions; lower = better)
SELECT model,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2024-25') / SUM(matches) FILTER (WHERE season = '2024-25'))::NUMERIC, 4) AS rps_2024_25,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2025-26') / SUM(matches) FILTER (WHERE season = '2025-26'))::NUMERIC, 4) AS rps_2025_26,
       ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS rps_both,
       SUM(matches) AS matches
FROM v_scores
WHERE season IN ('2024-25', '2025-26') AND run_label LIKE '%after GW%'
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'pi_ratings')
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'dc_xg')
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'dixon_coles')
  AND model IN ('base', 'xg_50', 'ens_dc_25', 'dixon_coles', 'dc_xg', 'pi_ratings', 'market')
GROUP BY model ORDER BY rps_both;

-- 12.4  The same models against the bookmakers on the same matches (2025-26)
SELECT model, SUM(matches) AS matches,
       ROUND((SUM(model_rps * matches) / SUM(matches))::NUMERIC, 4)     AS model_rps,
       ROUND((SUM(bookmaker_rps * matches) / SUM(matches))::NUMERIC, 4) AS bookmaker_rps
FROM v_vs_bookmaker
WHERE season = '2025-26' AND run_label LIKE '%after GW%'
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'pi_ratings')
  AND model IN ('xg_50', 'ens_dc_25', 'dixon_coles', 'dc_xg', 'pi_ratings', 'market')
GROUP BY model ORDER BY model_rps;

-- 12.5  Optional: ensembles of the main model with dc_xg (same idea as 9.3, swapping Dixon-Coles for dc_xg)
INSERT INTO external_predictions (source, run_label, match_id, p_home, p_draw, p_away)
SELECT 'ens_dcxg_' || (w.w * 100)::INT, m.run_label, m.match_id,
       ROUND(((1 - w.w) * m.p_home + w.w * d.p_home)::NUMERIC, 4),
       ROUND(((1 - w.w) * m.p_draw + w.w * d.p_draw)::NUMERIC, 4),
       ROUND(((1 - w.w) * m.p_away + w.w * d.p_away)::NUMERIC, 4)
FROM v_main_predictions m
JOIN external_predictions d ON d.source = 'dc_xg' AND d.run_label = m.run_label AND d.match_id = m.match_id
CROSS JOIN (VALUES (0.25), (0.5)) AS w(w)
ON CONFLICT (source, run_label, match_id)
DO UPDATE SET p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away;

-- 12.6  Do they beat ens_dc_25 in BOTH seasons?
SELECT model,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2024-25') / SUM(matches) FILTER (WHERE season = '2024-25'))::NUMERIC, 4) AS rps_2024_25,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2025-26') / SUM(matches) FILTER (WHERE season = '2025-26'))::NUMERIC, 4) AS rps_2025_26,
       ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS rps_both,
       SUM(matches) AS matches
FROM v_scores
WHERE season IN ('2024-25', '2025-26') AND run_label LIKE '%after GW%'
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'dc_xg')
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'dixon_coles')
  AND model IN ('xg_50', 'ens_dc_25', 'dc_xg', 'ens_dcxg_25', 'ens_dcxg_50')
GROUP BY model ORDER BY rps_both;
