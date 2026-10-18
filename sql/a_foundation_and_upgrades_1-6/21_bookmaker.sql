CREATE TABLE IF NOT EXISTS stg_odds (
  season TEXT, home_team TEXT, away_team TEXT,
  odds_h TEXT, odds_d TEXT, odds_a TEXT
);
TRUNCATE stg_odds;




SELECT season, COUNT(*) FROM stg_odds GROUP BY season ORDER BY season;



SELECT DISTINCT n AS unknown_name
FROM (SELECT TRIM(home_team) AS n FROM stg_odds
      UNION SELECT TRIM(away_team) FROM stg_odds) x
WHERE n NOT IN (SELECT alias FROM team_aliases);



INSERT INTO team_aliases (alias, team_id)
SELECT 'Man United', team_id FROM teams WHERE team_name = 'Manchester United';
INSERT INTO team_aliases (alias, team_id)
SELECT 'Hull', team_id FROM teams WHERE team_name = 'Hull City';






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





SELECT o.season, o.home_team, o.away_team
FROM stg_odds o
JOIN seasons s ON s.label = o.season
LEFT JOIN team_aliases h ON h.alias = TRIM(o.home_team)
LEFT JOIN team_aliases a ON a.alias = TRIM(o.away_team)
LEFT JOIN matches m ON m.season_id = s.season_id
                   AND m.home_team_id = h.team_id AND m.away_team_id = a.team_id
WHERE m.match_id IS NULL;






SELECT th.team_name AS home, ta.team_name AS away, e.p_home, e.p_draw, e.p_away
FROM external_predictions e
JOIN matches m ON m.match_id = e.match_id
JOIN teams th ON th.team_id = m.home_team_id
JOIN teams ta ON ta.team_id = m.away_team_id
JOIN seasons s ON s.season_id = m.season_id
WHERE e.source = 'bookmaker' AND s.label = '2025-26'
  AND th.team_name = 'Liverpool' AND ta.team_name = 'Bournemouth';





  CREATE OR REPLACE VIEW v_vs_bookmaker AS
SELECT sp.model, sp.season, sp.run_label,
       COUNT(*) AS matches,
       ROUND(AVG(sp.brier), 4) AS model_brier,
       ROUND(AVG(bk.brier), 4) AS bookmaker_brier,
       ROUND(AVG(sp.rps), 4)   AS model_rps,
       ROUND(AVG(bk.rps), 4)   AS bookmaker_rps
FROM v_scored_predictions sp
JOIN v_scored_predictions bk ON bk.match_id = sp.match_id AND bk.model = 'bookmaker'
WHERE sp.model <> 'bookmaker'
GROUP BY sp.model, sp.season, sp.run_label;

SELECT * FROM v_vs_bookmaker ORDER BY run_label, model_rps;