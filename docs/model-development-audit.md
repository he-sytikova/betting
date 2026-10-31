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
5. **No odds-aware backtest.** Historical bookmaker prices are not stored, so the current research report cannot establish ROI, closing-line value (CLV), or whether a model edge survives bookmaker margin.
6. **Source constraints.** The open-matchday repository is marked non-commercial with attribution required in the audit metadata. Any future training use must respect its license terms.

## Validation status before this correction

The existing walk-forward report covered three evaluated seasonal folds and 11,825 pooled test rows per market. Negative Binomial showed a small, statistically supported improvement over Poisson for corners at line 7.5 after the six-line Holm correction; most other corner-line comparisons were inconclusive. The previous card-line comparison is not relied upon because it used the wrong lines.

## Next development sequence

1. Re-run the existing report with the corrected market-specific lines and review yellow-card calibration at 0.5–5.5.
2. Benchmark CatBoost Poisson against the existing Poisson mean model on identical chronological folds, keeping it research-only.
3. Compare count error, Brier score, log loss, calibration, and stability by season and league before considering any candidate for production.
4. Add new match features only when historical values can be reconstructed strictly as-of kickoff and coverage is adequate.
5. Build odds capture and an out-of-sample betting ledger before making claims about expected value, ROI, or CLV.

## Production safety

This audit and the candidate benchmark do not modify the published browser model or public prediction pages. A candidate must pass chronological evaluation and calibration review before any production export is changed.
