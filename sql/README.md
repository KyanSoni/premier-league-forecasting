# SQL scripts

PostgreSQL scripts for the `prem_sims` database, kept in the order they were written. Run the two folders in order: folder **a** first, then **b**, with the files in each folder in number order.

## a_foundation_and_upgrades_1-6

| Scripts | What they do |
|---|---|
| 01–07 | Staging tables, core tables (seasons, teams, matches, model runs, ratings, simulations), team-name aliases, loading, checks, and the first views (league table, simulation summary, predicted vs actual) |
| 10 | Match predictions from each run's ratings, scored with Brier score and calibration |
| 11–12 | Foundations for model variants; Upgrade 1 home and away advantage |
| 13–16 | Upgrade 2 blend of pre-season and current ratings, the out-of-sample test and two data fixes |
| 17–20 | Upgrade 3 promoted-club regression on Championship ratings (tested, not adopted) |
| 21 | Upgrade 4 bookmaker benchmark (odds with the margin removed) |
| 22 | Upgrade 5 xG ratings (Understat) |
| 23 | Upgrade 6 Dixon-Coles predictions from `dc_model.py` |

Scripts 08–09 were never created, so the numbering skips them.

## b_weekly_and_upgrades_7-17

| Scripts | What they do |
|---|---|
| 20–22 | Weekly loads: results and xG, a new model run, odds refresh (the steps `weekly.py` now automates) |
| 23 | Upgrade 7: two more seasons of history (2023-24, 2024-25) |
| 24 | Upgrade 8: season forecast scoring (title, top 4, relegation, places out) |
| 25 | Upgrades 9–10: ensembles with Dixon-Coles and market-implied ratings |
| 26 | Upgrades 11–12: season simulations with rating uncertainty; pi-ratings and Dixon-Coles on xG |
| 27, 27b | Upgrade 13: squad value prior and its variants |
| 28 | Upgrade 14: player availability (FPL injuries and suspensions) |
| 29 | Upgrade 15: player and team match data, expected minutes, rates, markets and their tests |
| 30 | Upgrade 15: summary and dashboard views (market tests, calibration, player card, fixture card, last-5 hit rates) |
| 31 | Upgrade 17: upcoming odds and the value tracker (a reference copy, because `weekly.py` creates these itself) |
| 32 | Upgrades 19–24: read-only checks for calibration, the market blend, the closing-line test and the player-market rescaling (the tables are made by the Python scripts) |
| 33 | Upgrades 25–27: read-only checks for the prop log, the perfect-XI test inputs, the season-forecast checks and the frozen-ratings leak |

The two folders reuse the numbers 20–23. Folder **b** started a new numbered series once the weekly pipeline began.
