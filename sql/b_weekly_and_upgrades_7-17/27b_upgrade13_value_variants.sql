-- =====================================================================
-- UPGRADE 13b: squad value, second attempt (4 versions fixed in advance)
--   value_15, value_20       : smaller mix for every club
--   value_prom_50/100        : value used ONLY for newly promoted clubs
-- Run each block on its own. Undo file: v_variant_ratings_backup.sql still works.
-- =====================================================================

-- 13b.1  New setting: use the value prior for promoted clubs only (false = all clubs, as before)
ALTER TABLE model_variants ADD COLUMN IF NOT EXISTS value_promoted_only BOOLEAN NOT NULL DEFAULT false;

-- 13b.2  v_value_ratings gains a "promoted" column (club not in last season's final table).
--        Same as before plus that one column at the end.
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
       (fit.def_slope * vs.value_share + fit.def_int)::NUMERIC AS def_v,
       NOT EXISTS (
         SELECT 1 FROM final_tables f2 JOIN seasons s2 ON s2.season_id = f2.season_id
         WHERE f2.team_id = vs.team_id
           AND s2.label = (LEFT(vs.season, 4)::INT - 1)::TEXT || '-' || LPAD(((RIGHT(vs.season, 2)::INT + 99) % 100)::TEXT, 2, '0')
       ) AS promoted
FROM vs JOIN fit USING (season);

-- 13b.3  Check: 3 promoted clubs per season (expect 2024-25 Ipswich, Leicester, Southampton;
--        2025-26 Burnley, Leeds, Sunderland; 2026-27 Coventry, Hull, Ipswich)
SELECT v.season, t.team_name, ROUND(v.att_v, 3) AS att_v, ROUND(v.def_v, 3) AS def_v
FROM v_value_ratings v JOIN teams t USING (team_id)
WHERE v.promoted ORDER BY 1, 2;

-- 13b.4  Ratings view: same as now, plus "AND (NOT v.value_promoted_only OR vv.promoted)" in the 4 value lines
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
            CASE WHEN r.gameweek = 0 AND v.value_weight > 0 AND vv.att_v IS NOT NULL AND (NOT v.value_promoted_only OR vv.promoted)
                 THEN (1 - v.value_weight) * tr.att + v.value_weight * vv.att_v ELSE tr.att END AS att,
            CASE WHEN r.gameweek = 0 AND v.value_weight > 0 AND vv.def_v IS NOT NULL AND (NOT v.value_promoted_only OR vv.promoted)
                 THEN (1 - v.value_weight) * tr.def + v.value_weight * vv.def_v ELSE tr.def END AS def,
            CASE WHEN v.value_weight > 0 AND vv.att_v IS NOT NULL AND pre.att IS NOT NULL AND (NOT v.value_promoted_only OR vv.promoted)
                 THEN (1 - v.value_weight) * pre.att + v.value_weight * vv.att_v ELSE pre.att END AS pre_att,
            CASE WHEN v.value_weight > 0 AND vv.def_v IS NOT NULL AND pre.def IS NOT NULL AND (NOT v.value_promoted_only OR vv.promoted)
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
             LEFT JOIN v_value_ratings vv ON vv.season = r.season AND vv.team_id = tr.team_id
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

-- 13b.5  The four versions (expect INSERT 0 4)
INSERT INTO model_variants (variant, description, home_factor, away_factor, blend_k, xg_weight, value_weight, value_promoted_only)
SELECT x.name, x.descr, v.home_factor, v.away_factor, v.blend_k, v.xg_weight, x.w, x.prom
FROM model_variants v
CROSS JOIN (VALUES ('value_15',       'xg_50 + 15% squad-value prior, all clubs',       0.15, false),
                   ('value_20',       'xg_50 + 20% squad-value prior, all clubs',       0.20, false),
                   ('value_prom_50',  'xg_50 + 50% squad-value prior, promoted clubs',  0.50, true),
                   ('value_prom_100', 'xg_50 + squad-value rating for promoted clubs',  1.00, true)) AS x(name, descr, w, prom)
WHERE v.variant = 'xg_50';

-- 13b.6  One query for the decision: preseason, GW3-10 and all runs, both seasons (about 5 minutes)
SELECT model,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2024-25' AND run_label LIKE '%preseason%')
            / SUM(matches) FILTER (WHERE season = '2024-25' AND run_label LIKE '%preseason%'))::NUMERIC, 4) AS pre_24_25,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2025-26' AND run_label LIKE '%preseason%')
            / SUM(matches) FILTER (WHERE season = '2025-26' AND run_label LIKE '%preseason%'))::NUMERIC, 4) AS pre_25_26,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2024-25') / SUM(matches) FILTER (WHERE season = '2024-25'))::NUMERIC, 4) AS all_24_25,
       ROUND((SUM(rps * matches) FILTER (WHERE season = '2025-26') / SUM(matches) FILTER (WHERE season = '2025-26'))::NUMERIC, 4) AS all_25_26,
       ROUND((SUM(rps * matches) / SUM(matches))::NUMERIC, 4) AS all_both
FROM v_scores
WHERE season IN ('2024-25', '2025-26')
  AND model IN ('xg_50', 'value_15', 'value_20', 'value_33', 'value_50', 'value_prom_50', 'value_prom_100')
GROUP BY model ORDER BY all_both;
