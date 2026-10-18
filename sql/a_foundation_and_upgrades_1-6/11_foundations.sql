-- ===== 1. New tables =====
CREATE TABLE IF NOT EXISTS model_variants (
  variant      TEXT PRIMARY KEY,
  description  TEXT,
  home_factor  NUMERIC NOT NULL DEFAULT 1,
  away_factor  NUMERIC NOT NULL DEFAULT 1,
  blend_k      NUMERIC,                    -- NULL = no blending
  xg_weight    NUMERIC NOT NULL DEFAULT 0, -- 0 = goals only, 1 = xG only
  promoted_reg BOOLEAN NOT NULL DEFAULT FALSE,
  adopted      BOOLEAN NOT NULL DEFAULT FALSE
);
INSERT INTO model_variants (variant, description, adopted)
VALUES ('base', 'Original model: run ratings, no home advantage', TRUE)
ON CONFLICT (variant) DO NOTHING;

CREATE TABLE IF NOT EXISTS match_xg (
  match_id INT PRIMARY KEY REFERENCES matches(match_id),
  xg_home NUMERIC, xg_away NUMERIC
);
CREATE TABLE IF NOT EXISTS promoted_inputs (
  season_label TEXT, team_id INT REFERENCES teams(team_id),
  champ_att NUMERIC, champ_def NUMERIC,
  PRIMARY KEY (season_label, team_id)
);
CREATE TABLE IF NOT EXISTS external_predictions (
  source TEXT, run_label TEXT, match_id INT REFERENCES matches(match_id),
  p_home NUMERIC, p_draw NUMERIC, p_away NUMERIC,
  PRIMARY KEY (source, run_label, match_id)
);
CREATE TABLE IF NOT EXISTS dc_sim_summary (
  run_label TEXT, team_name TEXT, avg_finish NUMERIC,
  title_pct NUMERIC, top4_pct NUMERIC, relegation_pct NUMERIC
);
CREATE TABLE IF NOT EXISTS dc_team_ratings (
  run_label TEXT, team_name TEXT, attack NUMERIC, defence NUMERIC,
  home_adv NUMERIC, rho NUMERIC, avg_goals NUMERIC
);

-- ===== 2. Remove views that are rebuilt below =====
DROP VIEW IF EXISTS v_vs_bookmaker, v_promoted_check,
                    v_variant_calibration, v_scores, v_scored_predictions, v_all_predictions,
                    v_calibration, v_brier, v_rps, v_match_predictions,
                    v_variant_predictions, v_variant_ratings, v_promoted_fit,
                    v_xg_ratings, v_team_games, v_run_avg;

-- ===== 3. Helper views =====
CREATE VIEW v_run_avg AS
SELECT r.run_id, r.run_label, r.season_id, r.season, r.gameweek,
       CASE WHEN r.gameweek = 0 THEN
            (SELECT SUM(f.gf)::NUMERIC / SUM(f.played)
             FROM final_tables f JOIN seasons s2 USING (season_id)
             WHERE s2.label < r.season
             GROUP BY s2.label ORDER BY s2.label DESC LIMIT 1)
       ELSE
            (SELECT SUM(m.home_goals + m.away_goals)::NUMERIC / (2 * COUNT(*))
             FROM matches m
             WHERE m.season_id = r.season_id AND m.round_no <= r.gameweek
               AND m.home_goals IS NOT NULL)
       END AS avg_goals
FROM v_runs r;

CREATE VIEW v_team_games AS
SELECT r.run_id, t.team_id, COUNT(m.match_id) AS games
FROM v_runs r
CROSS JOIN teams t
LEFT JOIN matches m
       ON m.season_id = r.season_id AND m.round_no <= r.gameweek
      AND m.home_goals IS NOT NULL
      AND (m.home_team_id = t.team_id OR m.away_team_id = t.team_id)
GROUP BY r.run_id, t.team_id;

CREATE VIEW v_xg_ratings AS
WITH tm AS (
  SELECT r.run_id, m.home_team_id AS team_id, x.xg_home AS xgf, x.xg_away AS xga
  FROM v_runs r
  JOIN matches m  ON m.season_id = r.season_id AND m.round_no <= r.gameweek AND m.home_goals IS NOT NULL
  JOIN match_xg x ON x.match_id = m.match_id
  UNION ALL
  SELECT r.run_id, m.away_team_id, x.xg_away, x.xg_home
  FROM v_runs r
  JOIN matches m  ON m.season_id = r.season_id AND m.round_no <= r.gameweek AND m.home_goals IS NOT NULL
  JOIN match_xg x ON x.match_id = m.match_id
),
lg AS (SELECT run_id, AVG(xgf) AS lg_avg FROM tm GROUP BY run_id)
SELECT tm.run_id, tm.team_id, COUNT(*) AS xg_games,
       1 + (AVG(tm.xgf) / lg.lg_avg - 1) * 0.75 AS att_xg,
       1 + (AVG(tm.xga) / lg.lg_avg - 1) * 0.75 AS def_xg
