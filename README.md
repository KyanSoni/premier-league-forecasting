# Premier League Forecasting

**A Premier League forecasting system: an Excel Monte Carlo season model, a PostgreSQL data pipeline, Python models for match, player and team betting markets, and a live Excel dashboard — every change tested out of sample against the bookmakers.**

Built by Kyan Soni · 2025-26 and 2026-27 seasons · Excel · PostgreSQL · Python · Power Query

---

## Headline results

| | Result | Evidence |
|---|---|---|
| **Match predictions** | Ranked Probability Score (RPS) improved from **0.2271 to 0.2103 (−7.4%)**, cutting the gap to the bookmakers' closing odds from **11.8% to 3.5%** | 3,595 out-of-sample matches, 2024-25 and 2025-26 |
| **Is it real?** | Bootstrap 95% intervals on every upgrade: the ratings blend is a proven gain (RPS **−0.0126**, interval −0.0174 to −0.0076); each later upgrade is within ±0.002 and not significant on its own. The gap to the bookmakers is real: **0.0071** (0.0022 to 0.0120) | 700 matches, paired bootstrap by match ([`upgrade_significance.csv`](docs/upgrade_significance.csv)) |
| **League table** | Average error in predicted finishing position fell from **3.94 places pre-season to 1.94 by GW22 and 1.15 by GW34** | 2025-26, scored against the final table |
| **Player and match markets** | **117 of 126** market lines beat a simple baseline in 2024-25, **110 of 126** in 2025-26 | Brier score, every line predicted before kick-off |
| **Bet builders** | Simulating linked legs together matched reality: win + scorer hit **20.8%** vs **21.8%** simulated, against **15.7%** if the legs are just multiplied | Every fixture in 2024-25 |
| **Betting edge** | Replayed on two seasons, prices the model flagged as value **lost about 10% against the closing line** at every model weight (t-stat below −3). The model's weight against the market was cut from 50% to the fitted 25%, and the live tracker now reports expected return at the close with a t-stat | Every match of 2024-25 and 2025-26; live tracker from GW7 2026-27 |
| **Player data** | Player fouls, times fouled, shots and shots on target match Opta exactly (607 players, 20 matches), so player markets are on the bookmakers' scale | FotMob sample vs the project's data |

![2026-27 title race](images/title_race.png)

