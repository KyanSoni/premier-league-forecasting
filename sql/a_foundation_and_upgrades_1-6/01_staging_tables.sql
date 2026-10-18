CREATE TABLE stg_results (
  season TEXT, round_no TEXT, match_date TEXT, venue TEXT,
  home_team TEXT, away_team TEXT, home_goals TEXT, away_goals TEXT
);

CREATE TABLE stg_sims (
  run_label TEXT, sim_no TEXT, team TEXT, position TEXT
);

CREATE TABLE stg_ratings (
  run_label TEXT, team TEXT, att TEXT, def TEXT
);

CREATE TABLE stg_final_tables (
  season TEXT, position TEXT, team TEXT, played TEXT, won TEXT,
  drawn TEXT, lost TEXT, gf TEXT, ga TEXT, gd TEXT, pts TEXT
);

CREATE TABLE stg_promoted (
  club TEXT, season TEXT, champ_att TEXT, champ_def TEXT,
  epl_att TEXT, epl_def TEXT
);