FROM tm JOIN lg USING (run_id)
GROUP BY tm.run_id, tm.team_id, lg.lg_avg;

CREATE VIEW v_promoted_fit AS
SELECT COUNT(*) AS clubs,
       REGR_SLOPE(epl_att, champ_att)     AS att_slope,
       REGR_INTERCEPT(epl_att, champ_att) AS att_intercept,
       CORR(epl_att, champ_att)           AS att_corr,
       REGR_SLOPE(epl_def, champ_def)     AS def_slope,
       REGR_INTERCEPT(epl_def, champ_def) AS def_intercept,
       CORR(epl_def, champ_def)           AS def_corr
FROM promoted_history;

-- ===== 4. Ratings for every run and every variant =====
CREATE VIEW v_variant_ratings AS
WITH base AS (
  SELECT v.variant, v.blend_k, v.xg_weight, v.promoted_reg,
         r.run_id, r.season, r.gameweek, tr.team_id,
         tr.att, tr.def,
         pre.att AS pre_att, pre.def AS pre_def,
         COALESCE(g.games, 0) AS games,
         xr.att_xg, xr.def_xg
  FROM model_variants v
  CROSS JOIN v_runs r
  JOIN team_ratings tr       ON tr.run_id = r.run_id
  LEFT JOIN v_runs rp        ON rp.season_id = r.season_id AND rp.gameweek = 0
  LEFT JOIN team_ratings pre ON pre.run_id = rp.run_id AND pre.team_id = tr.team_id
  LEFT JOIN v_team_games g   ON g.run_id = r.run_id AND g.team_id = tr.team_id
  LEFT JOIN v_xg_ratings xr  ON xr.run_id = r.run_id AND xr.team_id = tr.team_id
),
blended AS (
  SELECT b.*,
         CASE WHEN b.blend_k IS NOT NULL AND b.gameweek > 0 AND b.pre_att IS NOT NULL
              THEN (b.pre_att * b.blend_k + b.att * b.games) / (b.blend_k + b.games)
              ELSE b.att END AS att_b,
         CASE WHEN b.blend_k IS NOT NULL AND b.gameweek > 0 AND b.pre_def IS NOT NULL
              THEN (b.pre_def * b.blend_k + b.def * b.games) / (b.blend_k + b.games)
              ELSE b.def END AS def_b
  FROM base b
),
promoted AS (
  SELECT bl.*,
         CASE WHEN bl.promoted_reg AND bl.gameweek = 0 AND pi.team_id IS NOT NULL
              THEN (f.att_slope * pi.champ_att + f.att_intercept)::NUMERIC
              ELSE bl.att_b END AS att_p,
         CASE WHEN bl.promoted_reg AND bl.gameweek = 0 AND pi.team_id IS NOT NULL
              THEN (f.def_slope * pi.champ_def + f.def_intercept)::NUMERIC
              ELSE bl.def_b END AS def_p
  FROM blended bl
  LEFT JOIN promoted_inputs pi ON pi.season_label = bl.season AND pi.team_id = bl.team_id
  CROSS JOIN v_promoted_fit f
)
SELECT variant, run_id, team_id,
       CASE WHEN xg_weight > 0 AND att_xg IS NOT NULL
            THEN (1 - xg_weight) * att_p + xg_weight * att_xg ELSE att_p END AS att,
       CASE WHEN xg_weight > 0 AND def_xg IS NOT NULL
            THEN (1 - xg_weight) * def_p + xg_weight * def_xg ELSE def_p END AS def
FROM promoted;

