SELECT 'results' AS data,
       (SELECT COUNT(*) FROM stg_results) AS staged,
       (SELECT COUNT(*) FROM matches)     AS loaded
UNION ALL
SELECT 'sims', (SELECT COUNT(*) FROM stg_sims), (SELECT COUNT(*) FROM sim_results)
UNION ALL
SELECT 'ratings', (SELECT COUNT(*) FROM stg_ratings), (SELECT COUNT(*) FROM team_ratings)
UNION ALL
SELECT 'final_tables', (SELECT COUNT(*) FROM stg_final_tables), (SELECT COUNT(*) FROM final_tables)
UNION ALL
SELECT 'promoted', (SELECT COUNT(*) FROM stg_promoted), (SELECT COUNT(*) FROM promoted_history);

SELECT DISTINCT TRIM(home_team) AS unmatched FROM stg_results
WHERE TRIM(home_team) NOT IN (SELECT alias FROM team_aliases)
UNION
SELECT DISTINCT TRIM(team) FROM stg_sims
WHERE TRIM(team) NOT IN (SELECT alias FROM team_aliases);

SELECT s.label, COUNT(*) AS fixtures
FROM matches m JOIN seasons s USING (season_id)
GROUP BY s.label;

SELECT season_id, home_team_id, COUNT(*) AS home_games
FROM matches
GROUP BY season_id, home_team_id
HAVING COUNT(*) <> 19;

SELECT run_id, COUNT(*) AS rows_in_run
FROM sim_results
GROUP BY run_id;

SELECT run_id, sim_no
FROM sim_results
GROUP BY run_id, sim_no
HAVING COUNT(DISTINCT final_position) <> 20;

SELECT s.label, COUNT(home_goals) AS played, COUNT(*) - COUNT(home_goals) AS to_play
FROM matches m JOIN seasons s USING (season_id)
GROUP BY s.label;