![A fixture as the dashboard's H2H sheet shows it](images/fixture_card.png)

---

## How it works

```
 Excel season model (ratings + 1,000 Monte Carlo seasons per run)
        │
        ▼
 weekly.py  part1 ─ results, Understat xG, bookmaker odds, FPL injuries  ──┐
            part2 ─ new ratings + simulations; Dixon-Coles, benchmarks,    │
                    market model, uncertainty sims, ensemble               ├──►  PostgreSQL (prem_sims)
            part3 ─ referees, expected minutes, player rates, team         │     tables + scoring views
                    counts, player and match markets for next gameweek     │
            odds  ─ upcoming prices → value log                          ──┘
            + leak_check.py (leak tests) and prop_log.py (prop price log)
        │
        ▼
 Excel dashboard (ODBC + Power Query, one Refresh All)
   title race · model scorecard · H2H and value vs bookmaker · player markets
   · match markets and referees · hit rates · bet builder checker · value tracker
```

Each week takes three commands and one Excel refresh. Every step prints **OK / CHECK** lines, so a failed download or a bad join shows up before it reaches the dashboard.

| Layer | What it does | Files |
|---|---|---|
| **Season model** | Team attack/defence ratings (home advantage, pre-season/current blend, xG, squad value for promoted clubs), 1,000 simulated seasons per run | Excel workbook (not published, see below) |
| **Database** | Results, xG, odds, ratings, simulations, player match data, referees; views score every model and feed the dashboard | [`sql/`](sql/) numbered scripts, in two ordered folders |
| **Match models** | Dixon-Coles (MLE with time decay and ridge shrinkage), Dixon-Coles on xG, pi-ratings, market-implied ratings, ensembles | `dc_model.py`, `benchmarks.py`, `market_model.py` |
| **Season simulations** | 10,000 seasons per run with rating uncertainty | `dynamic_sims.py` |
| **Player models** | Chance to start, expected minutes, per-90 rates shrunk to position averages, then to score / assist / shots / fouls / cards / saves | `player_minutes.py`, `player_rates.py`, `player_markets.py` |
| **Team and match markets** | Negative binomial models for corners, fouls, cards, shots, tackles with referee and game-state effects | `team_counts.py`, `match_markets.py` |
| **Bet builder checker** | Simulates each fixture 20,000 times so linked legs are priced together | `slip_checker.py` |
| **Automation** | The whole weekly pipeline, checks and the value log | `weekly.py` |
| **Model audits** | Calibration, market blend and margin removal, bootstrap significance, scoreline shape, player data vs Opta, perfect-XI test, season-forecast tails, leak check | `calibrate.py`, `market_blend.py`, `significance.py`, `scoreline_check.py`, `prop_audit.py`, `xi_test.py`, `season_tails.py`, `leak_check.py`, `quant_tools.py` |
| **Prop log** | Every prop price looked at, with the model's chance and the result, scored against the bookmaker | `prop_log.py` |

---

## Results in more detail

![Match model vs bookmakers](images/model_comparison.png)

The original model was 11.8% behind the bookmakers. The biggest single gain was blending pre-season and current ratings (Upgrade 2); the rest came from xG ratings and an ensemble with Dixon-Coles models. The remaining 3.5% gap is mostly information the bookmakers price in and a ratings model cannot see, such as team news and transfers. Upgrade 19 backed this up: the model's probabilities are already well calibrated (sharpening them did not help out of sample), so the gap is not a scaling problem.

![League table forecast error](images/position_error.png)

![Market tests](images/market_tests.png)

![Player market calibration](images/market_calibration.png)

When the model says a player has a 24% chance to score, it happened 24.1% of the time; 14.1% predictions happened 14.7% of the time. The calibration step (Upgrade 15.6) fixed chances that were too extreme for cards before it was added.

---

## What I tested, and what I kept or dropped

Every change was scored out of sample on two full seasons and adopted only if it helped in **both**. The full record, with the numbers, is in [`docs/upgrade_log.csv`](docs/upgrade_log.csv).

| Upgrade | Result | Decision |
|---|---|---|
| Home advantage factors | RPS −1.8% | ✅ Kept |
| Blend pre-season and current ratings (k = 15) | RPS −7.1%; −17% early season | ✅ Kept |
| Regression from Championship ratings for promoted clubs | **Worse** in both seasons (+3.5%, +3.9%) | ❌ Dropped |
| xG in the ratings (50%) | −1.7%, better in all 9 runs; 100% xG worse | ✅ Kept at 50% |
| Dixon-Coles on its own | Behind the main model | ❌ Kept only as a second opinion |
| Ensemble with Dixon-Coles on xG | Best of all models in both seasons | ✅ Main match model |
| Market-implied ratings ensemble | Better in 2025-26, **worse** in 2024-25 | ❌ Dropped |
| Squad values for every club | Helped 2025-26, **hurt** 2024-25 at every weight | ❌ Dropped (kept for promoted clubs only) |
| Rating uncertainty in season sims | 10% helped; 15% hurt top-4 and relegation | ✅ Kept at 10% |
| Recent form weighting for goals | No gain | ❌ Dropped (kept for xA, tackles, reds) |
| Referee factor for red cards | No gain | ❌ Dropped (kept for fouls) |
| Match corners and red card markets | Tie with the league-average baseline | ⚠️ Shown but flagged "no better than average" |
| Player injuries and suspensions (FPL) | Can only be tested going forward | 🧪 In test until about GW18 |
| Calibration layer (sharpening power) | Fitted powers disagreed between seasons (1.09, 0.98); RPS ±0.0002, log loss worse | ❌ Not needed: already calibrated |
| Fitted model/market blend and power margin removal | Model weight 0.25 for 1X2, 0 for over/under 2.5 (was 50/50) | ✅ Kept |
| Value flags judged against the closing line | −6% to −12% expected return at every weight | ⚠️ No edge on 1X2 or goals: flags are for review, not bets |
| Closing-line test, t-stat and quarter-Kelly stakes in the tracker | Verdict after 100 settled prices | ✅ Kept |
| Bootstrap significance of every upgrade | Only the ratings blend is significant on its own | ℹ️ Ensemble kept (removing parts is equally unproven) |
| Dixon-Coles low-score correction for the goal markets | 1-0s over-predicted, 2-2s under (z +3.6); best correction worth 0.0018 log loss | ❌ Not adopted; correlated goals noted for later |
| Player data vs Opta | Identical totals for all four stats | ✅ No rescaling; found and fixed blank-for-zero rows in 2026-27 |
| Knowing the actual starting XIs (perfect-information test) | RPS +0.0002 (95% interval −0.0022 to +0.0027): no help | ❌ Line-up model not built |
| Season forecasts at the extremes (PIT, interval coverage) | Python uncertainty sims honest at every stage; Excel sims overconfident before GW10 | ⚠️ Uncertainty sims used early in the season |
| Automatic leak check (8 tests) | All clear | ✅ Runs every week |
| Prop price log (model vs bookmaker on the props I look at) | Collecting; verdict after 150 settled prices | 🧪 In test |

Three data errors were caught by the checks and fixed: one run had been built with end-of-season ratings (a look-ahead leak that made it look better than it was: RPS 0.2035 instead of the honest 0.2176), two promoted clubs had the wrong goal totals, and in 2026-27 the player data source began leaving blanks instead of zeros.

---

## Data sources and licences

| Source | Used for | In this repo? |
|---|---|---|
| [football-data.co.uk](https://www.football-data.co.uk/) | Results, match stats, referees, bookmaker odds | Downloaded by the scripts; not stored |
| [Understat](https://understat.com/) | Team and player expected goals | Downloaded by the scripts; not stored |
| [Fantasy Premier League API](https://fantasy.premierleague.com/) | Injuries, suspensions, penalty takers | Downloaded by the scripts; not stored |
| [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League) | Past FPL gameweek data (minutes, starts, cards, saves) and Understat player xG, 2022-23 to 2025-26 | Downloaded by the scripts; not stored |
| FPL-Core-Insights | Per-match player stats | **Used locally only.** No licence file, so neither the data nor the dashboard that contains it is redistributed |
| Transfermarkt | Squad values (1 August each season) | Collected by hand into a local CSV; not stored |

---

## Use of AI

Built with Claude as a development partner for Python and SQL. Every model change was adopted only after out-of-sample tests on two seasons, and the automated checks caught errors in AI-written code. For example, in October 2026 they caught doubtful players getting a 94% start chance when the injury feed said 25%, which was fixed in `player_minutes.py`. The modelling decisions, test design and acceptance rules are my own.

---

## How to run

You need PostgreSQL 15+, Python 3.11+ and Excel (Windows, for the model and dashboard).

```powershell
# 1. Python packages
pip install -r python/requirements.txt

# 2. Database connection (the password is never stored in the code)
setx PREM_DB_URL "postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/prem_sims"

# 3. Create the database prem_sims in pgAdmin, then run the scripts in
#    sql/a_foundation_and_upgrades_1-6 and then sql/b_weekly_and_upgrades_7-17,
#    in number order (see sql/README.md)

# 4. Each gameweek
cd python
python weekly.py part1 "2026-27 after GW7"     # results, xG, odds, injuries
#   ...in Excel: paste availability, F9, save
python weekly.py part2 "2026-27 after GW7"     # ratings, sims, all match models
python weekly.py part3 "2026-27 after GW7"     # next gameweek's player and match markets
python weekly.py odds                           # upcoming prices and value log (rerun on Friday)
#   ...in the dashboard: Data > Refresh All
```

The full weekly routine, with troubleshooting, is in [`docs/weekly_update_guide.pdf`](docs/weekly_update_guide.pdf). How each upgrade was built and tested is in the two upgrade guides in [`docs/`](docs/).

---

## Repository layout

```
python/    the 24 scripts, requirements.txt, .env.example
sql/       a_ (foundation, upgrades 1-6) and b_ (weekly, upgrades 7-27): tables, loads, scoring and dashboard views
images/    charts used in this README
docs/      write-up, upgrade log, significance table, weekly guide, upgrade guides (PDF)
```

📄 **Full write-up:** [`docs/writeup.md`](docs/writeup.md) — the question, data, models, how each change was tested, results against the bookmakers, what didn't work and what's next.

---

*This project measures forecasting accuracy. It is not betting advice: the model is still less accurate than the bookmakers' closing prices.*
