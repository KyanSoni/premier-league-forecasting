-- =====================================================================================
-- UPGRADE 15.9 and 15.10: one test summary for every market, and the views the dashboard reads
-- Run each block in pgAdmin (Query Tool on prem_sims), top to bottom.
-- =====================================================================================


-- =====================================================================================
-- 15.9  TESTING SUMMARY
-- =====================================================================================

-- 15.9a  Every market, line and season in one table: actual vs predicted chance, Brier for the
--        model and its baseline, and whether the model beat the baseline.
CREATE OR REPLACE VIEW v_market_tests AS
WITH p AS (
  SELECT 'player' AS grp, market, line::TEXT AS line, season, COUNT(*) AS predictions,
         AVG((actual >= line)::INT) AS actual, AVG(prob) AS predicted,
         AVG(POWER(prob - (actual >= line)::INT, 2)) AS brier,
         AVG(POWER(baseline_prob - (actual >= line)::INT, 2)) AS baseline_brier
  FROM player_markets
  WHERE mode = 'backtest' AND act_minutes > 0 AND actual IS NOT NULL AND prob IS NOT NULL
    AND season IN ('2024-25', '2025-26')
  GROUP BY market, line, season
),
m AS (
  SELECT 'team / match' AS grp, market || ' (' || side || ')' AS market,
         COALESCE(line::TEXT, 'all') AS line, season, COUNT(*) AS predictions,
         AVG(hit) AS actual, AVG(prob) AS predicted,
         AVG(POWER(prob - hit, 2)) AS brier, AVG(POWER(base_prob - hit, 2)) AS baseline_brier
  FROM match_markets
  WHERE mode = 'backtest' AND hit IS NOT NULL AND season IN ('2024-25', '2025-26')
  GROUP BY market, side, COALESCE(line::TEXT, 'all'), season
),
s AS (
  SELECT 'bet builder' AS grp, combo AS market, 'slip' AS line, season, COUNT(*) AS predictions,
         AVG(hit) AS actual, AVG("joint") AS predicted,
         AVG(POWER("joint" - hit, 2)) AS brier, AVG(POWER(product - hit, 2)) AS baseline_brier
  FROM slip_test GROUP BY combo, season
),
a AS (SELECT * FROM p UNION ALL SELECT * FROM m UNION ALL SELECT * FROM s)
SELECT grp, market, line, season, predictions,
       ROUND(100 * actual::NUMERIC, 1) AS actual_pct, ROUND(100 * predicted::NUMERIC, 1) AS predicted_pct,
       ROUND(brier::NUMERIC, 4) AS brier, ROUND(baseline_brier::NUMERIC, 4) AS baseline_brier,
       CASE WHEN ROUND(brier::NUMERIC, 4) < ROUND(baseline_brier::NUMERIC, 4) THEN 'beats baseline'
            WHEN ROUND(brier::NUMERIC, 4) = ROUND(baseline_brier::NUMERIC, 4) THEN 'tie'
            ELSE 'worse' END AS result
FROM a;

-- 15.9b  The headline: how many market-lines beat the baseline, tie or lose, per group and season
SELECT grp, season, COUNT(*) AS market_lines,
       COUNT(*) FILTER (WHERE result = 'beats baseline') AS beats_baseline,
       COUNT(*) FILTER (WHERE result = 'tie') AS ties,
       COUNT(*) FILTER (WHERE result = 'worse') AS worse,
       ROUND(AVG(brier), 4) AS avg_brier, ROUND(AVG(baseline_brier), 4) AS avg_baseline_brier
FROM v_market_tests GROUP BY grp, season ORDER BY grp, season;

-- 15.9c  Anything that tied or lost (should be only red cards, match corners and 3+ goals)
SELECT * FROM v_market_tests WHERE result <> 'beats baseline' ORDER BY grp, market, line, season;


-- =====================================================================================
-- 15.10  DASHBOARD VIEWS (the Excel sheets read these over ODBC)
-- =====================================================================================

-- 15.10a  Player markets for the next gameweek: one row per player, every market as a column
CREATE OR REPLACE VIEW v_player_markets_next AS
WITH r AS (SELECT run_label, season FROM player_markets WHERE mode = 'next'
           ORDER BY season DESC, gameweek DESC LIMIT 1),
