-- =====================================================================
-- UPGRADES 9 and 10: ensembles (averages of models)
-- Needs: Dixon-Coles (dc_model.py) and, for the market rows, market_model.py.
-- Run each block on its own. Blocks 9.3 and 10.3 are rerun after every new run.
-- =====================================================================

-- 9.1  Your main model's predictions for every run, in one place.
--      Runs before 2026-27 GW6 hold raw goal ratings, so the xg_50 variant rebuilds your
--      Excel model from them. From 2026-27 GW6 the exported ratings (A, J, K) already include
--      blending and xG, so home_adv is the Excel model as it is.
CREATE OR REPLACE VIEW v_main_predictions AS
SELECT vp.run_label, vp.season, vp.run_gameweek, vp.match_id, vp.round_no,
       vp.p_home, vp.p_draw, vp.p_away, vp.result, vp.out_of_sample
FROM v_variant_predictions vp
WHERE vp.variant = CASE WHEN vp.season >= '2026-27' AND vp.run_gameweek >= 6
                        THEN 'home_adv' ELSE 'xg_50' END
  AND vp.variant IN ('home_adv', 'xg_50');

-- 9.2  Check it: one row per fixture per run (expect 380 for each run)
SELECT run_label, COUNT(*) FROM v_main_predictions GROUP BY run_label ORDER BY run_label;

-- 9.3  Ensembles of the main model and Dixon-Coles (25% and 50% Dixon-Coles).
--      Expect INSERT 0 <380 x runs that Dixon-Coles fitted>. Takes a minute.
INSERT INTO external_predictions (source, run_label, match_id, p_home, p_draw, p_away)
SELECT 'ens_dc_' || (w.w * 100)::INT, m.run_label, m.match_id,
       ROUND(((1 - w.w) * m.p_home + w.w * d.p_home)::NUMERIC, 4),
       ROUND(((1 - w.w) * m.p_draw + w.w * d.p_draw)::NUMERIC, 4),
       ROUND(((1 - w.w) * m.p_away + w.w * d.p_away)::NUMERIC, 4)
FROM v_main_predictions m
JOIN external_predictions d ON d.source = 'dixon_coles' AND d.run_label = m.run_label
                           AND d.match_id = m.match_id
CROSS JOIN (VALUES (0.25), (0.5)) AS w(w)
ON CONFLICT (source, run_label, match_id)
DO UPDATE SET p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away;

-- 9.4  Compare on the same runs (2025-26, runs Dixon-Coles fitted)
SELECT model, SUM(matches) AS matches, ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS rps
FROM v_scores
WHERE season = '2025-26' AND run_label LIKE '%after GW%'
  AND run_label IN (SELECT run_label FROM v_scores WHERE model = 'dixon_coles')
  AND model IN ('xg_50', 'dixon_coles', 'ens_dc_25', 'ens_dc_50')
GROUP BY model ORDER BY rps;
-- my rebuild (2025-26 GW5-34, Dixon-Coles with one season of history):
--   xg_50 0.2118 | ens_dc_25 0.2122 | ens_dc_50 0.2131 | dixon_coles 0.2166

-- 9.5  Both seasons side by side (the decision rule: an ensemble must beat xg_50 in BOTH)
SELECT model,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2024-25') / SUM(matches) FILTER (WHERE season = '2024-25'))::NUMERIC, 4) AS rps_2024_25,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2025-26') / SUM(matches) FILTER (WHERE season = '2025-26'))::NUMERIC, 4) AS rps_2025_26,
       ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS rps_both,
       SUM(matches) AS matches
FROM v_scores
WHERE season IN ('2024-25', '2025-26') AND run_label LIKE '%after GW%'
  AND run_label IN (SELECT run_label FROM v_scores WHERE model = 'dixon_coles')
  AND model IN ('xg_50', 'dixon_coles', 'ens_dc_25', 'ens_dc_50')
GROUP BY model ORDER BY rps_both;

