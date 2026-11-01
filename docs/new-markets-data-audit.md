# New markets: historical data coverage audit

Audit date: 2026-10-31  
Branch: `model-development`  
Status: coverage reconnaissance only; no new training data or production code changed.

## Scope

Markets under consideration:

- Team fouls
- Team shots
- Team shots on target
- Red cards
- Referee assignment and referee history as optional model features

The existing historical coverage artifact `data/corners_cards_audit.json` contains 78,599 match rows from 20 Football-Data competition codes for seasons 2015/16–2025/26. The field-level percentages below are raw match coverage, not proof of team-level eligibility or permission to train.

## Coverage by competition

Percentages are the share of played matches with both home and away values present.

| Competition | Matches | Fouls | Shots | Shots on target | Red cards | Referee |
|---|---:|---:|---:|---:|---:|---:|
| England Premier League | 4,180 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| England Championship | 6,072 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| England League One | 5,920 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| England League Two | 5,960 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Scotland Premiership | 2,459 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Scotland Championship | 1,892 | 81.0% | 81.0% | 81.0% | 81.0% | 81.0% |
| Scotland League One | 1,869 | 80.6% | 80.7% | 80.7% | 80.7% | 80.7% |
| Scotland League Two | 1,867 | 80.6% | 80.7% | 80.7% | 80.7% | 80.7% |
| Germany Bundesliga | 3,366 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% |
| Germany 2. Bundesliga | 3,366 | 81.8% | 81.8% | 81.8% | 81.8% | 0.0% |
| Italy Serie A | 4,180 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% |
| Italy Serie B | 4,388 | 78.9% | 78.9% | 78.9% | 78.9% | 0.0% |
| Spain La Liga | 4,180 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% |
| Spain Segunda | 5,082 | 81.4% | 81.4% | 81.4% | 81.4% | 0.0% |
| France Ligue 1 | 3,857 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% |
| France Ligue 2 | 3,930 | 70.9% | 80.6% | 80.6% | 80.6% | 0.0% |
| Netherlands Eredivisie | 3,292 | 81.4% | 81.4% | 81.4% | 81.4% | 0.0% |
| Belgium First Division | 3,045 | 68.3% | 84.0% | 84.1% | 84.1% | 0.0% |
| Portugal Primeira Liga | 3,366 | 81.8% | 81.8% | 81.8% | 81.8% | 0.0% |
| Turkey Super Lig | 3,700 | 82.6% | 82.6% | 82.6% | 82.6% | 0.0% |
| Greece Super League | 2,628 | 63.5% | 81.6% | 81.6% | 81.6% | 0.0% |

Coverage values are rounded to one decimal place. Exact values remain in the JSON audit artifact.

## Preliminary interpretation

Using 80% match coverage as a screening threshold only:

- Fouls: 17 of 20 competitions meet the raw coverage threshold.
- Shots: 19 of 20 meet it.
- Shots on target: 19 of 20 meet it.
- Red cards: 19 of 20 meet it.
- Referee identity: 8 of 20 meet it; in this audit those are the four English and four Scottish divisions.

This is not the final league whitelist. Before training, each market must also pass per-team history depth, recent-10 coverage, season-by-season stability, date parsing, duplicate checks, and leakage-safe feature construction. Referee features must be optional and carry explicit missingness/coverage indicators.

## Critical source-use restriction

Football-Data's current data page says its free spreadsheet data is intended for private individuals, not commercial or data-training products using automated bots, scrapers, or AI. This project has an automated pipeline and trains statistical models. Therefore, despite the useful coverage profile, do **not** extend automated downloads or use this source for new model training unless the provider gives explicit written permission covering this use.

The existing coverage figures are retained as reconnaissance metadata only. They are not authorization to reuse the underlying records.

## Other candidate sources

- Open-Matchday currently publishes detailed statistics, including fouls, shots, shots on target, yellow/red cards, for a subset of competitions. The project's existing audit checked 75 competition folders but found only 1,736 matches with complete corner pairs and 1,726 with complete yellow-card pairs in the 2026/27 snapshot. Its repository states non-commercial use and attribution requirements. Verify current terms and exact market coverage before using it.
- BSD (sports.bzzoiro.com) is already configured in the app for fixtures/predictions. Its public coverage page reports broad current-season match-stat and referee coverage, but historical depth, downloadable match-level history, and training/API permissions have not yet been verified. This is the next source to investigate.
- Do not re-enable Api-Football or Sportmonks as a shortcut; they remain excluded/disabled in the project.