pm AS (SELECT p.* FROM player_markets p JOIN r USING (run_label) WHERE p.mode = 'next'),
pv AS (
  SELECT run_label, match_id, team_id, player_code, MAX(position) AS position,
         MAX(p_start) AS p_start, MAX(p_play) AS p_play, MAX(minutes_if_plays) AS minutes_if_plays,
         MAX(expected) FILTER (WHERE market = 'goals' AND line = 1)   AS exp_goals,
         MAX(expected) FILTER (WHERE market = 'assists' AND line = 1) AS exp_assists,
         MAX(prob) FILTER (WHERE market = 'goals' AND line = 1)          AS score,
         MAX(prob) FILTER (WHERE market = 'goals' AND line = 2)          AS score_2plus,
         MAX(prob) FILTER (WHERE market = 'assists' AND line = 1)        AS assist,
         MAX(prob) FILTER (WHERE market = 'goal_or_assist' AND line = 1) AS goal_or_assist,
         MAX(prob) FILTER (WHERE market = 'shots' AND line = 1) AS shots_1plus,
         MAX(prob) FILTER (WHERE market = 'shots' AND line = 2) AS shots_2plus,
         MAX(prob) FILTER (WHERE market = 'shots' AND line = 3) AS shots_3plus,
         MAX(prob) FILTER (WHERE market = 'sot' AND line = 1) AS sot_1plus,
         MAX(prob) FILTER (WHERE market = 'sot' AND line = 2) AS sot_2plus,
         MAX(prob) FILTER (WHERE market = 'sot' AND line = 3) AS sot_3plus,
         MAX(prob) FILTER (WHERE market = 'fouls' AND line = 1) AS fouls_1plus,
         MAX(prob) FILTER (WHERE market = 'fouls' AND line = 2) AS fouls_2plus,
         MAX(prob) FILTER (WHERE market = 'fouls' AND line = 3) AS fouls_3plus,
         MAX(prob) FILTER (WHERE market = 'fouled' AND line = 1) AS fouled_1plus,
         MAX(prob) FILTER (WHERE market = 'fouled' AND line = 2) AS fouled_2plus,
         MAX(prob) FILTER (WHERE market = 'fouled' AND line = 3) AS fouled_3plus,
         MAX(prob) FILTER (WHERE market = 'tackles' AND line = 1) AS tackles_1plus,
         MAX(prob) FILTER (WHERE market = 'tackles' AND line = 2) AS tackles_2plus,
         MAX(prob) FILTER (WHERE market = 'tackles' AND line = 3) AS tackles_3plus,
         MAX(prob) FILTER (WHERE market = 'carded') AS carded,
         MAX(prob) FILTER (WHERE market = 'red')    AS red_card,
         MAX(prob) FILTER (WHERE market = 'saves' AND line = 2) AS saves_2plus,
         MAX(prob) FILTER (WHERE market = 'saves' AND line = 3) AS saves_3plus,
         MAX(prob) FILTER (WHERE market = 'saves' AND line = 4) AS saves_4plus
  FROM pm GROUP BY run_label, match_id, team_id, player_code
)
SELECT pv.run_label, ht.team_name || ' v ' || at.team_name AS fixture,
       t.team_name AS team, o.team_name AS opponent,
       CASE WHEN m.home_team_id = pv.team_id THEN 'home' ELSE 'away' END AS venue,
       COALESCE(pl.web_name, pv.player_code::TEXT) AS player, pv.position,
       ROUND(100 * pv.p_start::NUMERIC) AS start_pct, ROUND(pv.minutes_if_plays::NUMERIC) AS minutes_if_plays,
       ROUND(pv.exp_goals::NUMERIC, 2) AS xg, ROUND(pv.exp_assists::NUMERIC, 2) AS xa,
       ROUND(100 * pv.score::NUMERIC) AS score_pct, ROUND(100 * pv.score_2plus::NUMERIC) AS score_2plus_pct,
       ROUND(100 * pv.assist::NUMERIC) AS assist_pct, ROUND(100 * pv.goal_or_assist::NUMERIC) AS goal_or_assist_pct,
       ROUND(100 * pv.shots_1plus::NUMERIC) AS shots_1plus_pct, ROUND(100 * pv.shots_2plus::NUMERIC) AS shots_2plus_pct,
       ROUND(100 * pv.shots_3plus::NUMERIC) AS shots_3plus_pct,
       ROUND(100 * pv.sot_1plus::NUMERIC) AS sot_1plus_pct, ROUND(100 * pv.sot_2plus::NUMERIC) AS sot_2plus_pct,
       ROUND(100 * pv.sot_3plus::NUMERIC) AS sot_3plus_pct,
       ROUND(100 * pv.fouls_1plus::NUMERIC) AS fouls_1plus_pct, ROUND(100 * pv.fouls_2plus::NUMERIC) AS fouls_2plus_pct,
       ROUND(100 * pv.fouls_3plus::NUMERIC) AS fouls_3plus_pct,
       ROUND(100 * pv.fouled_1plus::NUMERIC) AS fouled_1plus_pct, ROUND(100 * pv.fouled_2plus::NUMERIC) AS fouled_2plus_pct,
       ROUND(100 * pv.fouled_3plus::NUMERIC) AS fouled_3plus_pct,
       ROUND(100 * pv.tackles_1plus::NUMERIC) AS tackles_1plus_pct, ROUND(100 * pv.tackles_2plus::NUMERIC) AS tackles_2plus_pct,
       ROUND(100 * pv.tackles_3plus::NUMERIC) AS tackles_3plus_pct,
       ROUND(100 * pv.carded::NUMERIC) AS carded_pct, ROUND(100 * pv.red_card::NUMERIC, 1) AS red_card_pct,
       ROUND(100 * pv.saves_2plus::NUMERIC) AS saves_2plus_pct, ROUND(100 * pv.saves_3plus::NUMERIC) AS saves_3plus_pct,
       ROUND(100 * pv.saves_4plus::NUMERIC) AS saves_4plus_pct
FROM pv
JOIN r ON r.run_label = pv.run_label
JOIN matches m ON m.match_id = pv.match_id
JOIN teams ht ON ht.team_id = m.home_team_id
JOIN teams at ON at.team_id = m.away_team_id
JOIN teams t  ON t.team_id = pv.team_id
JOIN teams o  ON o.team_id = CASE WHEN m.home_team_id = pv.team_id THEN m.away_team_id ELSE m.home_team_id END
LEFT JOIN pl_players pl ON pl.season = r.season AND pl.player_code = pv.player_code;

SELECT fixture, player, start_pct, score_pct, sot_1plus_pct, carded_pct
FROM v_player_markets_next ORDER BY score_pct DESC LIMIT 20;

