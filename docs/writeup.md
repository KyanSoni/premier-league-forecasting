# How close can a home-built model get to the bookmakers?

*A Premier League forecasting project, 2025-26 and 2026-27 — Kyan Soni*

## The question

Bookmakers' closing odds are the toughest benchmark in football forecasting. They combine statistical models, team news and the money of thousands of bettors. I wanted to know how close a model built from public data could get to them. I also wanted every claim to be honest: each improvement had to be measured on matches the model had not seen.

The project started as an Excel Monte Carlo model of the 2025-26 season. It grew into a system with four parts:

- a PostgreSQL database holding four seasons of data;
- Python models for match results, player props and team markets;
- a weekly pipeline that runs in three commands;
- an Excel dashboard that refreshes from the database.

## The data

All of the data is public:

- **football-data.co.uk:** results, match statistics, referees and bookmaker odds for 2023-24 to 2026-27.
- **Understat:** expected goals (xG) for teams and players.
- **Official Fantasy Premier League API:** injuries, suspensions and penalty takers.
- **FPL-Core-Insights:** per-match player statistics (used locally only, because it has no licence for redistribution).
- **Transfermarkt:** squad values.

Everything lands in staging tables first. SQL then links each source to one set of team, player and match ids. Linking was a real piece of work. "Man United", "Manchester United" and "Manchester Utd" all need to resolve to one team, and players are matched across sources by name, team and minutes played. Checks run on every load: row counts per season, matches that fail to link, and cross-checks between sources (goals and cards agree between football-data and FPL). Over 90% of Understat minutes are matched to FPL players.

## The models

**Season model (Excel).** Each team gets an attack and a defence rating. The model turns them into expected goals for every fixture and plays the rest of the season 1,000 times. That gives title, top-four and relegation chances, and a predicted finishing position.

**Match models (Python).** Several models predict each match:

- a Dixon-Coles model fitted by maximum likelihood, with time decay and ridge shrinkage;
- a second Dixon-Coles model fitted on expected goals instead of goals;
- pi-ratings, a published benchmark;
- a model that reverse-engineers ratings from past bookmaker odds.

The final match prediction is a 50/50 ensemble of the main ratings model and the Dixon-Coles model on xG.

**Player and team markets (Python).** The player models are built in layers:

1. Expected minutes come from recent line-ups and the injury feed.
2. Per-90 rates for shots, fouls, tackles and so on are shrunk towards the average for the player's position.
3. Those rates are scaled to the match model's expected goals and the opponent.

Team counts (corners, fouls, cards, shots and tackles) use negative binomial models with recency weighting. Fouls also get a referee factor, and corners get a game-state effect. A slip checker simulates each fixture 20,000 times, so bet-builder legs in the same match are priced together rather than multiplied as if they were independent.

## How every change was tested

Every upgrade followed the same rule.

**Re-create the past honestly.** I rebuilt "runs" as they would have looked at ten points in each season: pre-season, then after gameweeks 3, 5, 10, 15, 18, 22, 26, 30 and 34. Each run sees only results up to that point and predicts every remaining match.

**Score out of sample.** Match predictions are scored with the Ranked Probability Score (RPS). RPS rewards putting probability on the right result and penalises near-misses (a draw when the home team won) less than far misses. Markets are scored with the Brier score. League forecasts are scored on title, top-four and relegation chances and on the gap between predicted and final position.

**Adopt only if it helps in both seasons.** A change had to beat the current model in 2024-25 *and* 2025-26. This rule rejected several ideas that looked good in one season.

**Log everything.** Every test, including the failures, has a row in the upgrade log ([`upgrade_log.csv`](upgrade_log.csv)), with the numbers and the decision.

## Results

**Matches.** Over 3,595 out-of-sample predictions (both seasons, runs after GW3 to GW34), the main changes were:

