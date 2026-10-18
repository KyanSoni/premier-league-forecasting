-- =====================================================================
-- UPGRADE 13: squad market value as a pre-season prior
-- Run each block on its own.
-- =====================================================================

-- 13.2  Template for squad_values.csv: one row per club per season, using your database's names.
--       Run it, then click the "Save results to file" (download) button above the results to get a CSV,
--       and fill in value_m. Expect 80 rows (20 clubs x 4 seasons).
SELECT DISTINCT s.label AS season, t.team_name AS team, NULL::NUMERIC AS value_m
FROM matches m
JOIN seasons s ON s.season_id = m.season_id
JOIN teams t   ON t.team_id = m.home_team_id
WHERE s.label IN ('2023-24', '2024-25', '2025-26', '2026-27')
ORDER BY 1, 2;

-- 13.3a  Tables
CREATE TABLE IF NOT EXISTS stg_squad_values (season TEXT, team TEXT, value_m TEXT);
CREATE TABLE IF NOT EXISTS squad_values (
  season_label TEXT, team_id INT REFERENCES teams(team_id), value_m NUMERIC,
  PRIMARY KEY (season_label, team_id)
);
TRUNCATE stg_squad_values;
-- now import squad_values.csv -> stg_squad_values (csv, UTF8, Header on)

-- 13.3b  Unknown names (expect 0 rows; add aliases as in Upgrade 7 if not)
SELECT DISTINCT TRIM(team) FROM stg_squad_values
WHERE TRIM(team) NOT IN (SELECT alias FROM team_aliases UNION SELECT team_name FROM teams);

-- 13.3c  Store them (expect INSERT 0 80 for four seasons)
INSERT INTO squad_values (season_label, team_id, value_m)
SELECT v.season, a.team_id, v.value_m::NUMERIC
FROM stg_squad_values v
JOIN (SELECT alias AS name, team_id FROM team_aliases
      UNION SELECT team_name, team_id FROM teams) a ON a.name = TRIM(v.team)
ON CONFLICT (season_label, team_id) DO UPDATE SET value_m = EXCLUDED.value_m;

SELECT season_label, COUNT(*), ROUND(AVG(value_m)) AS avg_value_m
FROM squad_values GROUP BY season_label ORDER BY 1;     -- expect 20 per season

