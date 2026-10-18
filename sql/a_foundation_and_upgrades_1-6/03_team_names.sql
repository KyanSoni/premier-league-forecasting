INSERT INTO teams (team_name) VALUES
 ('Manchester City'),('Manchester United'),('Tottenham'),('Nottingham Forest'),
 ('Newcastle United'),('Leeds United'),('Ipswich Town'),('Coventry City'),
 ('Bournemouth'),('Middlesbrough'),('Leicester City');

INSERT INTO team_aliases (alias, team_id)
SELECT v.alias, t.team_id
FROM (VALUES
 ('Man City','Manchester City'), ('Manchester City','Manchester City'),
 ('Man Utd','Manchester United'), ('Manchester Utd','Manchester United'), ('Manchester United','Manchester United'),
 ('Spurs','Tottenham'), ('Tottenham','Tottenham'),
 ('Nott''m Forest','Nottingham Forest'), ('Nott''ham Forest','Nottingham Forest'), ('Nottingham Forest','Nottingham Forest'),
 ('Newcastle','Newcastle United'), ('Newcastle Utd','Newcastle United'), ('Newcastle United','Newcastle United'),
 ('Leeds','Leeds United'), ('Leeds United','Leeds United'),
 ('Ipswich','Ipswich Town'), ('Ipswich Town','Ipswich Town'),
 ('Coventry','Coventry City'), ('Coventry City','Coventry City'),
 ('Bournemouth','Bournemouth'), ('Bournmouth','Bournemouth'),
 ('Middlesbrough','Middlesbrough'), ('Middlesborough','Middlesbrough'),
 ('Leicester','Leicester City'), ('Leicester City','Leicester City')
) AS v(alias, canonical)
JOIN teams t ON t.team_name = v.canonical;

WITH all_names AS (
  SELECT TRIM(home_team) AS name FROM stg_results
  UNION SELECT TRIM(away_team) FROM stg_results
  UNION SELECT TRIM(team)      FROM stg_sims
  UNION SELECT TRIM(team)      FROM stg_ratings
  UNION SELECT TRIM(team)      FROM stg_final_tables
  UNION SELECT TRIM(club)      FROM stg_promoted
)
INSERT INTO teams (team_name)
SELECT name FROM all_names
WHERE name NOT IN (SELECT alias FROM team_aliases);

INSERT INTO team_aliases (alias, team_id)
SELECT team_name, team_id FROM teams
WHERE team_name NOT IN (SELECT alias FROM team_aliases);
