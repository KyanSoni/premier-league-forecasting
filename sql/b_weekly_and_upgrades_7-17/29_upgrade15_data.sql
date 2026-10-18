-- =====================================================================
-- UPGRADE 15.1: player and team match data (checks and views)
-- First run in VS Code:  python player_data.py history
-- The script creates and fills these tables itself:
--   pl_players, pl_player_matches, pl_player_fpl, pl_player_understat,
--   team_match_stats, fixture_referees
-- Then run each block below on its own.
-- =====================================================================

-- 15.1a  Team-name check: every club name the new data uses (2023-24 on) must be known to your
--        model (expect 0 rows). For each row listed, add an alias, e.g.
--        INSERT INTO team_aliases (alias, team_id) SELECT 'Sheffield Utd', team_id FROM teams WHERE team_name = 'Sheffield United';
WITH used AS (
  SELECT home_team AS team, 'team_match_stats (' || source || ')' AS found_in FROM team_match_stats WHERE season >= '2023-24'
  UNION SELECT away_team, 'team_match_stats (' || source || ')' FROM team_match_stats WHERE season >= '2023-24'
  UNION SELECT team, 'pl_player_matches' FROM pl_player_matches WHERE team IS NOT NULL
  UNION SELECT team, 'pl_player_fpl' FROM pl_player_fpl WHERE season >= '2023-24' AND team IS NOT NULL
  UNION SELECT team, 'pl_players' FROM pl_players WHERE season >= '2023-24' AND team IS NOT NULL
  UNION SELECT home_team, 'fixture_referees' FROM fixture_referees
  UNION SELECT away_team, 'fixture_referees' FROM fixture_referees
)
SELECT team AS unknown_team, STRING_AGG(found_in, ', ') AS found_in
FROM used
WHERE team NOT IN (SELECT alias FROM team_aliases UNION SELECT team_name FROM teams)
GROUP BY team ORDER BY team;

-- 15.1b  Views that put your team ids and match ids on the new data (run once; rerun is safe)
CREATE OR REPLACE VIEW v_team_names AS
SELECT alias AS name, team_id FROM team_aliases
UNION SELECT team_name, team_id FROM teams;

CREATE OR REPLACE VIEW v_team_match_stats AS
SELECT t.season, t.match_date, m.round_no, m.match_id, t.referee,
       t.home_team, t.away_team, h.team_id AS home_team_id, a.team_id AS away_team_id,
       t.home_goals, t.away_goals, t.home_shots, t.away_shots, t.home_sot, t.away_sot,
       t.home_corners, t.away_corners, t.home_fouls, t.away_fouls,
       t.home_yellows, t.away_yellows, t.home_reds, t.away_reds,
       10 * t.home_yellows + 25 * t.home_reds AS home_booking_points,
       10 * t.away_yellows + 25 * t.away_reds AS away_booking_points
FROM team_match_stats t
LEFT JOIN v_team_names h ON h.name = t.home_team
LEFT JOIN v_team_names a ON a.name = t.away_team
LEFT JOIN seasons s      ON s.label = t.season
LEFT JOIN matches m      ON m.season_id = s.season_id
                        AND m.home_team_id = h.team_id AND m.away_team_id = a.team_id
WHERE t.source = 'football-data';

-- 15.1c  Check 1, the team data: 380 matches per past season from each source (2026-27 = games
--        played so far), every football-data match with a referee, and sensible averages
--        (about 10 corners, 20-23 fouls and 3.5-4.5 yellows per match)
SELECT source, season, COUNT(*) AS matches, COUNT(referee) AS with_referee,
       ROUND(AVG(home_corners + away_corners), 1) AS corners,
       ROUND(AVG(home_fouls + away_fouls), 1)     AS fouls,
       ROUND(AVG(home_yellows + away_yellows), 2) AS yellows,
       SUM(home_reds + away_reds)                 AS reds
FROM team_match_stats GROUP BY source, season ORDER BY season, source;

-- 15.1d  Check 2, linked to your matches table: from 2023-24 every match should be linked
--        (linked = matches). Older seasons aren't in your matches table, which is fine:
--        they only feed the referee and spread figures.
SELECT season, COUNT(*) AS matches, COUNT(match_id) AS linked,
       COUNT(*) FILTER (WHERE home_team_id IS NULL OR away_team_id IS NULL) AS unknown_names
FROM v_team_match_stats GROUP BY season ORDER BY season;

-- 15.1e  Check 3, the two sources agree: FPL-Core-Insights vs football-data on the same matches.
--        Expect average differences well under 1 per match and most matches identical.
SELECT f.season, COUNT(*) AS matches,
       ROUND(AVG(ABS((f.home_corners + f.away_corners) - (d.home_corners + d.away_corners))), 2) AS corners_diff,
       ROUND(AVG(ABS((f.home_fouls + f.away_fouls) - (d.home_fouls + d.away_fouls))), 2)         AS fouls_diff,
       ROUND(AVG(ABS((f.home_yellows + f.away_yellows) - (d.home_yellows + d.away_yellows))), 2) AS yellows_diff,
       ROUND(100.0 * AVG(((f.home_corners + f.away_corners) = (d.home_corners + d.away_corners))::INT), 0) AS corners_same_pct
FROM team_match_stats f
JOIN v_team_names fh ON fh.name = f.home_team
JOIN v_team_names fa ON fa.name = f.away_team
JOIN v_team_match_stats d ON d.season = f.season AND d.home_team_id = fh.team_id AND d.away_team_id = fa.team_id
WHERE f.source = 'fci'
GROUP BY f.season ORDER BY f.season;