-- 10.0  Before running market_model.py: check the bookmaker odds it learns from (expect 380 for each season you loaded)
SELECT s.label AS season, COUNT(*) AS matches_with_odds
FROM external_predictions b
JOIN matches m ON m.match_id = b.match_id
JOIN seasons s ON s.season_id = m.season_id
WHERE b.source = 'bookmaker' AND b.run_label = 'odds'
GROUP BY s.label ORDER BY s.label;

-- 10.1  After market_model.py: rows stored per run (expect 380 each; skipped runs are missing)
SELECT run_label, COUNT(*) FROM external_predictions
WHERE source = 'market' GROUP BY run_label ORDER BY run_label;

-- 10.3  Ensembles with the market-implied model (after running python market_model.py)
INSERT INTO external_predictions (source, run_label, match_id, p_home, p_draw, p_away)
SELECT 'ens_mkt_50', m.run_label, m.match_id,
       ROUND((0.5 * m.p_home + 0.5 * k.p_home)::NUMERIC, 4),
       ROUND((0.5 * m.p_draw + 0.5 * k.p_draw)::NUMERIC, 4),
       ROUND((0.5 * m.p_away + 0.5 * k.p_away)::NUMERIC, 4)
FROM v_main_predictions m
JOIN external_predictions k ON k.source = 'market' AND k.run_label = m.run_label AND k.match_id = m.match_id
ON CONFLICT (source, run_label, match_id)
DO UPDATE SET p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away;

INSERT INTO external_predictions (source, run_label, match_id, p_home, p_draw, p_away)
SELECT 'ens_3way', m.run_label, m.match_id,
       ROUND(((m.p_home + k.p_home + d.p_home) / 3)::NUMERIC, 4),
       ROUND(((m.p_draw + k.p_draw + d.p_draw) / 3)::NUMERIC, 4),
       ROUND(((m.p_away + k.p_away + d.p_away) / 3)::NUMERIC, 4)
FROM v_main_predictions m
JOIN external_predictions k ON k.source = 'market'      AND k.run_label = m.run_label AND k.match_id = m.match_id
JOIN external_predictions d ON d.source = 'dixon_coles' AND d.run_label = m.run_label AND d.match_id = m.match_id
ON CONFLICT (source, run_label, match_id)
DO UPDATE SET p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away;

-- 10.4  The full comparison, bookmakers included, on exactly the same matches
SELECT model, SUM(matches) AS matches,
       ROUND((SUM(model_rps * matches) / SUM(matches))::NUMERIC, 4)     AS model_rps,
       ROUND((SUM(bookmaker_rps * matches) / SUM(matches))::NUMERIC, 4) AS bookmaker_rps
FROM v_vs_bookmaker
WHERE season = '2025-26' AND run_label LIKE '%after GW%'
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'market')
  AND model IN ('xg_50', 'market', 'ens_mkt_50', 'ens_3way', 'dixon_coles')
GROUP BY model ORDER BY model_rps;
-- my rebuild (2025-26 GW5-34): market 0.2117 | xg_50 0.2118 | ens_mkt_50 0.2110 ; bookmakers about 0.2061

-- 10.5  Both seasons side by side (adopt only if it beats ens_dc_25, your current best, in BOTH seasons)
SELECT model,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2024-25') / SUM(matches) FILTER (WHERE season = '2024-25'))::NUMERIC, 4) AS rps_2024_25,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2025-26') / SUM(matches) FILTER (WHERE season = '2025-26'))::NUMERIC, 4) AS rps_2025_26,
       ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS rps_both,
       SUM(matches) AS matches
FROM v_scores
WHERE season IN ('2024-25', '2025-26') AND run_label LIKE '%after GW%'
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'market')
  AND run_label IN (SELECT DISTINCT run_label FROM external_predictions WHERE source = 'dixon_coles')
  AND model IN ('xg_50', 'ens_dc_25', 'market', 'ens_mkt_50', 'ens_3way')
GROUP BY model ORDER BY rps_both;