-- 15.10b  Match markets for the next gameweek: one row per fixture, the common lines as columns
CREATE OR REPLACE VIEW v_match_markets_next AS
WITH r AS (SELECT run_label FROM match_markets WHERE mode = 'next' ORDER BY season DESC, gameweek DESC LIMIT 1),
mm AS (SELECT x.* FROM match_markets x JOIN r USING (run_label) WHERE x.mode = 'next'),
f AS (
  SELECT run_label, match_id, home_team_id, away_team_id, MAX(referee) AS referee,
         MAX(expected) FILTER (WHERE market = 'corners' AND side = 'home' AND line = 4.5)  AS exp_corners_home,
         MAX(expected) FILTER (WHERE market = 'corners' AND side = 'away' AND line = 4.5)  AS exp_corners_away,
         MAX(expected) FILTER (WHERE market = 'corners' AND side = 'match' AND line = 9.5) AS exp_corners,
         MAX(prob) FILTER (WHERE market = 'corners' AND side = 'match' AND line = 8.5)  AS corners_o85,
         MAX(prob) FILTER (WHERE market = 'corners' AND side = 'match' AND line = 9.5)  AS corners_o95,
         MAX(prob) FILTER (WHERE market = 'corners' AND side = 'match' AND line = 10.5) AS corners_o105,
         MAX(prob) FILTER (WHERE market = 'corners' AND side = 'match' AND line = 11.5) AS corners_o115,
         MAX(prob) FILTER (WHERE market = 'most_corners' AND outcome = 'home') AS most_corners_home,
         MAX(prob) FILTER (WHERE market = 'most_corners' AND outcome = 'draw') AS most_corners_draw,
         MAX(prob) FILTER (WHERE market = 'most_corners' AND outcome = 'away') AS most_corners_away,
         MAX(expected) FILTER (WHERE market = 'fouls' AND side = 'match' AND line = 20.5) AS exp_fouls,
         MAX(prob) FILTER (WHERE market = 'fouls' AND side = 'match' AND line = 20.5) AS fouls_o205,
         MAX(prob) FILTER (WHERE market = 'fouls' AND side = 'match' AND line = 22.5) AS fouls_o225,
         MAX(expected) FILTER (WHERE market = 'cards' AND side = 'home' AND line = 1.5)  AS exp_cards_home,
         MAX(expected) FILTER (WHERE market = 'cards' AND side = 'away' AND line = 1.5)  AS exp_cards_away,
         MAX(expected) FILTER (WHERE market = 'cards' AND side = 'match' AND line = 3.5) AS exp_cards,
         MAX(prob) FILTER (WHERE market = 'cards' AND side = 'match' AND line = 2.5) AS cards_o25,
         MAX(prob) FILTER (WHERE market = 'cards' AND side = 'match' AND line = 3.5) AS cards_o35,
         MAX(prob) FILTER (WHERE market = 'cards' AND side = 'match' AND line = 4.5) AS cards_o45,
         MAX(prob) FILTER (WHERE market = 'cards' AND side = 'match' AND line = 5.5) AS cards_o55,
         MAX(prob) FILTER (WHERE market = 'most_cards' AND outcome = 'home') AS most_cards_home,
         MAX(prob) FILTER (WHERE market = 'most_cards' AND outcome = 'draw') AS most_cards_draw,
         MAX(prob) FILTER (WHERE market = 'most_cards' AND outcome = 'away') AS most_cards_away,
         MAX(expected) FILTER (WHERE market = 'booking_points' AND side = 'match' AND line = 35.5) AS exp_booking_points,
         MAX(prob) FILTER (WHERE market = 'booking_points' AND side = 'match' AND line = 35.5) AS bp_o355,
         MAX(prob) FILTER (WHERE market = 'booking_points' AND side = 'match' AND line = 45.5) AS bp_o455,
         MAX(prob) FILTER (WHERE market = 'red' AND side = 'match') AS red_card
  FROM mm GROUP BY run_label, match_id, home_team_id, away_team_id
)
SELECT f.run_label, ht.team_name || ' v ' || at.team_name AS fixture, COALESCE(f.referee, 'referee TBC') AS referee,
       ROUND(f.exp_corners_home::NUMERIC, 1) AS exp_corners_home, ROUND(f.exp_corners_away::NUMERIC, 1) AS exp_corners_away,
       ROUND(f.exp_corners::NUMERIC, 1) AS exp_corners,
       ROUND(100 * f.corners_o85::NUMERIC) AS corners_over_8_5, ROUND(100 * f.corners_o95::NUMERIC) AS corners_over_9_5,
       ROUND(100 * f.corners_o105::NUMERIC) AS corners_over_10_5, ROUND(100 * f.corners_o115::NUMERIC) AS corners_over_11_5,
       ROUND(100 * f.most_corners_home::NUMERIC) AS most_corners_home, ROUND(100 * f.most_corners_draw::NUMERIC) AS most_corners_draw,
       ROUND(100 * f.most_corners_away::NUMERIC) AS most_corners_away,
       ROUND(f.exp_fouls::NUMERIC, 1) AS exp_fouls,
       ROUND(100 * f.fouls_o205::NUMERIC) AS fouls_over_20_5, ROUND(100 * f.fouls_o225::NUMERIC) AS fouls_over_22_5,
       ROUND(f.exp_cards_home::NUMERIC, 2) AS exp_cards_home, ROUND(f.exp_cards_away::NUMERIC, 2) AS exp_cards_away,
       ROUND(f.exp_cards::NUMERIC, 2) AS exp_cards,
       ROUND(100 * f.cards_o25::NUMERIC) AS cards_over_2_5, ROUND(100 * f.cards_o35::NUMERIC) AS cards_over_3_5,
       ROUND(100 * f.cards_o45::NUMERIC) AS cards_over_4_5, ROUND(100 * f.cards_o55::NUMERIC) AS cards_over_5_5,
       ROUND(100 * f.most_cards_home::NUMERIC) AS most_cards_home, ROUND(100 * f.most_cards_draw::NUMERIC) AS most_cards_draw,
       ROUND(100 * f.most_cards_away::NUMERIC) AS most_cards_away,
       ROUND(f.exp_booking_points::NUMERIC) AS exp_booking_points,
       ROUND(100 * f.bp_o355::NUMERIC) AS booking_points_over_35_5, ROUND(100 * f.bp_o455::NUMERIC) AS booking_points_over_45_5,
       ROUND(100 * f.red_card::NUMERIC) AS red_card_pct
FROM f
JOIN teams ht ON ht.team_id = f.home_team_id
JOIN teams at ON at.team_id = f.away_team_id;

SELECT fixture, referee, exp_corners, corners_over_9_5, exp_cards, cards_over_3_5, red_card_pct
FROM v_match_markets_next ORDER BY cards_over_3_5 DESC;

-- 15.10c  Player vs opponent: for every player in the latest gameweek, his chances against every
--         other team, home and away. His team's expected goals against each opponent come from
--         your model's ratings for that gameweek (attack x opponent's defence x league average x
--         home/away factor); his own chances are scaled by how that compares with his actual
--         next fixture. An approximation: the dashboard's exact numbers are for real fixtures.
CREATE OR REPLACE VIEW v_player_vs_opponent AS
WITH nx AS (SELECT run_label, season, gameweek FROM player_markets WHERE mode = 'next'
            ORDER BY season DESC, gameweek DESC LIMIT 1),
