# Corners and yellow-card model development audit

Audit date: 2026-10-31

## Current baseline

- Browser production export is an experimental Poisson model for corners and yellow cards.
- The checked-in training dataset contains 42,851 rows and ends on 2026-05-24.
- Historical source audit checked 78,599 match records across the configured competitions; 71,432 had complete corner totals and 71,435 had complete yellow-card totals.
- Ten leagues currently pass the historical and team-level eligibility rules for both markets: England Premier League, Championship, League One, League Two; Scotland Premiership; Germany Bundesliga; Italy Serie A; Spain La Liga; France Ligue 1; Netherlands Eredivisie.
- The 2026/27 open-matchday source was checked for coverage, but it is not currently part of the historical training dataset. It reported 1,736 complete corner matches and 1,726 complete card matches across 75 checked competitions.

## Findings and priorities

1. **Market-line validation mismatch (corrected in this branch).** The app's exported card market uses 0.5–5.5, while the previous research report evaluated both markets on corner lines 7.5–12.5. This made the prior card-line calibration and line backtest unsuitable for the card markets shown in the app. The training report now passes separate line sets through holdout, walk-forward, calibration, feature diagnostics, and league/season summaries.
2. **No current-season historical rows.** The latest historical training target date is 2026-05-24, so the 2026/27 walk-forward fold is skipped until eligible completed-match statistics enter the training source.
3. **Limited feature set.** Current features are mostly rolling 5/10-match team statistics and league-level averages. Referee tendencies, fouls, shots, possession, attacking pressure, lineups, injuries, and bookmaker odds are not in the training dataset.
4. **Constant home-advantage feature.** The dataset builder currently writes home_advantage = 1 for every row. It contains no information and should be removed from future candidate feature sets rather than treated as a meaningful signal.
5. **Additional historical features are available.** The new coverage audit found that fouls, shots, shots on target, and red cards are present in the ten currently eligible leagues at 81.4%–100% match coverage. This is sufficient to test them as rolling pre-match features with missing-value handling.
6. **Referee coverage is partial.** The Referee field is complete in the eligible English and Scottish leagues but absent in the eligible German, Italian, Spanish, French, and Dutch top divisions. Referee strictness must therefore be optional and accompanied by a coverage/missingness indicator; it cannot be a universal feature.
7. **No odds-aware backtest.** Historical bookmaker prices for the corners/cards markets are not stored, so the current research report cannot establish ROI, closing-line value (CLV), or whether a model edge survives bookmaker margin.
6. **Source constraints.** The open-matchday repository is marked non-commercial with attribution required in the audit metadata. Any future training use must respect its license terms.

## Corrected validation results

The corrected walk-forward report covers three evaluated seasonal folds and 11,825 pooled test rows per market. Card totals are now evaluated on 0.5–5.5, matching the app.

- **Corners:** NB2 improves probability loss over Poisson at line 7.5 after the six-line Holm correction (adjusted p = 0.015 for Brier and 0.009 for Log Loss). The other lines do not show a confirmed improvement after correction.
- **Yellow cards:** NB2 is better at 0.5, 4.5, and 5.5; worse at 2.5 and 3.5; and inconclusive at 1.5. These differences pass the six-line Holm correction in the corresponding directions. This suggests distribution choice should be assessed by market line, not globally.

## CatBoost candidate benchmark

CatBoost Poisson was compared with a PoissonRegressor research baseline using the same 19 numeric features, the same chronological folds, and league as an additional categorical feature. This controlled algorithm comparison is **not** a direct comparison with the reduced feature set in the browser export.

| Market | Poisson MAE | CatBoost MAE | Poisson deviance | CatBoost deviance |
|---|---:|---:|---:|---:|
| Corners | 2.709493 | 2.714260 | 1.175476 | 1.179015 |
| Yellow cards | 1.596568 | 1.594611 | 1.156594 | 1.155529 |

CatBoost is worse on corners. Its small point-metric improvement for cards is not accompanied by statistically confirmed Brier/Log Loss improvement after Holm correction. It remains research-only and is not selected for production.

## Next development sequence

1. Audit historical source coverage for referee, fouls, shots, shots on target, and red-card fields before changing the training dataset.
2. Add only features that have sufficient historical coverage and can be reconstructed strictly as-of kickoff, with same-day matches excluded from prior form.
3. Compare feature-augmented Poisson/NB2 and CatBoost on identical chronological folds; do not tune on the final evaluation folds.
4. Evaluate line-specific distribution choice and calibration on the corrected card and corner lines.
5. Build odds capture and an out-of-sample betting ledger before making claims about expected value, ROI, or CLV.

## Production safety

This audit and the candidate benchmark do not modify the published browser model or public prediction pages. A candidate must pass chronological evaluation and calibration review before any production export is changed.