## Next steps

1. Verify BSD API endpoints, historical depth, field-level completeness, rate limits, and terms for storing and training on match statistics.
2. Check Open-Matchday's exact 2026/27 per-competition completeness for all five fields and document its licence constraints.
3. Identify a licensed historical source for referee assignments and referee-level rolling statistics.
4. Only then build a separate dataset for the new markets. Keep corners/cards training files and production exports unchanged.
5. Apply market-specific eligibility thresholds and chronological walk-forward evaluation. Red-card event probabilities require rare-event calibration and confidence intervals; referee features require shrinkage for low match counts.


## BSD API v2 reconnaissance (documentation review, 2026-10-31)

BSD's current API documentation confirms that the match detail endpoint exposes referee identity, and each match has a `/stats/` sub-resource. The stats payload documents eight stable keys per side: `ball_possession`, `total_shots`, `shots_on_target`, `corner_kicks`, `fouls`, `yellow_cards`, `red_cards`, and `offsides`. Missing values are explicitly `null`, not zero. This supports the four requested statistical targets without inferring zeros.

The event `/availability/` endpoint reports per-match stat keys, enabling coverage checks before fetching full match details. The referee API provides referee lists, profiles with per-match aggregates (cards, fouls, penalties), and referee match logs with date/league/season filters.

Important limitations:
- The docs do not establish how many historical seasons have complete stats. Coverage is per-match and can vary substantially; the provider measured a recent sample where many matches lacked at least one stat. Therefore no training eligibility is declared yet.
- Commentary does not provide a complete foul log; fouls are available as aggregate team stats, not as a reliable event-by-event feed.
- The provider says total shots from aggregate stats and shotmap can disagree; for shot counts the shotmap is recommended, with shootout attempts excluded. For the first model dataset we should preserve both values and apply a documented source-precedence rule rather than silently mixing them.
- Red-card aggregates and incident events need reconciliation for rescinded cards and second-yellow dismissals. Define the target as match total red-card dismissals according to final settled stats, not naive count of every card timeline item.
- Referee history is available as a distinct API resource, but referee assignment coverage by competition and season still needs measurement.

The BSD license v4.0 (effective 2026-10-01) explicitly permits internal retention, historical snapshots, backtesting, model development/evaluation, ML training and display of derived outputs. It prohibits redistribution of substantial raw data and sharing API keys. The license does not warrant accuracy or fitness for wagering.

## Automated BSD historical-statistics audit

The first manual run (2026-10-31, run `41241880525`) successfully sampled 181 matches across 20 matched competition codes. It exposed two audit defects: fuzzy league matching assigned Scottish Championship/League One/League Two to English competitions, and rapid requests triggered HTTP 429 in the Turkish and Greek samples. The first report also checked only availability keys, not actual team-stat values.

The audit has now been hardened on `model-development`:

- Referee identity/history is excluded entirely by project decision.
- League matching requires an exact normalized league-name match **and** a country match; ambiguous or country-less matches are reported as unmatched rather than guessed.
- Requests are paced and HTTP 429 responses are retried with `Retry-After` or exponential backoff.
- The sample is increased to up to 10 recent finished matches in each of the two latest seasons per competition.
- For each sampled match, the script checks both `/availability/` and actual `/stats/` values. The report distinguishes key availability from the presence of both teams' non-null values. Zero is treated as a valid value; null/missing is not.
- Only aggregate coverage metadata is uploaded; raw match payloads are not stored.
- The workflow timeout is 60 minutes to accommodate rate limiting.

The report is still a bounded reconnaissance sample, not a final league whitelist. A league/market is not eligible for training solely because the sample has high coverage. The next decision must use actual two-sided values, adequate historical depth, season stability, and a sufficiently large dataset. Shot totals use BSD aggregate team stats for this first audit; any later shotmap comparison must be separately validated before choosing a production target definition.

Run GitHub Actions → **BSD historical coverage audit - new markets** → **Run workflow** on `model-development`. The current production branch and Pages deployment are not changed.