run AS (SELECT r.run_id, r.run_label, r.season, r.gameweek FROM v_runs r JOIN nx USING (run_label)),
var AS (SELECT v.* FROM model_variants v, run
        WHERE v.variant = CASE WHEN run.season >= '2026-27' AND run.gameweek >= 6 THEN 'home_adv' ELSE 'value_prom_50' END),
rt AS (SELECT vr.team_id, vr.att, vr.def FROM v_variant_ratings vr, run, var
       WHERE vr.run_id = run.run_id AND vr.variant = var.variant),
avg_g AS (SELECT ra.avg_goals FROM v_run_avg ra, run WHERE ra.run_id = run.run_id),
base AS (   -- each player's next fixture: his team's expected goals there and his own numbers
  SELECT p.player_code, p.team_id, p.match_id, p.market, p.line, p.expected, p.position, p.p_start,
         CASE WHEN m.home_team_id = p.team_id THEN l.lambda_home ELSE l.lambda_away END AS team_xg_next
  FROM player_markets p
  JOIN nx ON nx.run_label = p.run_label
  JOIN matches m ON m.match_id = p.match_id
  JOIN match_lambdas l ON l.run_label = p.run_label AND l.match_id = p.match_id
  WHERE p.mode = 'next' AND ((p.market IN ('goals', 'assists', 'goal_or_assist', 'shots', 'sot') AND p.line = 1))
),
opp AS (    -- his team's expected goals against every other team, at home and away
  SELECT b.player_code, o.team_id AS opp_id, v.venue,
         t.att * o.def * a.avg_goals * CASE WHEN v.venue = 'home' THEN var.home_factor ELSE var.away_factor END AS team_xg
  FROM (SELECT DISTINCT player_code, team_id FROM base) b
  JOIN rt t ON t.team_id = b.team_id
  JOIN rt o ON o.team_id <> b.team_id
  CROSS JOIN (VALUES ('home'), ('away')) AS v(venue)
  CROSS JOIN avg_g a CROSS JOIN var
)
SELECT COALESCE(pl.web_name, b.player_code::TEXT) AS player, tm.team_name AS team, b.position,
       ROUND(100 * b.p_start::NUMERIC) AS start_pct, ot.team_name AS opponent, opp.venue,
       ROUND(opp.team_xg::NUMERIC, 2) AS team_xg,
       ROUND(100 * MAX(1 - EXP(-b.expected * opp.team_xg / b.team_xg_next)) FILTER (WHERE b.market = 'goals')::NUMERIC) AS score_pct,
       ROUND(100 * MAX(1 - EXP(-b.expected * opp.team_xg / b.team_xg_next)) FILTER (WHERE b.market = 'assists')::NUMERIC) AS assist_pct,
       ROUND(100 * MAX(1 - EXP(-b.expected * opp.team_xg / b.team_xg_next)) FILTER (WHERE b.market = 'goal_or_assist')::NUMERIC) AS goal_or_assist_pct,
       ROUND(100 * MAX(1 - EXP(-b.expected * SQRT(opp.team_xg / b.team_xg_next))) FILTER (WHERE b.market = 'shots')::NUMERIC) AS shots_1plus_pct,
       ROUND(100 * MAX(1 - EXP(-b.expected * SQRT(opp.team_xg / b.team_xg_next))) FILTER (WHERE b.market = 'sot')::NUMERIC) AS sot_1plus_pct,
       ROUND(MAX(b.expected * opp.team_xg / b.team_xg_next) FILTER (WHERE b.market = 'goals')::NUMERIC, 2) AS exp_goals_if_plays
FROM base b
JOIN opp ON opp.player_code = b.player_code
JOIN teams tm ON tm.team_id = b.team_id
JOIN teams ot ON ot.team_id = opp.opp_id
JOIN nx ON TRUE
LEFT JOIN pl_players pl ON pl.season = nx.season AND pl.player_code = b.player_code
WHERE b.team_xg_next > 0
GROUP BY pl.web_name, b.player_code, tm.team_name, b.position, b.p_start, ot.team_name, opp.venue, opp.team_xg;

SELECT * FROM v_player_vs_opponent WHERE player = 'Haaland' ORDER BY score_pct DESC;

-- 15.10d  Calibration for the dashboard chart: predicted vs actual by chance band, for the main
--         markets (both test seasons together)
CREATE OR REPLACE VIEW v_market_calibration AS
SELECT 'to score' AS market, LEAST(FLOOR(prob * 10), 9) * 10 AS band_from,
       COUNT(*) AS predictions, ROUND(100 * AVG(prob)::NUMERIC, 1) AS predicted_pct,
       ROUND(100 * AVG((actual >= 1)::INT)::NUMERIC, 1) AS actual_pct
FROM player_markets WHERE mode = 'backtest' AND market = 'goals' AND line = 1 AND act_minutes > 0 AND prob IS NOT NULL
GROUP BY 2
UNION ALL
SELECT 'to be carded', LEAST(FLOOR(prob * 10), 9) * 10, COUNT(*), ROUND(100 * AVG(prob)::NUMERIC, 1),
       ROUND(100 * AVG((actual >= 1)::INT)::NUMERIC, 1)
FROM player_markets WHERE mode = 'backtest' AND market = 'carded' AND act_minutes > 0 AND prob IS NOT NULL
GROUP BY 2
UNION ALL
SELECT '1+ shot on target', LEAST(FLOOR(prob * 10), 9) * 10, COUNT(*), ROUND(100 * AVG(prob)::NUMERIC, 1),
       ROUND(100 * AVG((actual >= 1)::INT)::NUMERIC, 1)