-- 13.4  Value-implied ratings.
--   value_share = ln(club value / that season's average club value)
--   end-of-season rating (25% reverted, like your pre-season ratings) is regressed on value_share,
--   using ONLY earlier seasons, so a season's prior never sees its own results.
CREATE OR REPLACE VIEW v_value_ratings AS
WITH vs AS (
  SELECT sv.season_label AS season, sv.team_id,
         LN(sv.value_m / AVG(sv.value_m) OVER (PARTITION BY sv.season_label)) AS value_share
  FROM squad_values sv
),
fin AS (
  SELECT s.label AS season, f.team_id,
         1 + ((f.gf::NUMERIC / f.played) / ((SUM(f.gf) OVER w)::NUMERIC / SUM(f.played) OVER w) - 1) * 0.75 AS att_end,
         1 + ((f.ga::NUMERIC / f.played) / ((SUM(f.gf) OVER w)::NUMERIC / SUM(f.played) OVER w) - 1) * 0.75 AS def_end
  FROM final_tables f JOIN seasons s USING (season_id)
  WINDOW w AS (PARTITION BY f.season_id)
),
pairs AS (SELECT vs.season, vs.value_share, fin.att_end, fin.def_end
          FROM vs JOIN fin USING (season, team_id)),
fit AS (
  SELECT t.season,
         REGR_SLOPE(p.att_end, p.value_share)     AS att_slope,
         REGR_INTERCEPT(p.att_end, p.value_share) AS att_int,
         REGR_SLOPE(p.def_end, p.value_share)     AS def_slope,
         REGR_INTERCEPT(p.def_end, p.value_share) AS def_int,
         REGR_COUNT(p.att_end, p.value_share)     AS clubs
  FROM (SELECT DISTINCT season FROM vs) t
  JOIN pairs p ON p.season < t.season
  GROUP BY t.season
)
SELECT vs.season, vs.team_id, vs.value_share, fit.clubs,
       (fit.att_slope * vs.value_share + fit.att_int)::NUMERIC AS att_v,
       (fit.def_slope * vs.value_share + fit.def_int)::NUMERIC AS def_v
FROM vs JOIN fit USING (season);

-- 13.4b  Check: richer clubs get higher attack and lower defence ratings
SELECT v.season, t.team_name, ROUND(v.value_share, 2) AS value_share,
       ROUND(v.att_v, 3) AS att_v, ROUND(v.def_v, 3) AS def_v, v.clubs
FROM v_value_ratings v JOIN teams t USING (team_id)
WHERE v.season = '2025-26' ORDER BY v.att_v DESC;

-- 13.5-  SAFETY FIRST: copy the result of this into a text file called v_variant_ratings_backup.sql
SELECT pg_get_viewdef('v_variant_ratings', true);

-- 13.5a  A value_weight setting on every variant (0 = not used)
ALTER TABLE model_variants ADD COLUMN IF NOT EXISTS value_weight NUMERIC NOT NULL DEFAULT 0;

-- 13.5b  Rebuild v_variant_ratings so a variant can mix the value prior into the pre-season rating.
--        Built from YOUR live definition (checked 13 Oct): only the 4 marked lines in "base"
--        and the extra LEFT JOIN to v_value_ratings are new. Everything else is unchanged.
CREATE OR REPLACE VIEW v_variant_ratings AS
WITH base AS (
         SELECT v.variant,
            v.blend_k,
            v.xg_weight,
            v.promoted_reg,
            r.run_id,
            r.season,
            r.gameweek,
            tr.team_id,
            CASE WHEN r.gameweek = 0 AND v.value_weight > 0 AND vv.att_v IS NOT NULL                    -- NEW
                 THEN (1 - v.value_weight) * tr.att + v.value_weight * vv.att_v ELSE tr.att END AS att,
            CASE WHEN r.gameweek = 0 AND v.value_weight > 0 AND vv.def_v IS NOT NULL                    -- NEW
                 THEN (1 - v.value_weight) * tr.def + v.value_weight * vv.def_v ELSE tr.def END AS def,
            CASE WHEN v.value_weight > 0 AND vv.att_v IS NOT NULL AND pre.att IS NOT NULL               -- NEW
                 THEN (1 - v.value_weight) * pre.att + v.value_weight * vv.att_v ELSE pre.att END AS pre_att,
            CASE WHEN v.value_weight > 0 AND vv.def_v IS NOT NULL AND pre.def IS NOT NULL               -- NEW
                 THEN (1 - v.value_weight) * pre.def + v.value_weight * vv.def_v ELSE pre.def END AS pre_def,
            COALESCE(g.games, 0::bigint) AS games,
            xr.att_xg,
            xr.def_xg
           FROM model_variants v
             CROSS JOIN v_runs r
             JOIN team_ratings tr ON tr.run_id = r.run_id
             LEFT JOIN v_runs rp ON rp.season_id = r.season_id AND rp.gameweek = 0
             LEFT JOIN team_ratings pre ON pre.run_id = rp.run_id AND pre.team_id = tr.team_id
             LEFT JOIN v_team_games g ON g.run_id = r.run_id AND g.team_id = tr.team_id
             LEFT JOIN v_xg_ratings xr ON xr.run_id = r.run_id AND xr.team_id = tr.team_id
             LEFT JOIN v_value_ratings vv ON vv.season = r.season AND vv.team_id = tr.team_id   -- NEW
        ), blended AS (
         SELECT b.variant, b.blend_k, b.xg_weight, b.promoted_reg, b.run_id, b.season, b.gameweek, b.team_id,
            b.att, b.def, b.pre_att, b.pre_def, b.games, b.att_xg, b.def_xg,
                CASE
                    WHEN b.blend_k IS NOT NULL AND b.gameweek > 0 AND b.pre_att IS NOT NULL THEN (b.pre_att * b.blend_k + b.att * b.games::numeric) / (b.blend_k + b.games::numeric)
                    ELSE b.att
                END AS att_b,
                CASE
                    WHEN b.blend_k IS NOT NULL AND b.gameweek > 0 AND b.pre_def IS NOT NULL THEN (b.pre_def * b.blend_k + b.def * b.games::numeric) / (b.blend_k + b.games::numeric)
                    ELSE b.def
                END AS def_b
           FROM base b
        ), promoted AS (
         SELECT bl.variant, bl.blend_k, bl.xg_weight, bl.promoted_reg, bl.run_id, bl.season, bl.gameweek, bl.team_id,
            bl.att, bl.def, bl.pre_att, bl.pre_def, bl.games, bl.att_xg, bl.def_xg, bl.att_b, bl.def_b,
                CASE
                    WHEN bl.promoted_reg AND bl.gameweek = 0 AND pi.team_id IS NOT NULL THEN (f.att_slope * pi.champ_att::double precision + f.att_intercept)::numeric
                    ELSE bl.att_b
                END AS att_p,
                CASE
                    WHEN bl.promoted_reg AND bl.gameweek = 0 AND pi.team_id IS NOT NULL THEN (f.def_slope * pi.champ_def::double precision + f.def_intercept)::numeric
                    ELSE bl.def_b
                END AS def_p
           FROM blended bl
             LEFT JOIN promoted_inputs pi ON pi.season_label = bl.season AND pi.team_id = bl.team_id
             CROSS JOIN v_promoted_fit f
        )
 SELECT variant,
    run_id,
    team_id,
        CASE
            WHEN xg_weight > 0::numeric AND att_xg IS NOT NULL THEN (1::numeric - xg_weight) * att_p + xg_weight * att_xg
            ELSE att_p
        END AS att,
        CASE
            WHEN xg_weight > 0::numeric AND def_xg IS NOT NULL THEN (1::numeric - xg_weight) * def_p + xg_weight * def_xg
            ELSE def_p
        END AS def
   FROM promoted;

-- 13.5c  Check nothing changed for existing variants (expect xg_50 0.2119 and blend_15 0.2156, as before)
SELECT model, SUM(matches) AS matches, ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS rps
FROM v_scores
WHERE season = '2025-26' AND run_label LIKE '%after GW%' AND model IN ('xg_50', 'blend_15')
GROUP BY model;

-- 13.6  Two test variants on top of xg_50 (expect INSERT 0 2)
INSERT INTO model_variants (variant, description, home_factor, away_factor, blend_k, xg_weight, value_weight)
SELECT 'value_' || (w.w * 100)::INT, 'xg_50 + ' || (w.w * 100)::INT || '% squad-value prior',
       v.home_factor, v.away_factor, v.blend_k, v.xg_weight, w.w
FROM model_variants v CROSS JOIN (VALUES (0.33), (0.5)) AS w(w)
WHERE v.variant = 'xg_50';

-- 13.7  Where the prior should matter: pre-season and early runs, each season separately
SELECT season,
       CASE WHEN run_label LIKE '%preseason%' THEN '1 preseason'
            WHEN SUBSTRING(run_label FROM 'GW([0-9]+)')::INT <= 10 THEN '2 GW3-10'
            ELSE '3 GW15+' END AS stage,
       model, SUM(matches) AS matches,
       ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS rps
FROM v_scores
WHERE season IN ('2024-25', '2025-26') AND model IN ('xg_50', 'value_33', 'value_50')
GROUP BY season, 2, model ORDER BY season, stage, rps;

-- 13.8  Overall, both seasons (all runs including preseason)
SELECT model,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2024-25') / SUM(matches) FILTER (WHERE season = '2024-25'))::NUMERIC, 4) AS rps_2024_25,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2025-26') / SUM(matches) FILTER (WHERE season = '2025-26'))::NUMERIC, 4) AS rps_2025_26,
       ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS rps_both,
       SUM(matches) AS matches
FROM v_scores
WHERE season IN ('2024-25', '2025-26') AND model IN ('xg_50', 'value_33', 'value_50')
GROUP BY model ORDER BY rps_both;
