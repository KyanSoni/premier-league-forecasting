-- =====================================================================
-- 33  Upgrades 25-27: checks to run in pgAdmin
-- ---------------------------------------------------------------------
-- Nothing here changes data. prop_prices / v_prop_log are made by prop_log.py; the other
-- upgrades only read existing tables.
-- =====================================================================

-- 25.4  The prop log, newest first: model chance, the bookmaker's (1 / odds), edge and result
SELECT logged_on, fixture, player, side, market, line, outcome, odds, implied_pct, model_pct,
       edge_pct, flag, stake, result, won, void, profit
FROM v_prop_log
ORDER BY logged_on DESC, fixture, player;

-- 25.5  Running totals by market: prices, settled, how often the model's flags won, flat-stake return
SELECT market, COUNT(*) AS prices,
       COUNT(*) FILTER (WHERE won IS NOT NULL AND NOT void) AS settled,
       COUNT(*) FILTER (WHERE flag = 'FLAG') AS flagged,
       ROUND(100.0 * AVG(won::INT) FILTER (WHERE flag = 'FLAG' AND NOT void), 1) AS flagged_won_pct,
       ROUND(AVG(implied_pct) FILTER (WHERE flag = 'FLAG'), 1) AS flagged_needed_pct,
       ROUND(100.0 * AVG(CASE WHEN won THEN odds - 1 ELSE -1 END)
             FILTER (WHERE flag = 'FLAG' AND won IS NOT NULL AND NOT void), 1) AS flagged_return_pct
FROM v_prop_log
GROUP BY market ORDER BY prices DESC;

-- 25.6  Prices logged without a model chance (logged too late, or a line the model doesn't price)
SELECT logged_on, fixture, player, side, market, line FROM v_prop_log WHERE model_pct IS NULL;

-- 26.1  Before xi_test.py: the backtest rows it needs (expect every gameweek of 2024-25 and 2025-26)
SELECT season, COUNT(DISTINCT run_label) AS gameweeks, COUNT(DISTINCT match_id) AS matches,
       ROUND(AVG(p_start)::NUMERIC, 3) AS avg_start_chance
FROM player_rates WHERE mode = 'backtest' GROUP BY season ORDER BY season;

-- 26.2  Line-ups in the player data (expect about 22 starters per match: 11 per team)
SELECT season, COUNT(DISTINCT fci_match_id) AS matches,
       ROUND(1.0 * SUM(started::INT) / COUNT(DISTINCT fci_match_id), 1) AS starters_per_match
FROM pl_player_matches GROUP BY season ORDER BY season;

-- 27.1  Before season_tails.py: forecasts it can judge (seasons with a final table)
SELECT p.season, COUNT(DISTINCT p.run_label) AS runs, COUNT(DISTINCT p.team_name) AS teams
FROM v_position_probs p
WHERE p.season IN (SELECT s.label FROM final_tables f JOIN seasons s USING (season_id))
GROUP BY p.season ORDER BY p.season;

-- 27.2  The leak this check was built for: two runs of a season with identical ratings
--       (expect no rows)
WITH sig AS (
  SELECT r.season, r.run_label, r.gameweek,
         STRING_AGG(ROUND(tr.att, 4)::TEXT || '/' || ROUND(tr.def, 4)::TEXT, ',' ORDER BY tr.team_id) AS ratings
  FROM team_ratings tr JOIN v_runs r USING (run_id)
  GROUP BY r.season, r.run_label, r.gameweek)
SELECT a.run_label, b.run_label AS same_as_later_run
FROM sig a JOIN sig b ON a.season = b.season AND a.ratings = b.ratings AND a.gameweek < b.gameweek;