FROM player_markets WHERE mode = 'backtest' AND market = 'sot' AND line = 1 AND act_minutes > 0 AND prob IS NOT NULL
GROUP BY 2
UNION ALL
SELECT 'match cards over 3.5', LEAST(FLOOR(prob * 10), 9) * 10, COUNT(*), ROUND(100 * AVG(prob)::NUMERIC, 1),
       ROUND(100 * AVG(hit)::NUMERIC, 1)
FROM match_markets WHERE mode = 'backtest' AND market = 'cards' AND side = 'match' AND line = 3.5 AND hit IS NOT NULL
GROUP BY 2
UNION ALL
SELECT 'match corners over 9.5', LEAST(FLOOR(prob * 10), 9) * 10, COUNT(*), ROUND(100 * AVG(prob)::NUMERIC, 1),
       ROUND(100 * AVG(hit)::NUMERIC, 1)
FROM match_markets WHERE mode = 'backtest' AND market = 'corners' AND side = 'match' AND line = 9.5 AND hit IS NOT NULL
GROUP BY 2;

SELECT * FROM v_market_calibration ORDER BY market, band_from;

-- 15.10e  The list of players for the selector's drop-down (players in the latest gameweek)
CREATE OR REPLACE VIEW v_player_list AS
SELECT DISTINCT player, team FROM v_player_vs_opponent ORDER BY team, player;