-- ===== 5. Predictions for every fixture, run and variant =====
CREATE VIEW v_variant_predictions AS
WITH lambdas AS (
  SELECT vh.variant, ra.run_id, ra.run_label, ra.season, ra.gameweek,
         m.match_id, m.round_no, m.match_date,
         th.team_name AS home_team, ta.team_name AS away_team,
         m.home_goals, m.away_goals,
         vh.att * va.def * ra.avg_goals * v.home_factor AS xg_home,
         va.att * vh.def * ra.avg_goals * v.away_factor AS xg_away
  FROM v_run_avg ra
  JOIN matches m            ON m.season_id = ra.season_id
  JOIN v_variant_ratings vh ON vh.run_id = ra.run_id AND vh.team_id = m.home_team_id
  JOIN v_variant_ratings va ON va.run_id = ra.run_id AND va.team_id = m.away_team_id
                           AND va.variant = vh.variant
  JOIN model_variants v     ON v.variant = vh.variant
  JOIN teams th             ON th.team_id = m.home_team_id
  JOIN teams ta             ON ta.team_id = m.away_team_id
),
grid AS (
  SELECT l.variant, l.run_id, l.match_id, g.hg, g.ag,
         EXP(-l.xg_home::FLOAT8) * POWER(l.xg_home::FLOAT8, g.hg) / FACTORIAL(g.hg)::FLOAT8
       * EXP(-l.xg_away::FLOAT8) * POWER(l.xg_away::FLOAT8, g.ag) / FACTORIAL(g.ag)::FLOAT8 AS p
  FROM lambdas l
  CROSS JOIN (SELECT hg, ag FROM generate_series(0, 10) AS hg, generate_series(0, 10) AS ag) g
),
probs AS (
  SELECT variant, run_id, match_id,
         SUM(p) FILTER (WHERE hg > ag) / SUM(p) AS p_home,
         SUM(p) FILTER (WHERE hg = ag) / SUM(p) AS p_draw,
         SUM(p) FILTER (WHERE hg < ag) / SUM(p) AS p_away
  FROM grid
  GROUP BY variant, run_id, match_id
)
SELECT l.variant, l.season, l.run_label, l.gameweek AS run_gameweek,
       l.round_no, l.match_date, l.home_team, l.away_team,
       ROUND(l.xg_home, 2) AS xg_home, ROUND(l.xg_away, 2) AS xg_away,
       ROUND(p.p_home::NUMERIC, 4) AS p_home,
       ROUND(p.p_draw::NUMERIC, 4) AS p_draw,
       ROUND(p.p_away::NUMERIC, 4) AS p_away,
       l.home_goals, l.away_goals,
       CASE WHEN l.home_goals IS NULL THEN NULL
            WHEN l.home_goals > l.away_goals THEN 'H'
            WHEN l.home_goals = l.away_goals THEN 'D'
            ELSE 'A' END AS result,
       (l.round_no > l.gameweek) AS out_of_sample,
       l.run_id, l.match_id
FROM lambdas l
JOIN probs p ON p.variant = l.variant AND p.run_id = l.run_id AND p.match_id = l.match_id;

-- ===== 6. Your existing views, now following the adopted variant =====
CREATE VIEW v_match_predictions AS
SELECT season, run_label, run_gameweek, round_no, match_date, home_team, away_team,
       xg_home, xg_away, p_home, p_draw, p_away, home_goals, away_goals,
       result, out_of_sample, run_id, match_id
FROM v_variant_predictions
WHERE variant = COALESCE((SELECT variant FROM model_variants WHERE adopted ORDER BY variant LIMIT 1), 'base');

CREATE VIEW v_brier AS
WITH s AS (
  SELECT run_label, season, run_gameweek,
         COUNT(*) AS matches_tested,
         AVG(POWER(p_home - (result = 'H')::INT, 2)
           + POWER(p_draw - (result = 'D')::INT, 2)
           + POWER(p_away - (result = 'A')::INT, 2)) AS brier_model,
         1 - (POWER(AVG((result = 'H')::INT), 2)
            + POWER(AVG((result = 'D')::INT), 2)
            + POWER(AVG((result = 'A')::INT), 2)) AS brier_baseline,
         AVG(CASE WHEN (CASE WHEN p_home >= p_draw AND p_home >= p_away THEN 'H'
                             WHEN p_away >= p_draw THEN 'A'
                             ELSE 'D' END) = result
                  THEN 1.0 ELSE 0 END) AS pct_correct_pick
  FROM v_match_predictions
  WHERE result IS NOT NULL AND out_of_sample
  GROUP BY run_label, season, run_gameweek
)
SELECT run_label, season, run_gameweek, matches_tested,
       ROUND(brier_model, 4)    AS brier_model,
       ROUND(brier_baseline, 4) AS brier_baseline,
       ROUND(100 * (1 - brier_model / NULLIF(brier_baseline, 0)), 1) AS improvement_pct,
       ROUND(pct_correct_pick, 3) AS pct_correct_pick
FROM s;

CREATE VIEW v_calibration AS
SELECT run_label, season,
       FLOOR(p_home * 10) / 10 AS band_start,
       (FLOOR(p_home * 10) * 10)::INT || '-' || (FLOOR(p_home * 10) * 10 + 10)::INT || '%' AS band,
       COUNT(*) AS matches,
       ROUND(AVG(p_home), 3) AS avg_predicted,
       ROUND(AVG((result = 'H')::INT), 3) AS actual_rate
