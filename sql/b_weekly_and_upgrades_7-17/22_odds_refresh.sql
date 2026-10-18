-- =====================================================================
-- ODDS REFRESH (every run week, or every few weeks)
-- =====================================================================
TRUNCATE stg_odds;
-- import odds.csv -> stg_odds (csv, UTF8, Header on)

SELECT COUNT(*) FROM stg_odds;              -- expect: matches played so far

SELECT DISTINCT n AS unknown_name           -- expect 0 rows
FROM (SELECT TRIM(home_team) AS n FROM stg_odds
      UNION SELECT TRIM(away_team) FROM stg_odds) x
WHERE n NOT IN (SELECT alias FROM team_aliases);

INSERT INTO external_predictions (source, run_label, match_id, p_home, p_draw, p_away)
SELECT 'bookmaker', 'odds', m.match_id,
       ROUND((1 / o.h) / (1 / o.h + 1 / o.d + 1 / o.a), 4),
       ROUND((1 / o.d) / (1 / o.h + 1 / o.d + 1 / o.a), 4),
       ROUND((1 / o.a) / (1 / o.h + 1 / o.d + 1 / o.a), 4)
FROM (SELECT season, TRIM(home_team) AS home_team, TRIM(away_team) AS away_team,
             NULLIF(odds_h, '')::NUMERIC AS h,
             NULLIF(odds_d, '')::NUMERIC AS d,
             NULLIF(odds_a, '')::NUMERIC AS a
      FROM stg_odds) o
JOIN seasons s      ON s.label = o.season
JOIN team_aliases h ON h.alias = o.home_team
JOIN team_aliases a ON a.alias = o.away_team
JOIN matches m      ON m.season_id = s.season_id
                   AND m.home_team_id = h.team_id AND m.away_team_id = a.team_id
WHERE o.h IS NOT NULL AND o.d IS NOT NULL AND o.a IS NOT NULL
ON CONFLICT (source, run_label, match_id)
DO UPDATE SET p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away;
-- expect: INSERT 0 <matches with odds>