-- 15.10f  Player card (dashboard 'Player' sheet): one row per player per market and line for the
--         next gameweek, with his fixture, referee, start chance and both teams' expected goals.
--         Chances are "if he plays" (bookmakers void a player bet if he doesn't play).
CREATE OR REPLACE VIEW v_player_card AS
WITH r AS (
  SELECT run_label, season FROM player_markets WHERE mode = 'next'
  ORDER BY season DESC, gameweek DESC LIMIT 1
),
pm AS (SELECT p.* FROM player_markets p JOIN r USING (run_label) WHERE p.mode = 'next'),
info AS (
  SELECT match_id, team_id, player_code, MAX(position) AS position, MAX(p_start) AS p_start,
         MAX(p_play) AS p_play, MAX(minutes_if_plays) AS minutes_if_plays
  FROM pm GROUP BY match_id, team_id, player_code
),
ref AS (
  SELECT x.match_id, MAX(x.referee) AS referee
  FROM match_markets x JOIN r USING (run_label)
  WHERE x.mode = 'next' GROUP BY x.match_id
)
SELECT COALESCE(pl.web_name, pm.player_code::TEXT) || ' (' || t.team_name || ')' AS player_key,
       COALESCE(pl.web_name, pm.player_code::TEXT) AS player, t.team_name AS team, i.position,
       o.team_name AS opponent,
       CASE WHEN m.home_team_id = pm.team_id THEN 'home' ELSE 'away' END AS venue,
       ht.team_name || ' v ' || at.team_name AS fixture,
       COALESCE(rf.referee, 'TBC') AS referee,
       ROUND((CASE WHEN m.home_team_id = pm.team_id THEN l.lambda_home ELSE l.lambda_away END)::NUMERIC, 2) AS team_xg,
       ROUND((CASE WHEN m.home_team_id = pm.team_id THEN l.lambda_away ELSE l.lambda_home END)::NUMERIC, 2) AS opp_xg,
       ROUND(100 * i.p_start::NUMERIC) AS start_pct,
       ROUND(100 * i.p_play::NUMERIC) AS play_pct,
       ROUND(i.minutes_if_plays::NUMERIC) AS minutes_if_plays,
       CASE WHEN pm.market IN ('goals', 'assists', 'goal_or_assist', 'shots', 'sot') THEN 'Attacking'
            WHEN pm.market IN ('fouls', 'fouled', 'tackles') THEN 'Defending and duels'
            WHEN pm.market IN ('carded', 'red') THEN 'Discipline'
            ELSE 'Goalkeeping' END AS market_group,
       CASE pm.market WHEN 'goals' THEN 'To score' WHEN 'assists' THEN 'To assist'
            WHEN 'goal_or_assist' THEN 'Goal or assist' WHEN 'shots' THEN 'Shots'
            WHEN 'sot' THEN 'Shots on target' WHEN 'fouls' THEN 'Fouls committed'
            WHEN 'fouled' THEN 'Times fouled' WHEN 'tackles' THEN 'Tackles'
            WHEN 'carded' THEN 'To be carded' WHEN 'red' THEN 'Red card'
            WHEN 'saves' THEN 'Saves' ELSE pm.market END AS market_name,
       CASE WHEN pm.market IN ('carded', 'red', 'goal_or_assist') OR pm.line IS NULL THEN 'Yes'
            ELSE ROUND(pm.line::NUMERIC)::INT || '+' END AS line_label,
       ROUND(pm.expected::NUMERIC, 2) AS expected_if_plays,
       ROUND(100 * pm.prob::NUMERIC, 1) AS chance_pct,
       (CASE pm.market WHEN 'goals' THEN 1 WHEN 'assists' THEN 2 WHEN 'goal_or_assist' THEN 3
             WHEN 'shots' THEN 4 WHEN 'sot' THEN 5 WHEN 'fouls' THEN 6 WHEN 'fouled' THEN 7
             WHEN 'tackles' THEN 8 WHEN 'carded' THEN 9 WHEN 'red' THEN 10 ELSE 11 END) * 10
         + COALESCE(ROUND(pm.line::NUMERIC)::INT, 0) AS sort_order
FROM pm
CROSS JOIN r
JOIN info i ON i.match_id = pm.match_id AND i.team_id = pm.team_id AND i.player_code = pm.player_code
JOIN matches m ON m.match_id = pm.match_id
JOIN teams ht ON ht.team_id = m.home_team_id
JOIN teams at ON at.team_id = m.away_team_id
JOIN teams t  ON t.team_id = pm.team_id
JOIN teams o  ON o.team_id = CASE WHEN m.home_team_id = pm.team_id THEN m.away_team_id ELSE m.home_team_id END
LEFT JOIN match_lambdas l ON l.run_label = pm.run_label AND l.match_id = pm.match_id
LEFT JOIN ref rf ON rf.match_id = pm.match_id
LEFT JOIN pl_players pl ON pl.season = r.season AND pl.player_code = pm.player_code;

-- Check: one player's card (change the name). Expect about 22 rows for an outfield player (keepers also get saves).
SELECT market_group, market_name, line_label, chance_pct, expected_if_plays, opponent, venue, start_pct
FROM v_player_card WHERE player = 'Haaland' ORDER BY sort_order;


-- 15.10g  Fixture card (Player sheet, "His team's match" section): every team and match market for
--         each team's next fixture, seen from that team's side (Team / Opponent / Match).
--         Result and goals come from the main model's expected goals (match_lambdas, Poisson, the
--         same way the main model is scored), plus the stored model + market blend (Upgrade 10).
--         Corners, fouls, cards, booking points, red cards and most corners / cards come from
--         match_markets (15.7). Season chances from the latest run's simulations (Upgrades 8 and 11).
--         Note: the goals lines (team / match goals, BTTS, clean sheet) are new here and not yet
--         backtested on their own; the result chances are the tested main model.
CREATE OR REPLACE VIEW v_fixture_card AS
WITH r AS (
  SELECT run_label, season FROM match_markets WHERE mode = 'next'
  ORDER BY season DESC, gameweek DESC LIMIT 1
),
mm AS (SELECT x.* FROM match_markets x JOIN r USING (run_label) WHERE x.mode = 'next'),
sides AS (
  SELECT DISTINCT match_id, home_team_id AS team_id, away_team_id AS opp_id, 'home' AS venue FROM mm
  UNION
  SELECT DISTINCT match_id, away_team_id, home_team_id, 'away' FROM mm
),
lam AS (
  SELECT s.match_id, s.team_id, s.venue,
         (CASE WHEN s.venue = 'home' THEN l.lambda_home ELSE l.lambda_away END)::FLOAT8 AS lt,
         (CASE WHEN s.venue = 'home' THEN l.lambda_away ELSE l.lambda_home END)::FLOAT8 AS lo
  FROM sides s CROSS JOIN r
  JOIN match_lambdas l ON l.run_label = r.run_label AND l.match_id = s.match_id
),
res AS (
  SELECT l.match_id, l.team_id,
         SUM(pp.p) FILTER (WHERE g1 > g2) / SUM(pp.p) AS win,
         SUM(pp.p) FILTER (WHERE g1 = g2) / SUM(pp.p) AS draw,
         SUM(pp.p) FILTER (WHERE g1 < g2) / SUM(pp.p) AS lose
  FROM lam l
  CROSS JOIN generate_series(0, 10) AS g1
  CROSS JOIN generate_series(0, 10) AS g2
  CROSS JOIN LATERAL (SELECT EXP(-l.lt) * POWER(l.lt, g1) / FACTORIAL(g1)::FLOAT8
                           * EXP(-l.lo) * POWER(l.lo, g2) / FACTORIAL(g2)::FLOAT8 AS p) pp
  GROUP BY l.match_id, l.team_id
),
q AS (
  -- match result, main model
  SELECT x.match_id, x.team_id, 'Result' AS market_group, 'Match result (main model)' AS market_name,
         v.line_label, v.p::FLOAT8 AS p, NULL::FLOAT8 AS expected, (100 + v.k)::NUMERIC AS sort_order
  FROM res x
  CROSS JOIN LATERAL (VALUES ('Win', x.win, 1), ('Draw', x.draw, 2), ('Lose', x.lose, 3)) v(line_label, p, k)
  UNION ALL
  -- match result, model + market blend (Upgrade 10)
  SELECT s.match_id, s.team_id, 'Result', 'Match result (model + market blend)', v.line_label, v.p::FLOAT8,
         NULL::FLOAT8, (110 + v.k)::NUMERIC
  FROM sides s CROSS JOIN r
  JOIN external_predictions e ON e.source = 'ens_mkt_50' AND e.run_label = r.run_label AND e.match_id = s.match_id
  CROSS JOIN LATERAL (VALUES
     ('Win',  CASE WHEN s.venue = 'home' THEN e.p_home ELSE e.p_away END, 1),
     ('Draw', e.p_draw, 2),
     ('Lose', CASE WHEN s.venue = 'home' THEN e.p_away ELSE e.p_home END, 3)) v(line_label, p, k)
  UNION ALL
  -- goals from expected goals
  SELECT l.match_id, l.team_id, 'Goals', v.market_name, v.line_label, v.p, v.expected, (200 + v.k)::NUMERIC
  FROM lam l
  CROSS JOIN LATERAL (SELECT l.lt + l.lo AS t) tt
  CROSS JOIN LATERAL (VALUES
     ('Team goals', '1+', 1 - EXP(-l.lt), l.lt, 1),
     ('Team goals', '2+', 1 - EXP(-l.lt) * (1 + l.lt), l.lt, 2),
     ('Team goals', '3+', 1 - EXP(-l.lt) * (1 + l.lt + l.lt ^ 2 / 2), l.lt, 3),
     ('Opponent goals', '1+', 1 - EXP(-l.lo), l.lo, 4),
     ('Opponent goals', '2+', 1 - EXP(-l.lo) * (1 + l.lo), l.lo, 5),
     ('Clean sheet', 'Yes', EXP(-l.lo), NULL::FLOAT8, 6),
     ('Both teams to score', 'Yes', (1 - EXP(-l.lt)) * (1 - EXP(-l.lo)), NULL::FLOAT8, 7),
     ('Match goals', 'Over 1.5', 1 - EXP(-tt.t) * (1 + tt.t), tt.t, 8),
     ('Match goals', 'Over 2.5', 1 - EXP(-tt.t) * (1 + tt.t + tt.t ^ 2 / 2), tt.t, 9),
     ('Match goals', 'Over 3.5', 1 - EXP(-tt.t) * (1 + tt.t + tt.t ^ 2 / 2 + tt.t ^ 3 / 6), tt.t, 10),
     ('Match goals', 'Over 4.5', 1 - EXP(-tt.t) * (1 + tt.t + tt.t ^ 2 / 2 + tt.t ^ 3 / 6 + tt.t ^ 4 / 24), tt.t, 11)
  ) v(market_name, line_label, p, expected, k)
  UNION ALL
  -- corners, fouls, cards, booking points, red card, most corners / most cards (15.7)
  SELECT s.match_id, s.team_id,
         CASE WHEN x.market IN ('corners', 'most_corners') THEN 'Corners'
              WHEN x.market = 'fouls' THEN 'Fouls' ELSE 'Cards' END,
         CASE WHEN x.market = 'most_corners' THEN 'Most corners'
              WHEN x.market = 'most_cards' THEN 'Most cards'
              ELSE (CASE WHEN x.side = 'match' THEN 'Match' WHEN x.side = s.venue THEN 'Team' ELSE 'Opponent' END)
                   || ' ' || CASE x.market WHEN 'booking_points' THEN 'booking points'
                                           WHEN 'red' THEN 'red card' ELSE x.market END END,
         CASE WHEN x.market LIKE 'most_%' THEN
                   CASE WHEN x.outcome = 'draw' THEN 'Level' WHEN x.outcome = s.venue THEN 'Team' ELSE 'Opponent' END
              WHEN x.market = 'red' THEN 'Yes'
              ELSE 'Over ' || TO_CHAR(x.line::NUMERIC, 'FM990.0') END,
         x.prob::FLOAT8,
         CASE WHEN x.market LIKE 'most_%' THEN NULL ELSE x.expected::FLOAT8 END,
         ((CASE x.market WHEN 'corners' THEN 300 WHEN 'most_corners' THEN 390 WHEN 'fouls' THEN 400
                         WHEN 'cards' THEN 500 WHEN 'booking_points' THEN 600 WHEN 'red' THEN 700 ELSE 790 END)
          + CASE WHEN x.market LIKE 'most_%' THEN
                      CASE WHEN x.outcome = s.venue THEN 1 WHEN x.outcome = 'draw' THEN 2 ELSE 3 END
                 ELSE (CASE WHEN x.side = s.venue THEN 0 WHEN x.side = 'match' THEN 60 ELSE 30 END)
                      + COALESCE(x.line::NUMERIC, 0) / 100 END)::NUMERIC
  FROM mm x
  JOIN sides s ON s.match_id = x.match_id
  WHERE x.side IN ('home', 'away', 'match')
  UNION ALL
  -- season chances for the team (Upgrades 8 and 11)
  SELECT s.match_id, s.team_id, 'Season', v.market_name, 'Yes', (v.p / 100.0)::FLOAT8, NULL::FLOAT8, (900 + v.k)::NUMERIC
  FROM sides s CROSS JOIN r
  JOIN teams t ON t.team_id = s.team_id
  JOIN v_sim_summary ss ON ss.run_label = r.run_label AND ss.team_name = t.team_name
  CROSS JOIN LATERAL (VALUES ('Win the league', ss.title_pct, 1), ('Top 4', ss.top4_pct, 2),
                             ('Relegated', ss.relegation_pct, 3)) v(market_name, p, k)
)
SELECT t.team_name AS team, ht.team_name || ' v ' || at.team_name AS fixture, o.team_name AS opponent,
       s.venue, q.market_group, q.market_name, q.line_label,
       ROUND((100 * q.p)::NUMERIC, 1) AS chance_pct,
       ROUND(q.expected::NUMERIC, 2) AS expected,
       q.sort_order
