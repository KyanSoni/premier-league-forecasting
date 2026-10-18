-- =====================================================================
-- WEEKLY PART 1 (Stage C): load results.csv and xg.csv
-- Run each block on its own (highlight it, F5). No run label needed.
-- =====================================================================

-- C1  Empty staging
TRUNCATE stg_results, stg_xg;
-- expect: TRUNCATE TABLE

-- C2  Now import results.csv -> stg_results and xg.csv -> stg_xg
--     (right-click table > Import/Export Data > Import, csv, UTF8, Header on)

-- C3  What arrived
SELECT 'results' AS tbl, COUNT(*) FROM stg_results
UNION ALL SELECT 'xg', COUNT(*) FROM stg_xg;
-- expect: results 380 | xg = matches played so far (60 after GW6, 100 after GW10)

-- C4  Any team names the database doesn't know (expect 0 rows)
SELECT DISTINCT n AS unknown_name
FROM (SELECT TRIM(home_team) AS n FROM stg_results
      UNION SELECT TRIM(away_team) FROM stg_results
      UNION SELECT TRIM(home_team) FROM stg_xg
      UNION SELECT TRIM(away_team) FROM stg_xg) x
WHERE n NOT IN (SELECT alias FROM team_aliases);

-- C5  Update the scores
UPDATE matches m
SET home_goals = NULLIF(r.home_goals, '')::INT,
    away_goals = NULLIF(r.away_goals, '')::INT,
    match_date = r.match_date::DATE
FROM stg_results r
JOIN seasons s      ON s.label = r.season
JOIN team_aliases h ON h.alias = TRIM(r.home_team)
JOIN team_aliases a ON a.alias = TRIM(r.away_team)
WHERE m.season_id    = s.season_id
  AND m.home_team_id = h.team_id
  AND m.away_team_id = a.team_id;
-- expect: UPDATE 380

-- C6  Store the xG (adds new matches, overwrites any Understat revisions)
INSERT INTO match_xg (match_id, xg_home, xg_away)
SELECT m.match_id, NULLIF(x.xg_home, '')::NUMERIC, NULLIF(x.xg_away, '')::NUMERIC
FROM stg_xg x
JOIN seasons s      ON s.label = x.season
JOIN team_aliases h ON h.alias = TRIM(x.home_team)
JOIN team_aliases a ON a.alias = TRIM(x.away_team)
JOIN matches m      ON m.season_id = s.season_id
                   AND m.home_team_id = h.team_id AND m.away_team_id = a.team_id
WHERE NULLIF(x.xg_home, '') IS NOT NULL
ON CONFLICT (match_id)
DO UPDATE SET xg_home = EXCLUDED.xg_home, xg_away = EXCLUDED.xg_away;
-- expect: INSERT 0 <matches played so far>

-- C7  Every played match has xG (expect played = with_xg on both rows)
SELECT s.label, COUNT(*) AS played, COUNT(x.match_id) AS with_xg
FROM matches m
JOIN seasons s ON s.season_id = m.season_id
LEFT JOIN match_xg x ON x.match_id = m.match_id
WHERE m.home_goals IS NOT NULL
GROUP BY s.label ORDER BY s.label;

-- C8  Every team has played the same number of games (expect one number for all 20)
SELECT team_name, played
FROM v_league_table
WHERE season = '2026-27'
ORDER BY played, team_name;