| Model | RPS | Behind bookmakers |
|---|---|---|
| Original model | 0.2271 | 11.8% |
| + blend of pre-season and current ratings | 0.2126 | 4.6% |
| + xG ratings | 0.2112 | 3.9% |
| Final ensemble | **0.2103** | **3.5%** |
| Bookmakers' closing odds | 0.2032 | — |

The single biggest gain came from the simplest idea. Early in a season, the current table is mostly noise, so I mixed pre-season ratings in by games played. That cut RPS by 7.1% overall and by 17% in the first weeks.

**League table.** In 2025-26 the average error in predicted finishing position fell from 3.94 places pre-season to 1.94 by GW22 and 1.15 by GW34. Adding 10% rating uncertainty to the season simulations also fixed overconfidence. Top-four chances of 80-99% had come true 78% of the time; after the change they came true 91% of the time.

**Markets.** 117 of 126 market lines beat a simple baseline in 2024-25, and 110 of 126 in 2025-26. The baseline is the player's own season rate or the league average. The chances are also calibrated. Predicted 24% chances to score happened 24.1% of the time, and predicted 14.1% chances happened 14.7% of the time. For bet builders, simulating the legs together matters. "Team to win and a player to score" landed 20.8% of the time in 2024-25. The simulation said 21.8%; multiplying the two single chances said 15.7%.

## Where the model falls short of the bookmakers

The remaining gap has a pattern. Compared with the market, the model is flatter: it gives favourites a little too little and underdogs a little too much. It is also slow to trust big clubs that start a season badly. Bookmakers price in team news, transfers and fitness, which a ratings model learns only after results arrive.

That is also why I built a value tracker rather than claiming the model finds value. Each week it logs every price for the next gameweek with three numbers: the model's chance, the bookmaker's fair chance (with the margin removed) and a 50/50 blend of the two. Prices where the blended chance gives an edge of 5% or more are flagged. After the games, the tracker records the closing odds and the result, so it measures two things: whether flagged prices beat the closing line, and whether they won. It started in October 2026. I will report it once more than 100 prices have settled, whatever it shows.

## What didn't work

The failures were as useful as the successes:

- **Promoted clubs rated from Championship form.** Worse in both seasons. Championship ratings explain only about 7% of Premier League form.
- **100% xG ratings.** Worse than a 50/50 mix of xG and goals.
- **Dixon-Coles on its own.** Behind the main model. It only helped once it was part of an ensemble and could learn from two previous seasons.
- **Ratings from past bookmaker odds.** Helped one season and hurt the other. Past prices do not carry the information that makes closing odds good.
- **Squad values for every club.** Helped 2025-26 and hurt 2024-25 at every weight. Squad values were kept only for newly promoted clubs.
- **Weighting recent form for goal scoring.** No gain.
- **A referee factor for red cards.** No gain.
- **Corners and red-card match markets.** No better than the league average, and the dashboard says so.

The checks also caught two mistakes of my own. One backtest run had been built with end-of-season ratings, a look-ahead leak that made it look better than it was (RPS 0.2035 instead of the honest 0.2176). The other was wrong goal totals for two promoted clubs. Both were fixed and the affected tests re-run.

## Using AI

I built the project with Claude as a development partner for Python and SQL. That made it possible to build a large system quickly. It also made testing more important, not less. The automated checks caught errors in AI-written code. In the first live week, for example, they caught doubtful players being given a 94% chance to start when the injury feed said 25%, which was then fixed in `player_minutes.py`. The modelling choices, the testing rules and the decisions to adopt or reject were mine.

## What's next

- **Season-end review.** Re-score all of 2026-27, including the player-availability adjustment that is in test until about GW18.
- **Close more of the gap with information, not cleverness.** Use confirmed line-ups an hour before kick-off and blend in the market's opening prices.
- **Judge the value tracker on closing-line value**, which shows whether the model is finding real edges well before results can.
- **Move the season model from Excel into Python**, so the whole pipeline runs in one place and can be scheduled.