FROM q
JOIN sides s ON s.match_id = q.match_id AND s.team_id = q.team_id
JOIN matches m ON m.match_id = q.match_id
JOIN teams t  ON t.team_id = s.team_id
JOIN teams o  ON o.team_id = s.opp_id
JOIN teams ht ON ht.team_id = m.home_team_id
JOIN teams at ON at.team_id = m.away_team_id;

-- Check: Man City's match card. Expect about 120 rows; Win about 70-75%, over 2.5 goals about 60-70%.
SELECT market_group, market_name, line_label, chance_pct, expected
FROM v_fixture_card WHERE team = 'Manchester City' ORDER BY sort_order;


-- 15.10h  Player hit rates: every current player's numbers in each of his club's last 5 league games
--         (any season), including games he missed (minutes 0), so "4 of 5" is honest.
--         player_key matches v_player_card ("Haaland (Manchester City)").
CREATE OR REPLACE VIEW v_team_last5_games AS
SELECT * FROM (
  SELECT t.team_id, m.match_id, m.match_date, m.round_no, s.label AS season,
         CASE WHEN m.home_team_id = t.team_id THEN m.away_team_id ELSE m.home_team_id END AS opp_id,
         CASE WHEN m.home_team_id = t.team_id THEN 'H' ELSE 'A' END AS venue,
         CASE WHEN m.home_team_id = t.team_id THEN m.home_goals ELSE m.away_goals END AS gf,
         CASE WHEN m.home_team_id = t.team_id THEN m.away_goals ELSE m.home_goals END AS ga,
         ROW_NUMBER() OVER (PARTITION BY t.team_id ORDER BY m.match_date DESC, m.match_id DESC) AS games_ago
  FROM teams t
  JOIN matches m ON t.team_id IN (m.home_team_id, m.away_team_id)
  JOIN seasons s ON s.season_id = m.season_id
  WHERE m.home_goals IS NOT NULL
) x
WHERE games_ago <= 5;

