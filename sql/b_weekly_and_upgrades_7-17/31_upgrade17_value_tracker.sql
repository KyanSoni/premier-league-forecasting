-- =====================================================================
-- 31  Upgrade 17: upcoming odds and the value tracker
-- ---------------------------------------------------------------------
-- Reference copy. weekly.py creates these objects itself
-- (`python weekly.py odds` and `part1`), so you never need to run this
-- file by hand. It is here so the SQL side of the project can be read
-- in one place.
--
--   next_odds        bookmaker prices for unplayed games (football-data fixtures.csv)
--   value_log        every next-gameweek price with model chance, bookie fair chance,
--                    50/50 blended chance, edge and flag; closing odds + result added later
--   v_next_odds      read by the dashboard's H2H "Value vs bookmaker" section
--   v_value_log      one row per logged price, with closing-line value (CLV) and 1-unit profit
--   v_value_summary  VALUE / small / no / all: beat-the-close %, hit rate, ROI
-- =====================================================================

CREATE TABLE IF NOT EXISTS next_odds (
    match_id      INT PRIMARY KEY,
    home_team_id  INT,
    away_team_id  INT,
    kickoff       TEXT,
    avg_h         NUMERIC,
    avg_d         NUMERIC,
    avg_a         NUMERIC,
    avg_over25    NUMERIC,
    avg_under25   NUMERIC,
    b365_h        NUMERIC,
    b365_d        NUMERIC,
    b365_a        NUMERIC,
    b365_over25   NUMERIC,
    b365_under25  NUMERIC,
    max_h         NUMERIC,
    max_d         NUMERIC,
    max_a         NUMERIC,
    max_over25    NUMERIC,
    max_under25   NUMERIC,
    downloaded_at TIMESTAMP DEFAULT now()
);

CREATE OR REPLACE VIEW v_next_odds AS
    SELECT ht.team_name || ' v ' || at.team_name AS fixture, ht.team_name AS home_team,
           at.team_name AS away_team, m.round_no AS gameweek, o.kickoff,
           o.avg_h, o.avg_d, o.avg_a, o.avg_over25, o.avg_under25,
           o.b365_h, o.b365_d, o.b365_a, o.b365_over25, o.b365_under25,
           o.max_h, o.max_d, o.max_a, o.max_over25, o.max_under25, o.downloaded_at
    FROM next_odds o
    JOIN matches m ON m.match_id = o.match_id
    JOIN teams ht ON ht.team_id = o.home_team_id
    JOIN teams at ON at.team_id = o.away_team_id
    WHERE m.home_goals IS NULL;

-- flag: VALUE when blended chance x odds - 1 >= +5%, small when >= 0, otherwise no
-- blended chance = 0.5 x model + 0.5 x bookmaker fair chance (margin removed)
CREATE TABLE IF NOT EXISTS value_log (
    match_id          INT,
    market            TEXT,        -- home, draw, away, over25, under25
    run_label         TEXT,
    source            TEXT,        -- b365, avg or max
    model_prob        NUMERIC,
    fair_prob         NUMERIC,
    blended_prob      NUMERIC,
    taken_odds        NUMERIC,
    edge              NUMERIC,
    flag              TEXT,
    logged_at         TIMESTAMP DEFAULT now(),
    closing_odds      NUMERIC,
    closing_fair_prob NUMERIC,
    won               BOOLEAN,
    settled_at        TIMESTAMP,
    PRIMARY KEY (match_id, market)
);

CREATE OR REPLACE VIEW v_value_log AS
SELECT v.run_label, m.round_no AS gameweek, ht.team_name || ' v ' || at.team_name AS fixture,
       CASE v.market WHEN 'home' THEN ht.team_name || ' win' WHEN 'away' THEN at.team_name || ' win'
            WHEN 'draw' THEN 'Draw' WHEN 'over25' THEN 'Over 2.5 goals' ELSE 'Under 2.5 goals' END AS bet,
       v.source, v.flag,
       ROUND(100 * v.model_prob, 1) AS model_pct, ROUND(100 * v.fair_prob, 1) AS bookie_fair_pct,
       ROUND(100 * v.blended_prob, 1) AS blended_pct, v.taken_odds, ROUND(100 * v.edge, 1) AS edge_pct,
       v.closing_odds,
       ROUND(100 * (v.taken_odds / NULLIF(v.closing_odds, 0) - 1), 1) AS clv_pct,
       v.won,
       CASE WHEN v.won IS NULL THEN NULL WHEN v.won THEN v.taken_odds - 1 ELSE -1 END AS profit_1u,
       v.logged_at, v.settled_at
FROM value_log v
JOIN matches m ON m.match_id = v.match_id
JOIN teams ht ON ht.team_id = m.home_team_id
JOIN teams at ON at.team_id = m.away_team_id;

CREATE OR REPLACE VIEW v_value_summary AS
SELECT g.grp AS flag_group,
       COUNT(*) AS prices_logged,
       COUNT(*) FILTER (WHERE v.settled_at IS NOT NULL) AS settled,
       ROUND(AVG(100 * v.edge), 1) AS avg_edge_pct,
       ROUND(AVG(100 * (v.taken_odds / NULLIF(v.closing_odds, 0) - 1))
             FILTER (WHERE v.closing_odds IS NOT NULL), 1) AS avg_clv_pct,
       ROUND(100.0 * AVG((v.taken_odds > v.closing_odds)::INT)
             FILTER (WHERE v.closing_odds IS NOT NULL), 1) AS beat_close_pct,
       ROUND(100.0 * AVG(v.won::INT) FILTER (WHERE v.won IS NOT NULL), 1) AS won_pct,
       ROUND(100.0 * AVG(1 / v.taken_odds) FILTER (WHERE v.won IS NOT NULL), 1) AS odds_implied_pct,
       ROUND(SUM(CASE WHEN v.won THEN v.taken_odds - 1 WHEN NOT v.won THEN -1 END), 2) AS profit_1u,
       ROUND(100 * SUM(CASE WHEN v.won THEN v.taken_odds - 1 WHEN NOT v.won THEN -1 END)
             / NULLIF(COUNT(*) FILTER (WHERE v.won IS NOT NULL), 0), 1) AS roi_pct
FROM value_log v
CROSS JOIN LATERAL (VALUES (v.flag), ('All prices')) g(grp)
GROUP BY g.grp
ORDER BY CASE g.grp WHEN 'VALUE' THEN 1 WHEN 'small' THEN 2 WHEN 'no' THEN 3 ELSE 4 END;
