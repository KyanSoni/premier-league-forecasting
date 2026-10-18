INSERT INTO seasons (label)
SELECT season FROM stg_results
UNION
SELECT season FROM stg_final_tables;

INSERT INTO model_runs (season_id, run_label)
SELECT s.season_id, r.run_label
FROM (SELECT DISTINCT run_label FROM stg_sims) r
JOIN seasons s ON s.label = LEFT(r.run_label, 7);

INSERT INTO matches (season_id, round_no, match_date, venue,
                     home_team_id, away_team_id, home_goals, away_goals)
SELECT s.season_id,
       r.round_no::INT,
       r.match_date::DATE,
       r.venue,
       h.team_id,
       a.team_id,
       NULLIF(r.home_goals, '')::INT,
       NULLIF(r.away_goals, '')::INT
FROM stg_results r
JOIN seasons s      ON s.label = r.season
JOIN team_aliases h ON h.alias = TRIM(r.home_team)
JOIN team_aliases a ON a.alias = TRIM(r.away_team);

INSERT INTO team_ratings (run_id, team_id, att, def)
SELECT m.run_id, a.team_id, r.att::NUMERIC, r.def::NUMERIC
FROM stg_ratings r
JOIN model_runs m   ON m.run_label = r.run_label
JOIN team_aliases a ON a.alias = TRIM(r.team);

INSERT INTO sim_results (run_id, sim_no, team_id, final_position)
SELECT m.run_id, s.sim_no::INT, a.team_id, s.position::INT
FROM stg_sims s
JOIN model_runs m   ON m.run_label = s.run_label
JOIN team_aliases a ON a.alias = TRIM(s.team);

INSERT INTO final_tables
SELECT s.season_id, a.team_id,
       f.position::INT, f.played::INT, f.won::INT, f.drawn::INT, f.lost::INT,
       f.gf::INT, f.ga::INT, f.gd::INT, f.pts::INT
FROM stg_final_tables f
JOIN seasons s      ON s.label = f.season
JOIN team_aliases a ON a.alias = TRIM(f.team);

INSERT INTO promoted_history
SELECT a.team_id, p.season,
       p.champ_att::NUMERIC, p.champ_def::NUMERIC,
       p.epl_att::NUMERIC,   p.epl_def::NUMERIC
FROM stg_promoted p
JOIN team_aliases a ON a.alias = TRIM(p.club);
