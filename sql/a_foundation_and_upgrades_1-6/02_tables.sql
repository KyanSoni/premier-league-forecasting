CREATE TABLE teams (
  team_id   SERIAL PRIMARY KEY,
  team_name TEXT UNIQUE NOT NULL
);

CREATE TABLE team_aliases (
  alias   TEXT PRIMARY KEY,
  team_id INT REFERENCES teams(team_id)
);

CREATE TABLE seasons (
  season_id SERIAL PRIMARY KEY,
  label     TEXT UNIQUE NOT NULL
);

CREATE TABLE model_runs (
  run_id    SERIAL PRIMARY KEY,
  season_id INT REFERENCES seasons(season_id),
  run_label TEXT UNIQUE NOT NULL,
  run_date  DATE DEFAULT CURRENT_DATE
);

CREATE TABLE matches (
  match_id     SERIAL PRIMARY KEY,
  season_id    INT REFERENCES seasons(season_id),
  round_no     INT,
  match_date   DATE,
  venue        TEXT,
  home_team_id INT REFERENCES teams(team_id),
  away_team_id INT REFERENCES teams(team_id),
  home_goals   INT,
  away_goals   INT,
  UNIQUE (season_id, home_team_id, away_team_id)
);

CREATE TABLE team_ratings (
  run_id  INT REFERENCES model_runs(run_id),
  team_id INT REFERENCES teams(team_id),
  att NUMERIC, def NUMERIC,
  PRIMARY KEY (run_id, team_id)
);

CREATE TABLE sim_results (
  run_id         INT REFERENCES model_runs(run_id),
  sim_no         INT,
  team_id        INT REFERENCES teams(team_id),
  final_position INT,
  PRIMARY KEY (run_id, sim_no, team_id)
);

CREATE TABLE final_tables (
  season_id INT REFERENCES seasons(season_id),
  team_id   INT REFERENCES teams(team_id),
  position INT, played INT, won INT, drawn INT, lost INT,
  gf INT, ga INT, gd INT, pts INT,
  PRIMARY KEY (season_id, team_id)
);

CREATE TABLE promoted_history (
  team_id      INT REFERENCES teams(team_id),
  season_label TEXT,
  champ_att NUMERIC, champ_def NUMERIC,
  epl_att   NUMERIC, epl_def   NUMERIC
);