CREATE OR REPLACE VIEW v_player_last5 AS
WITH cur AS (   -- each player's current club = his most recent appearance this season
  SELECT DISTINCT ON (player_code) player_code, team_id, season
  FROM v_player_match_ids
  WHERE match_id IS NOT NULL AND season = (SELECT MAX(season) FROM pl_player_matches)
  ORDER BY player_code, match_date DESC
)
SELECT COALESCE(pl.web_name, c.player_code::TEXT) || ' (' || tm.team_name || ')' AS player_key,
       COALESCE(pl.web_name, c.player_code::TEXT) AS player, tm.team_name AS team, pl.position,
       g.games_ago, g.season, g.round_no AS gameweek, g.match_date, opp.team_name AS opponent, g.venue,
       g.gf || '-' || g.ga AS score,
       CASE WHEN g.gf > g.ga THEN 'W' WHEN g.gf = g.ga THEN 'D' ELSE 'L' END AS result,
       COALESCE(p.minutes, 0) AS minutes, COALESCE(p.started, FALSE) AS started,
       COALESCE(p.minutes, 0) > 0 AS played,
       COALESCE(p.goals, 0) AS goals, COALESCE(p.assists, 0) AS assists,
       COALESCE(p.goals, 0) + COALESCE(p.assists, 0) AS goals_assists,
       COALESCE(p.shots, 0) AS shots, COALESCE(p.shots_on_target, 0) AS shots_on_target,
       COALESCE(p.fouls_committed, 0) AS fouls, COALESCE(p.was_fouled, 0) AS fouled,
       COALESCE(p.tackles, 0) AS tackles, COALESCE(p.saves, 0) AS saves,
       COALESCE(cd.yellows, 0) AS yellows, COALESCE(cd.reds, 0) AS reds,
       (COALESCE(cd.yellows, 0) + COALESCE(cd.reds, 0)) > 0 AS carded,
       ROUND(COALESCE(p.xg, 0)::NUMERIC, 2) AS xg, ROUND(COALESCE(p.xa, 0)::NUMERIC, 2) AS xa
FROM cur c
JOIN v_team_last5_games g ON g.team_id = c.team_id
JOIN teams tm  ON tm.team_id = c.team_id
JOIN teams opp ON opp.team_id = g.opp_id
LEFT JOIN pl_players pl ON pl.season = c.season AND pl.player_code = c.player_code
LEFT JOIN v_player_match_ids p ON p.player_code = c.player_code AND p.match_id = g.match_id
LEFT JOIN LATERAL (
  SELECT SUM(f.yellow_cards) AS yellows, SUM(f.red_cards) AS reds
  FROM pl_player_fpl f
  WHERE p.player_code IS NOT NULL AND f.player_code = p.player_code AND f.season = p.season
    AND (f.match_date = p.match_date OR (f.match_date IS NULL AND f.gameweek = p.gameweek))
) cd ON TRUE;

-- Check: Haaland's last 5 (expect 5 rows, newest first, with goals, shots and minutes filled)
SELECT games_ago, gameweek, opponent, venue, score, minutes, goals, assists, shots, shots_on_target,
       fouls, fouled, tackles, carded
FROM v_player_last5 WHERE player = 'Haaland' ORDER BY games_ago;


-- 15.10i  Team hit rates: every team's last 5 league games with goals, corners, fouls, cards and shots
--         for and against, and the match totals bookmakers price.
CREATE OR REPLACE VIEW v_team_last5 AS
SELECT tm.team_name AS team, g.games_ago, g.season, g.round_no AS gameweek, g.match_date,
       opp.team_name AS opponent, g.venue, g.gf || '-' || g.ga AS score,
       CASE WHEN g.gf > g.ga THEN 'W' WHEN g.gf = g.ga THEN 'D' ELSE 'L' END AS result,
       g.gf AS goals_for, g.ga AS goals_against, g.gf + g.ga AS match_goals,
       (g.gf > 0 AND g.ga > 0) AS btts, (g.gf + g.ga >= 3) AS over_2_5, (g.ga = 0) AS clean_sheet,
       ROUND((CASE WHEN g.venue = 'H' THEN x.xg_home ELSE x.xg_away END)::NUMERIC, 2) AS xg_for,
       ROUND((CASE WHEN g.venue = 'H' THEN x.xg_away ELSE x.xg_home END)::NUMERIC, 2) AS xg_against,
       CASE WHEN g.venue = 'H' THEN s.home_corners ELSE s.away_corners END AS corners_for,
       CASE WHEN g.venue = 'H' THEN s.away_corners ELSE s.home_corners END AS corners_against,
       s.home_corners + s.away_corners AS match_corners,
       CASE WHEN g.venue = 'H' THEN s.home_fouls ELSE s.away_fouls END AS fouls_for,
       CASE WHEN g.venue = 'H' THEN s.away_fouls ELSE s.home_fouls END AS fouls_against,
       s.home_fouls + s.away_fouls AS match_fouls,
       CASE WHEN g.venue = 'H' THEN s.home_yellows + s.home_reds ELSE s.away_yellows + s.away_reds END AS cards_for,
       CASE WHEN g.venue = 'H' THEN s.away_yellows + s.away_reds ELSE s.home_yellows + s.home_reds END AS cards_against,
       s.home_yellows + s.home_reds + s.away_yellows + s.away_reds AS match_cards,
       s.home_booking_points + s.away_booking_points AS match_booking_points,
       CASE WHEN g.venue = 'H' THEN s.home_shots ELSE s.away_shots END AS shots_for,
       CASE WHEN g.venue = 'H' THEN s.away_shots ELSE s.home_shots END AS shots_against,
       CASE WHEN g.venue = 'H' THEN s.home_sot ELSE s.away_sot END AS sot_for,
       CASE WHEN g.venue = 'H' THEN s.away_sot ELSE s.home_sot END AS sot_against,
       s.referee
FROM v_team_last5_games g
JOIN teams tm  ON tm.team_id = g.team_id
JOIN teams opp ON opp.team_id = g.opp_id
LEFT JOIN v_team_match_stats s ON s.match_id = g.match_id
LEFT JOIN match_xg x ON x.match_id = g.match_id
WHERE g.team_id IN (SELECT team_id FROM v_team_last5_games
                    WHERE season = (SELECT MAX(label) FROM seasons s2
                                    JOIN matches m2 ON m2.season_id = s2.season_id
                                    WHERE m2.home_goals IS NOT NULL));

-- Check: 20 teams x 5 games = 100 rows, corners and cards filled
SELECT team, COUNT(*) AS games, ROUND(AVG(match_corners), 1) AS avg_match_corners,
       ROUND(AVG(match_cards), 1) AS avg_match_cards
FROM v_team_last5 GROUP BY team ORDER BY team;
