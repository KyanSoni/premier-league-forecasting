-- =====================================================================
-- UPGRADE 8: score the season forecasts (title, top 4, relegation, final position)
-- Run each block on its own.
-- =====================================================================

-- 8.1  Tables for the Python season simulations of Upgrade 11 (created now so the views below work)
CREATE TABLE IF NOT EXISTS dyn_sim_summary (
  source TEXT, run_label TEXT, team_name TEXT,
  avg_finish NUMERIC, title_pct NUMERIC, top4_pct NUMERIC, relegation_pct NUMERIC,
  PRIMARY KEY (source, run_label, team_name)
);
CREATE TABLE IF NOT EXISTS dyn_position_probs (
  source TEXT, run_label TEXT, team_name TEXT, final_position INT, pct NUMERIC,
  PRIMARY KEY (source, run_label, team_name, final_position)
);

-- 8.2  Title / top-4 / relegation Brier scores and places out, per source and run
--      (lower = better; only seasons with a final table)
CREATE OR REPLACE VIEW v_season_scores AS
WITH ev AS (
  SELECT 'excel'::TEXT AS source, s.run_label, s.season, s.gameweek, s.team_name,
         s.avg_finish, s.title_pct, s.top4_pct, s.relegation_pct
  FROM v_sim_summary s
  UNION ALL
  SELECT 'dixon_coles', d.run_label, r.season, r.gameweek, d.team_name,
         d.avg_finish, d.title_pct, d.top4_pct, d.relegation_pct
  FROM dc_sim_summary d JOIN v_runs r USING (run_label)
  UNION ALL
  SELECT y.source, y.run_label, r.season, r.gameweek, y.team_name,
         y.avg_finish, y.title_pct, y.top4_pct, y.relegation_pct
  FROM dyn_sim_summary y JOIN v_runs r USING (run_label)
),
act AS (
  SELECT s.label AS season, t.team_name, f.position
  FROM final_tables f JOIN seasons s USING (season_id) JOIN teams t USING (team_id)
)
SELECT ev.source, ev.season, ev.run_label, ev.gameweek, COUNT(*) AS teams,
       ROUND(AVG(POWER(ev.title_pct::NUMERIC / 100 - (a.position = 1)::INT, 2)), 4)       AS title_brier,
       ROUND(AVG(POWER(ev.top4_pct::NUMERIC / 100 - (a.position <= 4)::INT, 2)), 4)       AS top4_brier,
       ROUND(AVG(POWER(ev.relegation_pct::NUMERIC / 100 - (a.position >= 18)::INT, 2)), 4) AS relegation_brier,
       ROUND(AVG(ABS(ev.avg_finish::NUMERIC - a.position)), 2)                             AS avg_places_out
FROM ev JOIN act a ON a.season = ev.season AND a.team_name = ev.team_name
GROUP BY ev.source, ev.season, ev.run_label, ev.gameweek;

-- 8.3  Ranked probability score over all 20 finishing positions (lower = better)
CREATE OR REPLACE VIEW v_position_rps AS
WITH probs AS (
  SELECT 'excel'::TEXT AS source, run_label, season, gameweek, team_name, final_position, pct
  FROM v_position_probs
  UNION ALL
  SELECT p.source, p.run_label, r.season, r.gameweek, p.team_name, p.final_position, p.pct
  FROM dyn_position_probs p JOIN v_runs r USING (run_label)
),
teams_runs AS (SELECT DISTINCT source, run_label, season, gameweek, team_name FROM probs),
cum AS (
  SELECT tr.source, tr.run_label, tr.season, tr.gameweek, tr.team_name, k.k,
         COALESCE(SUM(p.pct) FILTER (WHERE p.final_position <= k.k), 0) / 100.0 AS f
  FROM teams_runs tr
  CROSS JOIN generate_series(1, 19) AS k(k)
  LEFT JOIN probs p ON p.source = tr.source AND p.run_label = tr.run_label AND p.team_name = tr.team_name
  GROUP BY tr.source, tr.run_label, tr.season, tr.gameweek, tr.team_name, k.k
),
act AS (
  SELECT s.label AS season, t.team_name, f.position
  FROM final_tables f JOIN seasons s USING (season_id) JOIN teams t USING (team_id)
),
team_rps AS (
  SELECT c.source, c.season, c.run_label, c.gameweek, c.team_name,
         SUM(POWER(c.f - (a.position <= c.k)::INT, 2)) / 19 AS rps
  FROM cum c JOIN act a ON a.season = c.season AND a.team_name = c.team_name
  GROUP BY c.source, c.season, c.run_label, c.gameweek, c.team_name
)
SELECT source, season, run_label, gameweek, COUNT(*) AS teams, ROUND(AVG(rps)::NUMERIC, 4) AS position_rps
FROM team_rps
GROUP BY source, season, run_label, gameweek;

-- 8.4  Your Excel model's season forecasts, run by run (2025-26)
SELECT s.run_label, s.title_brier, s.top4_brier, s.relegation_brier, s.avg_places_out, p.position_rps
FROM v_season_scores s
LEFT JOIN v_position_rps p USING (source, run_label)
WHERE s.source = 'excel' AND s.season = '2025-26'
ORDER BY s.gameweek;

-- 8.5  Excel model vs Dixon-Coles on the same runs (title / top 4 / relegation)
SELECT source, COUNT(*) AS runs,
       ROUND(AVG(title_brier), 4) AS title_brier, ROUND(AVG(top4_brier), 4) AS top4_brier,
       ROUND(AVG(relegation_brier), 4) AS relegation_brier, ROUND(AVG(avg_places_out), 2) AS places_out
FROM v_season_scores
WHERE season = '2025-26'
  AND run_label IN (SELECT run_label FROM v_season_scores WHERE source = 'dixon_coles')
GROUP BY source ORDER BY title_brier;

-- 8.6  Calibration of the big calls: when you said 20-40% for top 4, how often did it happen?
WITH ev AS (
  SELECT s.run_label, s.team_name, s.top4_pct, (f.position <= 4)::INT AS hit
  FROM v_sim_summary s
  JOIN seasons se     ON se.label = s.season
  JOIN teams t        ON t.team_name = s.team_name
  JOIN final_tables f ON f.season_id = se.season_id AND f.team_id = t.team_id
)
SELECT FLOOR(top4_pct::NUMERIC / 20) * 20 AS band_from_pct, COUNT(*) AS forecasts,
       ROUND(AVG(top4_pct)::NUMERIC, 1) AS avg_forecast_pct, ROUND(100.0 * AVG(hit), 1) AS actual_pct
FROM ev GROUP BY 1 ORDER BY 1;