FROM v_match_predictions
WHERE result IS NOT NULL AND out_of_sample
GROUP BY run_label, season, FLOOR(p_home * 10);

-- ===== 7. Every model and external source, scored =====
CREATE VIEW v_all_predictions AS
SELECT variant AS model, season, run_label, run_gameweek, match_id, round_no,
       p_home, p_draw, p_away, result, out_of_sample
FROM v_variant_predictions
UNION ALL
SELECT e.source, s.label, e.run_label, r.gameweek, e.match_id, m.round_no,
       e.p_home, e.p_draw, e.p_away,
       CASE WHEN m.home_goals IS NULL THEN NULL
            WHEN m.home_goals > m.away_goals THEN 'H'
            WHEN m.home_goals = m.away_goals THEN 'D'
            ELSE 'A' END,
       COALESCE(m.round_no > r.gameweek, TRUE)
FROM external_predictions e
JOIN matches m     ON m.match_id = e.match_id
JOIN seasons s     ON s.season_id = m.season_id
LEFT JOIN v_runs r ON r.run_label = e.run_label;

CREATE VIEW v_scored_predictions AS
SELECT a.*,
       POWER(p_home - (result = 'H')::INT, 2)
     + POWER(p_draw - (result = 'D')::INT, 2)
     + POWER(p_away - (result = 'A')::INT, 2) AS brier,
       0.5 * (POWER(p_home - (result = 'H')::INT, 2)
            + POWER(p_home + p_draw - (result IN ('H', 'D'))::INT, 2)) AS rps,
       ((CASE WHEN p_home >= p_draw AND p_home >= p_away THEN 'H'
              WHEN p_away >= p_draw THEN 'A'
              ELSE 'D' END) = result) AS fav_correct
FROM v_all_predictions a
WHERE result IS NOT NULL AND out_of_sample;

CREATE VIEW v_scores AS
SELECT model, season, run_label,
       COUNT(*) AS matches,
       ROUND(AVG(brier), 4) AS brier,
       ROUND(AVG(rps), 4) AS rps,
       ROUND(AVG(fav_correct::INT), 3) AS pct_fav_correct
FROM v_scored_predictions
GROUP BY model, season, run_label;

CREATE VIEW v_variant_calibration AS
SELECT model, season, run_label,
       FLOOR(p_home * 10) / 10 AS band_start,
       (FLOOR(p_home * 10) * 10)::INT || '-' || (FLOOR(p_home * 10) * 10 + 10)::INT || '%' AS band,
       COUNT(*) AS matches,
       ROUND(AVG(p_home), 3) AS avg_predicted,
       ROUND(AVG((result = 'H')::INT), 3) AS actual_rate,
       ROUND(AVG((result = 'H')::INT) - AVG(p_home), 3) AS gap
FROM v_scored_predictions
GROUP BY model, season, run_label, FLOOR(p_home * 10);






SELECT s.run_label, s.brier AS new_brier, b.brier_model AS scorecard_brier, s.rps
FROM v_scores s
JOIN v_brier b ON b.run_label = s.run_label
WHERE s.model = 'base'
ORDER BY s.run_label;


CREATE OR REPLACE VIEW v_variant_calibration AS
WITH bands AS (
  SELECT b AS band_no,
         b / 10.0 AS band_start,
         (b * 10) || '-' || (b * 10 + 10) || '%' AS band
  FROM generate_series(0, 9) AS b
),
combos AS (
  SELECT DISTINCT model, season, run_label FROM v_scored_predictions
),
agg AS (
  SELECT model, run_label,
         FLOOR(p_home * 10)::INT AS band_no,
         COUNT(*) AS matches,
         ROUND(AVG(p_home), 3) AS avg_predicted,
         ROUND(AVG((result = 'H')::INT), 3) AS actual_rate,
         ROUND(AVG((result = 'H')::INT) - AVG(p_home), 3) AS gap
  FROM v_scored_predictions
  GROUP BY model, run_label, FLOOR(p_home * 10)
)
SELECT c.model, c.season, c.run_label, b.band_start, b.band,
       COALESCE(a.matches, 0) AS matches,
       a.avg_predicted, a.actual_rate,
       CASE WHEN a.matches >= 10 THEN a.gap END AS gap
FROM combos c
CROSS JOIN bands b
LEFT JOIN agg a ON a.model = c.model AND a.run_label = c.run_label AND a.band_no = b.band_no;



SELECT band, matches, gap
FROM v_variant_calibration
WHERE model = 'base' AND run_label = '2025-26 preseason'
ORDER BY band_start;