-- 15.1f  Check 4, the player data adds up. Scores come from your own matches table (a few
--        scores in FPL-Core-Insights' match file are wrong, but its player rows are right).
--        Expect player goals = the score in about 9 matches in 10 (own goals make up the rest),
--        goals_too_many 0 or very close, 11 starters and about 990 minutes per team per match,
--        and only a handful of players with no side. 2025-26 and 2026-27 link once those
--        seasons' matches are in your matches table.
WITH pg AS (
  SELECT fci_match_id,
         SUM(goals) FILTER (WHERE was_home)     AS hg,
         SUM(goals) FILTER (WHERE NOT was_home) AS ag,
         COUNT(*)   FILTER (WHERE was_home IS NULL) AS no_side,
         SUM(started::INT) AS starters, SUM(minutes) AS minutes
  FROM pl_player_matches GROUP BY fci_match_id
),
sc AS (
  SELECT t.season, t.fci_match_id, m.home_goals, m.away_goals
  FROM team_match_stats t
  JOIN v_team_names h ON h.name = t.home_team
  JOIN v_team_names a ON a.name = t.away_team
  JOIN seasons s ON s.label = t.season
  LEFT JOIN matches m ON m.season_id = s.season_id AND m.home_team_id = h.team_id
                     AND m.away_team_id = a.team_id AND m.home_goals IS NOT NULL
  WHERE t.source = 'fci'
)
SELECT sc.season, COUNT(*) AS matches, COUNT(sc.home_goals) AS with_score,
       ROUND(100.0 * AVG((COALESCE(pg.hg, 0) = sc.home_goals AND COALESCE(pg.ag, 0) = sc.away_goals)::INT), 1) AS goals_exact_pct,
       SUM((COALESCE(pg.hg, 0) > sc.home_goals OR COALESCE(pg.ag, 0) > sc.away_goals)::INT) AS goals_too_many,
       ROUND(AVG(pg.starters) / 2, 1) AS starters_per_team,
       ROUND(AVG(pg.minutes) / 2, 0)  AS minutes_per_team,
       SUM(pg.no_side) AS players_no_side
FROM sc JOIN pg USING (fci_match_id)
GROUP BY sc.season ORDER BY sc.season;

-- 15.1g  Check 5, cards: FPL player cards vs football-data team cards per season. FPL is a little
--        lower (it ignores cards shown to unused subs and staff); within about 5% is fine.
SELECT f.season, f.yellows AS fpl_yellows, d.yellows AS fd_yellows,
       f.reds AS fpl_reds, d.reds AS fd_reds, f.player_rows
FROM (SELECT season, SUM(yellow_cards) AS yellows, SUM(red_cards) AS reds, COUNT(*) AS player_rows
      FROM pl_player_fpl GROUP BY season) f
LEFT JOIN (SELECT season, SUM(home_yellows + away_yellows) AS yellows, SUM(home_reds + away_reds) AS reds
           FROM team_match_stats WHERE source = 'football-data' GROUP BY season) d USING (season)
ORDER BY f.season;

-- 15.1h  Check 6, Understat 2023-24 matched to FPL players: expect 90%+ of minutes matched
SELECT season, COUNT(*) AS players, COUNT(player_code) AS matched,
       ROUND(100.0 * SUM(minutes) FILTER (WHERE player_code IS NOT NULL) / SUM(minutes), 1) AS minutes_matched_pct
FROM pl_player_understat GROUP BY season;

-- 15.1i  Referee stats (used in 15.4). One row per referee per season plus an 'all' row.
--        Factors pull referees with few games back to average (10 games for yellows and fouls,
--        40 for reds); 15.4 adds the weighting towards recent seasons.
CREATE OR REPLACE VIEW v_referee_stats AS
WITH g AS (
  SELECT season, referee, home_yellows AS hy, away_yellows AS ay, home_reds AS hr, away_reds AS ar,
         home_fouls AS hf, away_fouls AS af
  FROM team_match_stats WHERE source = 'football-data' AND referee IS NOT NULL
),
per AS (
  SELECT season, referee, COUNT(*) AS matches,
         AVG(hy + ay) AS yellows, AVG(hr + ar) AS reds, AVG(10 * (hy + ay) + 25 * (hr + ar)) AS booking_points,
         AVG(hf + af) AS fouls, AVG(hy) AS home_yellows, AVG(ay) AS away_yellows,
         AVG(hf) AS home_fouls, AVG(af) AS away_fouls
  FROM g GROUP BY season, referee
  UNION ALL
  SELECT 'all', referee, COUNT(*), AVG(hy + ay), AVG(hr + ar), AVG(10 * (hy + ay) + 25 * (hr + ar)),
         AVG(hf + af), AVG(hy), AVG(ay), AVG(hf), AVG(af)
  FROM g GROUP BY referee
),
lg AS (
  SELECT season, AVG(hy + ay) AS y, AVG(hr + ar) AS r, AVG(hf + af) AS f,
         AVG(10 * (hy + ay) + 25 * (hr + ar)) AS bp
  FROM g GROUP BY season
  UNION ALL
  SELECT 'all', AVG(hy + ay), AVG(hr + ar), AVG(hf + af), AVG(10 * (hy + ay) + 25 * (hr + ar)) FROM g
)
SELECT p.season, p.referee, p.matches,
       ROUND(p.yellows, 2) AS yellows_pg, ROUND(p.reds, 3) AS reds_pg,
       ROUND(p.booking_points, 1) AS booking_points_pg, ROUND(p.fouls, 1) AS fouls_pg,
       ROUND(p.home_yellows, 2) AS home_yellows_pg, ROUND(p.away_yellows, 2) AS away_yellows_pg,
       ROUND(p.home_fouls, 1) AS home_fouls_pg, ROUND(p.away_fouls, 1) AS away_fouls_pg,
       ROUND(p.yellows / l.y, 2) AS yellows_vs_league, ROUND(p.fouls / l.f, 2) AS fouls_vs_league,
       ROUND(((p.yellows * p.matches + 10 * l.y) / (p.matches + 10)) / l.y, 3) AS yellow_factor,
       ROUND(((p.fouls * p.matches + 10 * l.f) / (p.matches + 10)) / l.f, 3)   AS foul_factor,
       ROUND(((p.reds * p.matches + 40 * NULLIF(l.r, 0)) / (p.matches + 40)) / NULLIF(l.r, 0), 3) AS red_factor
FROM per p JOIN lg l USING (season);

-- 15.1j  Check 7, referees: the strictest and most lenient regulars over all seasons
SELECT referee, matches, yellows_pg, reds_pg, booking_points_pg, fouls_pg, yellow_factor, foul_factor
FROM v_referee_stats WHERE season = 'all' AND matches >= 20
ORDER BY yellows_pg DESC;

-- 15.1k  Each gameweek, after 'python player_data.py referees load': every appointed referee
--        should have history (matches filled in). A blank means a new referee (fine, factor 1)
--        or a spelling that differs from football-data's (fix it in fixture_referees).
SELECT f.gameweek, f.home_team, f.away_team, f.referee, r.matches, r.yellows_pg, r.fouls_pg
FROM fixture_referees f
LEFT JOIN v_referee_stats r ON r.season = 'all' AND r.referee = f.referee
WHERE f.gameweek = (SELECT MAX(gameweek) FROM fixture_referees)
ORDER BY f.home_team;


-- =====================================================================
-- UPGRADE 15.2: expected minutes
-- First run in VS Code:  python player_minutes.py backtest
-- =====================================================================

-- 15.2a  Check the predictions were stored: one row per gameweek, about 11 expected starters
--        and about 985 expected minutes per team (2024-25 and 2025-26: 37 gameweeks each)
SELECT season, COUNT(DISTINCT run_label) AS gameweeks, COUNT(*) AS player_rows,
       ROUND(SUM(p_start) / COUNT(DISTINCT (match_id, team_id)), 2) AS starters_per_team,
       ROUND(SUM(exp_minutes) / COUNT(DISTINCT (match_id, team_id)), 0) AS minutes_per_team
FROM player_minutes WHERE mode = 'backtest'
GROUP BY season ORDER BY season;

-- 15.2b  Two views: the player data with your team and match ids, and predicted vs actual
--        minutes (players who played but weren't predicted count as predicted 0)
CREATE OR REPLACE VIEW v_player_match_ids AS
SELECT p.*, t.team_id, o.team_id AS opp_id, m.match_id
FROM pl_player_matches p
JOIN v_team_names t ON t.name = p.team
JOIN v_team_names o ON o.name = p.opponent
JOIN seasons s ON s.label = p.season
LEFT JOIN matches m ON m.season_id = s.season_id
      AND m.home_team_id = CASE WHEN p.was_home THEN t.team_id ELSE o.team_id END
      AND m.away_team_id = CASE WHEN p.was_home THEN o.team_id ELSE t.team_id END;

CREATE OR REPLACE VIEW v_minutes_test AS
WITH pred AS (SELECT * FROM player_minutes WHERE mode = 'backtest'),
fx AS (SELECT DISTINCT run_label, season, match_id FROM pred),
act AS (SELECT match_id, player_code, team_id, minutes, started
        FROM v_player_match_ids WHERE match_id IN (SELECT match_id FROM fx))
SELECT f.run_label, f.season, f.match_id,
       COALESCE(p.player_code, a.player_code) AS player_code,
       COALESCE(p.team_id, a.team_id) AS team_id,
       COALESCE(p.position, pl.position) AS position,
       COALESCE(p.p_start, 0) AS p_start, COALESCE(p.baseline_p_start, 0) AS baseline_p_start,
       COALESCE(p.exp_minutes, 0) AS exp_minutes, COALESCE(p.baseline_minutes, 0) AS baseline_minutes,
       COALESCE(a.minutes, 0) AS minutes, COALESCE(a.started, false) AS started,
       (p.player_code IS NULL) AS not_predicted
FROM pred p
FULL JOIN act a ON a.match_id = p.match_id AND a.player_code = p.player_code
JOIN fx f ON f.match_id = COALESCE(p.match_id, a.match_id)
LEFT JOIN pl_players pl ON pl.season = f.season AND pl.player_code = COALESCE(p.player_code, a.player_code);

-- 15.2c  The test (lower is better), both seasons. minutes_error = the typical miss in minutes
--        (root mean squared error, the right measure for an expected value); start_brier = how
--        good the chance of starting is. The model should beat the baseline (his average over
--        the last 5 matches) on both, in both seasons. bias near 0 = no over- or under-prediction.
SELECT season, COUNT(*) AS player_matches,
       SUM(not_predicted::INT) AS played_not_predicted,
       ROUND(SQRT(AVG(POWER(exp_minutes - minutes, 2))), 1)      AS minutes_error,
       ROUND(SQRT(AVG(POWER(baseline_minutes - minutes, 2))), 1) AS baseline_minutes_error,
       ROUND(AVG(exp_minutes - minutes), 2)                      AS bias,
       ROUND(AVG(POWER(p_start - started::INT, 2)), 4)           AS start_brier,
       ROUND(AVG(POWER(baseline_p_start - started::INT, 2)), 4)  AS baseline_start_brier
FROM v_minutes_test GROUP BY season ORDER BY season;

-- 15.2d  The same by position: the model should beat the baseline in every row. Goalkeepers
--        have the best start_brier (the No. 1 rarely changes); forwards the smallest minutes
--        error (they're often subbed, so fewer full-90 vs 0 surprises).
SELECT season, position, COUNT(*) AS player_matches,
       ROUND(SQRT(AVG(POWER(exp_minutes - minutes, 2))), 1)      AS minutes_error,
       ROUND(SQRT(AVG(POWER(baseline_minutes - minutes, 2))), 1) AS baseline_minutes_error,
       ROUND(AVG(POWER(p_start - started::INT, 2)), 4)           AS start_brier,
       ROUND(AVG(POWER(baseline_p_start - started::INT, 2)), 4)  AS baseline_start_brier
FROM v_minutes_test WHERE season IN ('2024-25', '2025-26')
GROUP BY season, position ORDER BY season, position;

-- 15.2e  Calibration: players given a 70-80% chance of starting should start about 75% of the time
SELECT LEAST(FLOOR(p_start * 10), 9) * 10 || '-' || (LEAST(FLOOR(p_start * 10), 9) + 1) * 10 || '%' AS chance_band,
       COUNT(*) AS players, ROUND(100 * AVG(p_start), 1) AS predicted_pct,
       ROUND(100 * AVG(started::INT), 1) AS actually_started_pct
FROM v_minutes_test WHERE season IN ('2024-25', '2025-26') AND NOT not_predicted
GROUP BY 1 ORDER BY MIN(p_start);

-- 15.2f  After 'python player_minutes.py next': the next gameweek's likely line-ups, one team at a
--        time (change the team name), with FPL's flags. Check it against what you know.
SELECT t.team_name, pl.web_name, pm.position, ROUND(100 * pm.p_start) AS start_pct,
       ROUND(100 * pm.p_sub) AS sub_pct, ROUND(pm.exp_minutes) AS exp_minutes,
       ROUND(100 * pm.availability) AS fpl_chance_pct
FROM player_minutes pm
JOIN teams t ON t.team_id = pm.team_id
LEFT JOIN pl_players pl ON pl.season = pm.season AND pl.player_code = pm.player_code
WHERE pm.mode = 'next' AND t.team_name = 'Arsenal'
  AND pm.run_label = (SELECT run_label FROM player_minutes WHERE mode = 'next'
                       ORDER BY season DESC, gameweek DESC LIMIT 1)
ORDER BY pm.p_start DESC, pm.exp_minutes DESC;


-- =====================================================================
-- UPGRADE 15.3: player rates per 90
-- First run in VS Code:  python player_rates.py backtest
-- (needs player_minutes from 15.2). Each test below uses the minutes the player actually
-- played, so it judges the rates on their own, separately from the minutes in 15.2.
-- =====================================================================

-- 15.3a  Check the rates were stored: rows per season, and how many players played in the
--        predicted match (those are the ones tested). Expect the same gameweeks as 15.2a.
SELECT season, COUNT(DISTINCT run_label) AS gameweeks, COUNT(*) AS player_rows,
       COUNT(*) FILTER (WHERE act_minutes > 0) AS played_and_tested,
       ROUND(AVG(xg_90)::NUMERIC, 3) AS avg_xg_90, ROUND(AVG(fouls_90)::NUMERIC, 2) AS avg_fouls_90,
       ROUND(AVG(yellow_90)::NUMERIC, 3) AS avg_yellow_90
FROM player_rates WHERE mode = 'backtest'
GROUP BY season ORDER BY season;

-- 15.3b  The test: chance of 1 or more in the next match, model vs baseline (lower Brier is
--        better), plus predicted vs actual totals (should be close: no bias). Goals are
--        predicted from the xG rate and assists from the xA rate; their baselines use actual
--        goals and assists, so this also shows whether xG beats goals as a guide.
WITH t AS (
  SELECT r.season, v.stat, v.rate, v.base, v.actual, r.act_minutes
  FROM player_rates r
  CROSS JOIN LATERAL (VALUES
      ('1 goals (from xG)',   r.xg_90,      r.base_xg_90,      r.act_goals),
      ('2 assists (from xA)', r.xa_90,      r.base_xa_90,      r.act_assists),
      ('3 shots',             r.shots_90,   r.base_shots_90,   r.act_shots),
      ('4 shots on target',   r.sot_90,     r.base_sot_90,     r.act_sot),
      ('5 fouls committed',   r.fouls_90,   r.base_fouls_90,   r.act_fouls),
      ('6 fouled',            r.fouled_90,  r.base_fouled_90,  r.act_fouled),
      ('7 tackles',           r.tackles_90, r.base_tackles_90, r.act_tackles),
      ('8 yellow card',       r.yellow_90,  r.base_yellow_90,  r.act_yellow),
      ('9 red card',          r.red_90,     r.base_red_90,     r.act_red)) AS v(stat, rate, base, actual)
  WHERE r.mode = 'backtest' AND r.act_minutes > 0 AND v.actual IS NOT NULL
    AND r.season IN ('2024-25', '2025-26')
),
mu AS (SELECT season, stat, actual, rate * act_minutes / 90.0 AS m,
              GREATEST(base * act_minutes / 90.0, 0) AS b FROM t)
SELECT stat, season, COUNT(*) AS players,
       ROUND(SUM(m)::NUMERIC, 0) AS predicted_total, SUM(actual)::INT AS actual_total,
       ROUND(AVG(POWER((1 - EXP(-m)) - (actual >= 1)::INT, 2))::NUMERIC, 4) AS brier_1plus,
       ROUND(AVG(POWER((1 - EXP(-b)) - (actual >= 1)::INT, 2))::NUMERIC, 4) AS baseline_brier_1plus
FROM mu GROUP BY stat, season ORDER BY stat, season;

-- 15.3c  The "2 or more" markets (shots, shots on target, fouls, fouled, tackles): same test
WITH t AS (
  SELECT r.season, v.stat, v.rate, v.base, v.actual, r.act_minutes
  FROM player_rates r
  CROSS JOIN LATERAL (VALUES
      ('3 shots',           r.shots_90,   r.base_shots_90,   r.act_shots),
      ('4 shots on target', r.sot_90,     r.base_sot_90,     r.act_sot),
      ('5 fouls committed', r.fouls_90,   r.base_fouls_90,   r.act_fouls),
      ('6 fouled',          r.fouled_90,  r.base_fouled_90,  r.act_fouled),
      ('7 tackles',         r.tackles_90, r.base_tackles_90, r.act_tackles)) AS v(stat, rate, base, actual)
  WHERE r.mode = 'backtest' AND r.act_minutes > 0 AND v.actual IS NOT NULL
    AND r.season IN ('2024-25', '2025-26')
),
mu AS (SELECT season, stat, actual, rate * act_minutes / 90.0 AS m,
              GREATEST(base * act_minutes / 90.0, 0) AS b FROM t)
SELECT stat, season, COUNT(*) AS players,
       ROUND(100 * AVG((actual >= 2)::INT)::NUMERIC, 1) AS actual_2plus_pct,
       ROUND(100 * AVG(1 - EXP(-m) * (1 + m))::NUMERIC, 1) AS predicted_2plus_pct,
       ROUND(AVG(POWER((1 - EXP(-m) * (1 + m)) - (actual >= 2)::INT, 2))::NUMERIC, 4) AS brier_2plus,
       ROUND(AVG(POWER((1 - EXP(-b) * (1 + b)) - (actual >= 2)::INT, 2))::NUMERIC, 4) AS baseline_brier_2plus
FROM mu GROUP BY stat, season ORDER BY stat, season;

-- 15.3d  Who the rates rate highest: the top 15 for one stat in the latest gameweek
--        (change xg_90 to shots_90, fouls_90, fouled_90, tackles_90 or yellow_90)
SELECT pl.web_name, t.team_name, r.position, ROUND(r.xg_90::NUMERIC, 2) AS xg_90,
       ROUND(r.shots_90::NUMERIC, 2) AS shots_90, ROUND(r.fouled_90::NUMERIC, 2) AS fouled_90,
       ROUND(r.yellow_90::NUMERIC, 3) AS yellow_90, r.minutes_season
FROM player_rates r
JOIN teams t ON t.team_id = r.team_id
LEFT JOIN pl_players pl ON pl.season = r.season AND pl.player_code = r.player_code
WHERE r.run_label = (SELECT run_label FROM player_rates ORDER BY season DESC, gameweek DESC LIMIT 1)
ORDER BY r.xg_90 DESC LIMIT 15;


-- =====================================================================
-- UPGRADE 15.4: team corners, fouls, cards, shots, shots on target, tackles
-- First run in VS Code:  python team_counts.py backtest   (prints the settings it chose)
-- side = 'home', 'away' or 'match' (both teams together)
-- =====================================================================

-- 15.4a  Check the predictions were stored (7 stats x 3 sides per match)
SELECT season, COUNT(DISTINCT run_label) AS gameweeks, COUNT(DISTINCT match_id) AS matches,
       COUNT(*) AS rows, COUNT(*) FILTER (WHERE actual IS NOT NULL) AS with_result
FROM team_counts WHERE mode = 'backtest'
GROUP BY season ORDER BY season;

-- 15.4b  The test: log score (lower = better) for each team's count, model vs baseline (league
--        average with home advantage). The model should win for every stat in both seasons.
SELECT stat, season, COUNT(*) AS team_matches,
       ROUND(AVG(logscore)::NUMERIC, 4)      AS log_score,
       ROUND(AVG(base_logscore)::NUMERIC, 4) AS baseline_log_score
FROM team_counts
WHERE mode = 'backtest' AND side IN ('home', 'away') AND logscore IS NOT NULL
  AND season IN ('2024-25', '2025-26')
GROUP BY stat, season ORDER BY stat, season;

-- 15.4c  The over/under test at the usual line (e.g. match corners over 10.5): Brier, model vs
--        baseline, for team totals and match totals, plus how often it went over vs predicted
SELECT stat, CASE WHEN side = 'match' THEN 'match' ELSE 'team' END AS level, season,
       ROUND(AVG(line)::NUMERIC, 1) AS typical_line, COUNT(*) AS n,
       ROUND(100 * AVG((actual > line)::INT)::NUMERIC, 1) AS actual_over_pct,
       ROUND(100 * AVG(p_over)::NUMERIC, 1) AS predicted_over_pct,
       ROUND(AVG(POWER(p_over - (actual > line)::INT, 2))::NUMERIC, 4)      AS brier,
       ROUND(AVG(POWER(base_p_over - (actual > line)::INT, 2))::NUMERIC, 4) AS baseline_brier
FROM team_counts
WHERE mode = 'backtest' AND actual IS NOT NULL AND season IN ('2024-25', '2025-26')
GROUP BY stat, 2, season ORDER BY stat, 2, season;

-- 15.4d  No bias: average expected vs actual per match (should be within a few %)
SELECT stat, season, ROUND(AVG(expected)::NUMERIC, 2) AS expected_per_match,
       ROUND(AVG(actual)::NUMERIC, 2) AS actual_per_match
FROM team_counts
WHERE mode = 'backtest' AND side = 'match' AND actual IS NOT NULL
GROUP BY stat, season ORDER BY stat, season;

-- 15.4e  Does the referee factor work? Matches grouped by the referee's factor: strict referees
--        (factor above 1.05) should see more cards than the baseline expects, lenient ones fewer.
--        (Empty if the backtest chose no referee factor for that stat.)
SELECT stat, CASE WHEN ref_factor < 0.95 THEN '1 lenient (<0.95)'
                  WHEN ref_factor > 1.05 THEN '3 strict (>1.05)' ELSE '2 average' END AS referee,
       COUNT(*) AS matches, ROUND(AVG(base_expected)::NUMERIC, 2) AS baseline_expected,
       ROUND(AVG(expected)::NUMERIC, 2) AS model_expected, ROUND(AVG(actual)::NUMERIC, 2) AS actual
FROM team_counts
WHERE mode = 'backtest' AND side = 'match' AND actual IS NOT NULL
  AND stat IN ('fouls', 'yellows', 'reds') AND season IN ('2024-25', '2025-26')
GROUP BY stat, 2 ORDER BY stat, 2;

-- 15.4f  After 'python team_counts.py next': next gameweek's expected counts per fixture
SELECT h.team_name AS home, a.team_name AS away, MAX(c.referee) AS referee,
       ROUND(SUM(c.expected) FILTER (WHERE c.stat = 'corners' AND c.side = 'home')::NUMERIC, 1) AS home_corners,
       ROUND(SUM(c.expected) FILTER (WHERE c.stat = 'corners' AND c.side = 'away')::NUMERIC, 1) AS away_corners,
       ROUND(SUM(c.expected) FILTER (WHERE c.stat = 'fouls'   AND c.side = 'match')::NUMERIC, 1) AS match_fouls,
       ROUND(SUM(c.expected) FILTER (WHERE c.stat = 'yellows' AND c.side = 'home')::NUMERIC, 2) AS home_yellows,
       ROUND(SUM(c.expected) FILTER (WHERE c.stat = 'yellows' AND c.side = 'away')::NUMERIC, 2) AS away_yellows,
       ROUND(SUM(c.expected) FILTER (WHERE c.stat = 'reds'    AND c.side = 'match')::NUMERIC, 3) AS match_reds
FROM team_counts c
JOIN teams h ON h.team_id = c.home_team_id
JOIN teams a ON a.team_id = c.away_team_id
WHERE c.mode = 'next'
  AND c.run_label = (SELECT run_label FROM team_counts WHERE mode = 'next' ORDER BY season DESC, gameweek DESC LIMIT 1)
GROUP BY h.team_name, a.team_name ORDER BY h.team_name;


-- =====================================================================
-- UPGRADE 15.5: attacking player markets (score, assist, goal or assist, shots, shots on target)
-- =====================================================================

-- 15.5a  Each team's expected goals for every predicted fixture, from your main model (the same
--        variants v_main_predictions uses: value_prom_50 before 2026-27 GW6, home_adv after).
--        Run once now, then again after each weekly run (it only adds/updates). Takes a minute or two.
CREATE TABLE IF NOT EXISTS match_lambdas (
  run_label TEXT, match_id INT, lambda_home NUMERIC, lambda_away NUMERIC,
  PRIMARY KEY (run_label, match_id));

INSERT INTO match_lambdas (run_label, match_id, lambda_home, lambda_away)
SELECT r.run_label, m.match_id,
       h.att * a.def * ra.avg_goals * v.home_factor,
       a.att * h.def * ra.avg_goals * v.away_factor
FROM v_runs r
JOIN v_run_avg ra     ON ra.run_id = r.run_id
JOIN model_variants v ON v.variant = CASE WHEN r.season >= '2026-27' AND r.gameweek >= 6
                                          THEN 'home_adv' ELSE 'value_prom_50' END
JOIN matches m        ON m.season_id = r.season_id AND m.round_no = r.gameweek + 1
JOIN v_variant_ratings h ON h.variant = v.variant AND h.run_id = r.run_id AND h.team_id = m.home_team_id
JOIN v_variant_ratings a ON a.variant = v.variant AND a.run_id = r.run_id AND a.team_id = m.away_team_id
WHERE r.run_label LIKE '%after GW%' AND r.season >= '2024-25'
ON CONFLICT (run_label, match_id)
DO UPDATE SET lambda_home = EXCLUDED.lambda_home, lambda_away = EXCLUDED.lambda_away;

-- 15.5a check: about 10 fixtures per gameweek, home about 1.5 and away about 1.2 expected goals
SELECT LEFT(run_label, 7) AS season, COUNT(DISTINCT run_label) AS gameweeks, COUNT(*) AS fixtures,
       ROUND(AVG(lambda_home), 2) AS avg_home_xg, ROUND(AVG(lambda_away), 2) AS avg_away_xg
FROM match_lambdas GROUP BY 1 ORDER BY 1;

-- 15.5a-i  Which gameweeks have a saved model run (backtest runs were only saved for some weeks)
SELECT season, COUNT(*) AS runs, STRING_AGG(gameweek::TEXT, ',' ORDER BY gameweek) AS gameweeks
FROM v_runs WHERE season >= '2024-25' AND run_label LIKE '%after GW%'
GROUP BY season ORDER BY season;

-- 15.5a-ii  Fill the gameweeks with no saved run, using the latest earlier run's team ratings
-- (no leak: those ratings only used games before that earlier run). Saved runs are never
-- overwritten, and the current season only uses its real weekly runs.
INSERT INTO match_lambdas (run_label, match_id, lambda_home, lambda_away)
SELECT sr.season || ' after GW' || g.gw, m.match_id,
       h.att * a.def * ra.avg_goals * v.home_factor,
       a.att * h.def * ra.avg_goals * v.away_factor
FROM (SELECT DISTINCT season_id, season FROM v_runs WHERE season >= '2024-25') sr
CROSS JOIN generate_series(1, 37) AS g(gw)
JOIN LATERAL (SELECT r.run_id, r.gameweek, r.season FROM v_runs r
              WHERE r.season_id = sr.season_id AND r.run_label LIKE '%after GW%' AND r.gameweek <= g.gw
              ORDER BY r.gameweek DESC LIMIT 1) r ON TRUE
JOIN v_run_avg ra ON ra.run_id = r.run_id
JOIN model_variants v ON v.variant = CASE WHEN r.season >= '2026-27' AND r.gameweek >= 6 THEN 'home_adv' ELSE 'value_prom_50' END
JOIN matches m ON m.season_id = sr.season_id AND m.round_no = g.gw + 1
JOIN v_variant_ratings h ON h.variant = v.variant AND h.run_id = r.run_id AND h.team_id = m.home_team_id
JOIN v_variant_ratings a ON a.variant = v.variant AND a.run_id = r.run_id AND a.team_id = m.away_team_id
WHERE sr.season < '2026-27' OR g.gw = r.gameweek
ON CONFLICT (run_label, match_id) DO NOTHING;

-- (then rerun the 15.5a check: expect about 33-37 gameweeks for 2024-25 and 2025-26)

-- 15.5b  After 'python player_markets.py backtest': storage check (11 markets per player-fixture)
SELECT season, COUNT(DISTINCT run_label) AS gameweeks, COUNT(*) AS rows,
       COUNT(*) FILTER (WHERE act_minutes > 0) AS rows_where_he_played
FROM player_markets WHERE mode = 'backtest'
GROUP BY season ORDER BY season;

-- 15.5c  The test: Brier (lower = better) for every market and line, for players who played.
--        model should beat baseline everywhere; rates_only shows what the team and opponent add
--        (model below rates_only = the opponent/team scaling helps).
SELECT market, line, season, COUNT(*) AS players,
       ROUND(100 * AVG((actual >= line)::INT)::NUMERIC, 1) AS actual_pct,
       ROUND(100 * AVG(prob)::NUMERIC, 1) AS predicted_pct,
       ROUND(AVG(POWER(prob - (actual >= line)::INT, 2))::NUMERIC, 4)            AS brier,
       ROUND(AVG(POWER(rates_only_prob - (actual >= line)::INT, 2))::NUMERIC, 4) AS rates_only_brier,
       ROUND(AVG(POWER(baseline_prob - (actual >= line)::INT, 2))::NUMERIC, 4)   AS baseline_brier
FROM player_markets
WHERE mode = 'backtest' AND act_minutes > 0 AND actual IS NOT NULL AND season IN ('2024-25', '2025-26')
GROUP BY market, line, season ORDER BY market, line, season;

-- 15.5d  Calibration for 'to score': players given 30-40% should score about 35% of the time
SELECT LEAST(FLOOR(prob * 10), 9) * 10 || '-' || (LEAST(FLOOR(prob * 10), 9) + 1) * 10 || '%' AS chance_band,
       COUNT(*) AS players, ROUND(100 * AVG(prob)::NUMERIC, 1) AS predicted_pct,
       ROUND(100 * AVG((actual >= 1)::INT)::NUMERIC, 1) AS actually_scored_pct
FROM player_markets
WHERE mode = 'backtest' AND market = 'goals' AND line = 1 AND act_minutes > 0 AND prob IS NOT NULL
  AND season IN ('2024-25', '2025-26')
GROUP BY 1 ORDER BY MIN(prob);

-- 15.5e  The opponent effect: one player's chance to score against each opponent in a season
--        (change the name). Weaker defences should be near the top.
SELECT pl.web_name, opp.team_name AS opponent, CASE WHEN m.home_team_id = pm.team_id THEN 'home' ELSE 'away' END AS venue,
       ROUND(100 * pm.prob::NUMERIC) AS score_pct, ROUND(100 * pm.rates_only_prob::NUMERIC) AS without_opponent_pct,
       pm.actual AS goals
FROM player_markets pm
JOIN matches m ON m.match_id = pm.match_id
JOIN teams opp ON opp.team_id = CASE WHEN m.home_team_id = pm.team_id THEN m.away_team_id ELSE m.home_team_id END
JOIN pl_players pl ON pl.season = pm.season AND pl.player_code = pm.player_code
WHERE pm.mode = 'backtest' AND pm.market = 'goals' AND pm.line = 1
  AND pm.season = '2025-26' AND pl.web_name = 'Haaland'
ORDER BY pm.prob DESC;

-- 15.5f  After 'python player_markets.py next': the next gameweek's likeliest scorers
SELECT pl.web_name, t.team_name, opp.team_name AS opponent,
       ROUND(100 * MAX(pm.prob) FILTER (WHERE pm.market = 'goals' AND pm.line = 1)::NUMERIC) AS score_pct,
       ROUND(100 * MAX(pm.prob) FILTER (WHERE pm.market = 'goals' AND pm.line = 2)::NUMERIC) AS score_2plus_pct,
       ROUND(100 * MAX(pm.prob) FILTER (WHERE pm.market = 'assists')::NUMERIC) AS assist_pct,
       ROUND(100 * MAX(pm.prob) FILTER (WHERE pm.market = 'sot' AND pm.line = 1)::NUMERIC) AS sot_pct,
       ROUND(100 * MAX(pm.p_start)::NUMERIC) AS start_pct
FROM player_markets pm
JOIN matches m ON m.match_id = pm.match_id
JOIN teams t ON t.team_id = pm.team_id
JOIN teams opp ON opp.team_id = CASE WHEN m.home_team_id = pm.team_id THEN m.away_team_id ELSE m.home_team_id END
LEFT JOIN pl_players pl ON pl.season = pm.season AND pl.player_code = pm.player_code
WHERE pm.mode = 'next'
  AND pm.run_label = (SELECT run_label FROM player_markets WHERE mode = 'next' ORDER BY season DESC, gameweek DESC LIMIT 1)
GROUP BY pl.web_name, t.team_name, opp.team_name
ORDER BY score_pct DESC LIMIT 25;

-- =====================================================================================
-- UPGRADE 15.6: defensive and discipline player markets (fouls, fouled, tackles, to be carded,
-- red card, keeper saves). Same table as 15.5: player_markets.py now builds both.
-- =====================================================================================

-- 15.6a  After 'python player_markets.py backtest': rows per market (keeper saves = keepers only)
SELECT market, COUNT(DISTINCT line) AS lines, COUNT(*) AS rows,
       COUNT(*) FILTER (WHERE act_minutes > 0) AS rows_where_he_played
FROM player_markets WHERE mode = 'backtest'
GROUP BY market ORDER BY market;

-- 15.6b  The test for the new markets: Brier (lower = better); model should beat baseline
SELECT market, line, season, COUNT(*) AS players,
       ROUND(100 * AVG((actual >= line)::INT)::NUMERIC, 1) AS actual_pct,
       ROUND(100 * AVG(prob)::NUMERIC, 1) AS predicted_pct,
       ROUND(AVG(POWER(prob - (actual >= line)::INT, 2))::NUMERIC, 4)            AS brier,
       ROUND(AVG(POWER(rates_only_prob - (actual >= line)::INT, 2))::NUMERIC, 4) AS rates_only_brier,
       ROUND(AVG(POWER(baseline_prob - (actual >= line)::INT, 2))::NUMERIC, 4)   AS baseline_brier
FROM player_markets
WHERE mode = 'backtest' AND act_minutes > 0 AND actual IS NOT NULL AND prob IS NOT NULL
  AND season IN ('2024-25', '2025-26')
  AND market IN ('fouls', 'fouled', 'tackles', 'carded', 'red', 'saves')
GROUP BY market, line, season ORDER BY market, line, season;

-- 15.6c  Calibration for 'to be carded': players given 20-30% should be booked about 25% of the time
SELECT LEAST(FLOOR(prob * 10), 9) * 10 || '-' || (LEAST(FLOOR(prob * 10), 9) + 1) * 10 || '%' AS chance_band,
       COUNT(*) AS players, ROUND(100 * AVG(prob)::NUMERIC, 1) AS predicted_pct,
       ROUND(100 * AVG((actual >= 1)::INT)::NUMERIC, 1) AS actually_carded_pct
FROM player_markets
WHERE mode = 'backtest' AND market = 'carded' AND act_minutes > 0 AND prob IS NOT NULL
  AND season IN ('2024-25', '2025-26')
GROUP BY 1 ORDER BY MIN(prob);

-- 15.6d  The referee effect on players: card and foul chances by how strict the referee is
--        (strictness = the referee factor 15.4 gave that match's fouls)
WITH ref AS (
  SELECT run_label, match_id, referee, ref_factor,
         CASE WHEN ref_factor < 0.97 THEN '1 lenient' WHEN ref_factor > 1.03 THEN '3 strict' ELSE '2 average' END AS band
  FROM team_counts WHERE mode = 'backtest' AND stat = 'fouls' AND side = 'match')
SELECT r.band, pm.market, COUNT(*) AS players,
       ROUND(100 * AVG(pm.prob)::NUMERIC, 1) AS predicted_pct,
       ROUND(100 * AVG((pm.actual >= pm.line)::INT)::NUMERIC, 1) AS actual_pct
FROM player_markets pm JOIN ref r ON r.run_label = pm.run_label AND r.match_id = pm.match_id
WHERE pm.mode = 'backtest' AND pm.act_minutes > 0 AND pm.prob IS NOT NULL
  AND ((pm.market = 'carded') OR (pm.market = 'fouls' AND pm.line = 2))
GROUP BY r.band, pm.market ORDER BY pm.market, r.band;

-- 15.6e  Keeper saves against each opponent: one keeper's chance of 3+ saves (change the name)
SELECT pl.web_name, opp.team_name AS opponent, CASE WHEN m.home_team_id = pm.team_id THEN 'home' ELSE 'away' END AS venue,
       ROUND(pm.expected::NUMERIC, 1) AS expected_saves,
       ROUND(100 * pm.prob::NUMERIC) AS three_plus_pct, pm.actual AS saves
FROM player_markets pm
JOIN matches m ON m.match_id = pm.match_id
JOIN teams opp ON opp.team_id = CASE WHEN m.home_team_id = pm.team_id THEN m.away_team_id ELSE m.home_team_id END
JOIN pl_players pl ON pl.season = pm.season AND pl.player_code = pm.player_code
WHERE pm.mode = 'backtest' AND pm.market = 'saves' AND pm.line = 3
  AND pm.season = '2025-26' AND pl.web_name = 'Pickford'
ORDER BY pm.prob DESC;

-- 15.6f  After 'python player_markets.py next': next gameweek's likeliest to be carded, with fouls
SELECT pl.web_name, t.team_name, opp.team_name AS opponent,
       ROUND(100 * MAX(pm.prob) FILTER (WHERE pm.market = 'carded')::NUMERIC) AS carded_pct,
       ROUND(100 * MAX(pm.prob) FILTER (WHERE pm.market = 'fouls' AND pm.line = 2)::NUMERIC) AS fouls_2plus_pct,
       ROUND(100 * MAX(pm.prob) FILTER (WHERE pm.market = 'fouled' AND pm.line = 2)::NUMERIC) AS fouled_2plus_pct,
       ROUND(100 * MAX(pm.prob) FILTER (WHERE pm.market = 'tackles' AND pm.line = 2)::NUMERIC) AS tackles_2plus_pct,
       ROUND(100 * MAX(pm.p_start)::NUMERIC) AS start_pct
FROM player_markets pm
JOIN matches m ON m.match_id = pm.match_id
JOIN teams t ON t.team_id = pm.team_id
JOIN teams opp ON opp.team_id = CASE WHEN m.home_team_id = pm.team_id THEN m.away_team_id ELSE m.home_team_id END
LEFT JOIN pl_players pl ON pl.season = pm.season AND pl.player_code = pm.player_code
WHERE pm.mode = 'next'
  AND pm.run_label = (SELECT run_label FROM player_markets WHERE mode = 'next' ORDER BY season DESC, gameweek DESC LIMIT 1)
GROUP BY pl.web_name, t.team_name, opp.team_name
ORDER BY carded_pct DESC LIMIT 25;


-- =====================================================================================
-- UPGRADE 15.7: team and match markets + referee forecasts
-- First in VS Code: python team_counts.py backtest, then python match_markets.py backtest
-- =====================================================================================

-- 15.7a  Storage: lines per market and side (every fixture has the same set)
SELECT market, side, COUNT(DISTINCT COALESCE(line::TEXT, outcome)) AS lines,
       COUNT(DISTINCT match_id) AS fixtures, COUNT(*) AS rows
FROM match_markets WHERE mode = 'backtest'
GROUP BY market, side ORDER BY market, side;

-- 15.7b  The test: Brier over every line, model vs baseline (lower = better), plus the average
--        predicted vs actual chance (should be close)
SELECT market, side, season, COUNT(*) AS predictions,
       ROUND(100 * AVG(hit)::NUMERIC, 1) AS actual_pct, ROUND(100 * AVG(prob)::NUMERIC, 1) AS predicted_pct,
       ROUND(AVG(POWER(prob - hit, 2))::NUMERIC, 4) AS brier,
       ROUND(AVG(POWER(base_prob - hit, 2))::NUMERIC, 4) AS baseline_brier
FROM match_markets
WHERE mode = 'backtest' AND hit IS NOT NULL AND season IN ('2024-25', '2025-26')
GROUP BY market, side, season ORDER BY market, side, season;

-- 15.7c  The common match lines, one by one: predicted vs actual chance of going over
SELECT market, line, season,
       ROUND(100 * AVG(prob)::NUMERIC, 1) AS predicted_pct, ROUND(100 * AVG(hit)::NUMERIC, 1) AS actual_pct,
       ROUND(AVG(POWER(prob - hit, 2))::NUMERIC, 4) AS brier,
       ROUND(AVG(POWER(base_prob - hit, 2))::NUMERIC, 4) AS baseline_brier
FROM match_markets
WHERE mode = 'backtest' AND side = 'match' AND hit IS NOT NULL AND season IN ('2024-25', '2025-26')
  AND ((market = 'corners' AND line IN (9.5, 10.5, 11.5)) OR (market = 'cards' AND line IN (3.5, 4.5, 5.5))
    OR (market = 'fouls' AND line IN (20.5, 22.5)) OR (market = 'booking_points' AND line IN (35.5, 45.5)))
GROUP BY market, line, season ORDER BY market, line, season;

-- 15.7d  Calibration for match cards over 3.5: games given 60-70% should go over about 65% of the time
SELECT LEAST(FLOOR(prob * 10), 9) * 10 || '-' || (LEAST(FLOOR(prob * 10), 9) + 1) * 10 || '%' AS chance_band,
       COUNT(*) AS games, ROUND(100 * AVG(prob)::NUMERIC, 1) AS predicted_pct, ROUND(100 * AVG(hit)::NUMERIC, 1) AS actual_pct
FROM match_markets
WHERE mode = 'backtest' AND market = 'cards' AND side = 'match' AND line = 3.5 AND hit IS NOT NULL
  AND season IN ('2024-25', '2025-26')
GROUP BY 1 ORDER BY MIN(prob);

-- 15.7e  The referee forecast for upcoming matches (after the weekly 'next' runs). One row per
--        fixture: the referee, what the model expects in that game, and his own per-game
--        averages next to the league's. 'referee TBC' = not appointed or not loaded yet.
CREATE OR REPLACE VIEW v_referee_forecast AS
WITH nx AS (
  SELECT * FROM match_markets
  WHERE mode = 'next' AND run_label = (SELECT run_label FROM match_markets WHERE mode = 'next'
                                       ORDER BY season DESC, gameweek DESC LIMIT 1)
),
f AS (
  SELECT run_label, match_id, home_team_id, away_team_id, MAX(referee) AS referee, MAX(ref_factor) AS foul_factor,
         MAX(expected) FILTER (WHERE market = 'fouls' AND side = 'match' AND line = 20.5)          AS exp_fouls,
         MAX(expected) FILTER (WHERE market = 'cards' AND side = 'match' AND line = 3.5)           AS exp_cards,
         MAX(expected) FILTER (WHERE market = 'booking_points' AND side = 'match' AND line = 35.5) AS exp_booking_points,
         MAX(prob) FILTER (WHERE market = 'cards' AND side = 'match' AND line = 3.5)  AS over_3_5_cards,
         MAX(prob) FILTER (WHERE market = 'cards' AND side = 'match' AND line = 4.5)  AS over_4_5_cards,
         MAX(prob) FILTER (WHERE market = 'fouls' AND side = 'match' AND line = 20.5) AS over_20_5_fouls,
         MAX(prob) FILTER (WHERE market = 'fouls' AND side = 'match' AND line = 22.5) AS over_22_5_fouls,
         MAX(prob) FILTER (WHERE market = 'booking_points' AND side = 'match' AND line = 35.5) AS over_35_5_bp,
         MAX(prob) FILTER (WHERE market = 'red' AND side = 'match') AS red_card
  FROM nx GROUP BY run_label, match_id, home_team_id, away_team_id
),
lgs AS (
  SELECT ROUND(SUM(yellows_pg * matches) / SUM(matches), 2) AS yellows_pg,
         ROUND(SUM(fouls_pg * matches) / SUM(matches), 1) AS fouls_pg,
         ROUND(SUM(booking_points_pg * matches) / SUM(matches), 1) AS booking_points_pg
  FROM v_referee_stats WHERE season = 'all'
)
SELECT f.run_label, ht.team_name AS home_team, at.team_name AS away_team,
       COALESCE(f.referee, 'referee TBC') AS referee, ROUND(f.foul_factor::NUMERIC, 2) AS referee_foul_factor,
       ROUND(f.exp_fouls::NUMERIC, 1) AS exp_fouls, ROUND(f.exp_cards::NUMERIC, 2) AS exp_cards,
       ROUND(f.exp_booking_points::NUMERIC, 0) AS exp_booking_points,
       ROUND(100 * f.over_3_5_cards::NUMERIC) AS over_3_5_cards_pct, ROUND(100 * f.over_4_5_cards::NUMERIC) AS over_4_5_cards_pct,
       ROUND(100 * f.over_20_5_fouls::NUMERIC) AS over_20_5_fouls_pct, ROUND(100 * f.over_22_5_fouls::NUMERIC) AS over_22_5_fouls_pct,
       ROUND(100 * f.over_35_5_bp::NUMERIC) AS over_35_5_bp_pct, ROUND(100 * f.red_card::NUMERIC) AS red_card_pct,
       r.matches AS ref_matches, r.yellows_pg AS ref_yellows_pg, r.reds_pg AS ref_reds_pg,
       r.booking_points_pg AS ref_booking_points_pg, r.fouls_pg AS ref_fouls_pg,
       lgs.yellows_pg AS league_yellows_pg, lgs.fouls_pg AS league_fouls_pg, lgs.booking_points_pg AS league_booking_points_pg
FROM f
JOIN teams ht ON ht.team_id = f.home_team_id
JOIN teams at ON at.team_id = f.away_team_id
LEFT JOIN v_referee_stats r ON r.season = 'all' AND r.referee = f.referee
CROSS JOIN lgs;

SELECT * FROM v_referee_forecast ORDER BY over_3_5_cards_pct DESC;

-- 15.7f  Referee forecasts tested: in 2024-25 and 2025-26, predicted vs actual cards and fouls
--        in each regular referee's matches (model should track the strict / lenient referees)
SELECT referee, COUNT(DISTINCT match_id) AS games,
       ROUND(AVG(expected) FILTER (WHERE market = 'cards' AND line = 3.5)::NUMERIC, 2) AS predicted_cards,
       ROUND(AVG(actual) FILTER (WHERE market = 'cards' AND line = 3.5)::NUMERIC, 2) AS actual_cards,
       ROUND(AVG(expected) FILTER (WHERE market = 'fouls' AND line = 20.5)::NUMERIC, 1) AS predicted_fouls,
       ROUND(AVG(actual) FILTER (WHERE market = 'fouls' AND line = 20.5)::NUMERIC, 1) AS actual_fouls
FROM match_markets
WHERE mode = 'backtest' AND side = 'match' AND referee IS NOT NULL AND season IN ('2024-25', '2025-26')
GROUP BY referee HAVING COUNT(DISTINCT match_id) >= 15
ORDER BY actual_cards DESC;


-- =====================================================================================
-- UPGRADE 15.8: slip checker (bet builders)
-- First in VS Code: python slip_checker.py test   (then: example / your own slip.csv)
-- =====================================================================================

-- 15.8a  The test: for three standard bet builders, actual hit rate vs the simulated (linked)
--        chance vs the single chances multiplied. Brier: lower = better; joint should win.
SELECT combo, season, COUNT(*) AS slips,
       ROUND(100 * AVG(hit)::NUMERIC, 1) AS actual_pct,
       ROUND(100 * AVG("joint")::NUMERIC, 1) AS joint_pct,
       ROUND(100 * AVG(product)::NUMERIC, 1) AS product_pct,
       ROUND(AVG(POWER("joint" - hit, 2))::NUMERIC, 4) AS brier_joint,
       ROUND(AVG(POWER(product - hit, 2))::NUMERIC, 4) AS brier_product
FROM slip_test GROUP BY combo, season ORDER BY combo, season;

-- 15.8b  Calibration of the linked chance across all test slips: slips given 20-30% should land
--        about 25% of the time
SELECT LEAST(FLOOR("joint" * 10), 9) * 10 || '-' || (LEAST(FLOOR("joint" * 10), 9) + 1) * 10 || '%' AS chance_band,
       COUNT(*) AS slips, ROUND(100 * AVG("joint")::NUMERIC, 1) AS predicted_pct,
       ROUND(100 * AVG(hit)::NUMERIC, 1) AS actual_pct
FROM slip_test GROUP BY 1 ORDER BY MIN("joint");

-- 15.8c  After 'python slip_checker.py': your slips, with the fair odds and the value at the site's price
SELECT run_label, slip, legs, ROUND(100 * joint_prob::NUMERIC, 1) AS chance_pct,
       ROUND(100 * product_of_singles::NUMERIC, 1) AS if_unlinked_pct,
       ROUND((1 / NULLIF(joint_prob, 0))::NUMERIC, 2) AS fair_odds, price,
       ROUND(100 * implied_prob::NUMERIC, 1) AS site_implied_pct, ROUND(100 * edge::NUMERIC, 1) AS edge_pct,
       ROUND(100 * all_named_play::NUMERIC) AS all_named_players_play_pct
FROM slip_results ORDER BY slip;
