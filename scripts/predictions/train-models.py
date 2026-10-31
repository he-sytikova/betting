#!/usr/bin/env python3
"""Train Poisson baselines and evaluate total-goal-style market lines/calibration."""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import numpy as np
from scipy.stats import nbinom, poisson
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "corners_cards_training.csv"
OUT = ROOT / "data" / "corners_cards_model_report.json"
HOLDOUT_START = date(2024, 7, 1)
LINES = [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
FEATURES = [
    "home_team_for_avg5", "home_team_against_avg5",
    "home_team_for_avg10", "home_team_against_avg10",
    "away_team_for_avg5", "away_team_against_avg5",
    "away_team_for_avg10", "away_team_against_avg10",
    "home_card_team_for_avg5", "home_card_team_against_avg5",
    "home_card_team_for_avg10", "home_card_team_against_avg10",
    "away_card_team_for_avg5", "away_card_team_against_avg5",
    "away_card_team_for_avg10", "away_card_team_against_avg10",
    "league_corners_avg", "league_cards_avg", "home_advantage",
]

def poisson_deviance(y, pred):
    pred = np.maximum(pred, 1e-8)
    terms = np.where(y > 0, y * np.log(np.maximum(y, 1e-8) / pred) - (y - pred), pred)
    return float(2 * np.mean(terms))

def probability_metrics(actual, probability):
    actual = np.asarray(actual, dtype=float)
    probability = np.clip(np.asarray(probability, dtype=float), 1e-8, 1 - 1e-8)
    brier = float(np.mean((probability - actual) ** 2))
    log_loss = float(-np.mean(actual * np.log(probability) + (1 - actual) * np.log(1 - probability)))
    bins = []
    edges = np.linspace(0, 1, 11)
    for idx in range(10):
        mask = (probability >= edges[idx]) & (
            probability < edges[idx + 1] if idx < 9 else probability <= edges[idx + 1]
        )
        if np.any(mask):
            bins.append({
                "range": [round(float(edges[idx]), 1), round(float(edges[idx + 1]), 1)],
                "count": int(np.sum(mask)),
                "meanPredicted": round(float(np.mean(probability[mask])), 4),
                "observedRate": round(float(np.mean(actual[mask])), 4),
            })
    return {
        "brierScore": round(brier, 5),
        "logLoss": round(log_loss, 5),
        "calibrationBins": bins,
    }

def negative_binomial_sf(line, mean, alpha):
    mean = np.maximum(np.asarray(mean, dtype=float), 1e-8)
    alpha = max(float(alpha), 1e-8)
    size = 1.0 / alpha
    probability = size / (size + mean)
    return nbinom.sf(int(line), size, probability)

def evaluate_lines(y, prediction, distribution="poisson", alpha=None):
    results = []
    for line in LINES:
        # For half-point lines there is no push: over means integer total > line.
        over_actual = (y > line).astype(float)
        if distribution == "negative_binomial":
            over_probability = negative_binomial_sf(line, prediction, alpha)
        else:
            over_probability = poisson.sf(int(line), prediction)
        under_actual = 1.0 - over_actual
        under_probability = 1.0 - over_probability
        results.append({
            "line": line,
            "over": {
                "eventCount": int(np.sum(over_actual)),
                "eventRate": round(float(np.mean(over_actual)), 5),
                **probability_metrics(over_actual, over_probability),
            },
            "under": {
                "eventCount": int(np.sum(under_actual)),
                "eventRate": round(float(np.mean(under_actual)), 5),
                **probability_metrics(under_actual, under_probability),
            },
        })
    return results

def grouped_metrics(rows, y, pred, baseline, group_key, min_rows=100, distribution="poisson", alpha=None):
    groups = {}
    keys = sorted({r.get(group_key, "") for r in rows if r.get(group_key, "")})
    for key in keys:
        mask = np.array([r.get(group_key, "") == key for r in rows], dtype=bool)
        count = int(np.sum(mask))
        if count < min_rows:
            continue
        gy, gp, gb = y[mask], pred[mask], baseline[mask]
        groups[key] = {
            "matches": count,
            "mae": round(float(np.mean(np.abs(gy - gp))), 4),
            "baselineMae": round(float(np.mean(np.abs(gy - gb))), 4),
            "poissonDeviance": round(poisson_deviance(gy, gp), 4),
            "baselinePoissonDeviance": round(poisson_deviance(gy, gb), 4),
            "totalLines": evaluate_lines(gy, gp, distribution, alpha),
            "baselineTotalLines": evaluate_lines(gy, gb),
            "actualMean": round(float(np.mean(gy)), 4),
            "actualVariance": round(float(np.var(gy)), 4),
            "varianceToMean": round(float(np.var(gy) / np.mean(gy)), 4) if np.mean(gy) > 0 else None,
        }
    return {"minimumGroupRows": min_rows, "groupsReported": len(groups), "groups": groups}

def paired_bootstrap_ci(actual, p_poisson, p_nb, seed=20261031, samples=1000):
    """95% paired bootstrap CI for NB2-minus-Poisson probability-loss differences."""
    y = np.asarray(actual, dtype=float)
    p = np.clip(np.asarray(p_poisson, dtype=float), 1e-8, 1 - 1e-8)
    q = np.clip(np.asarray(p_nb, dtype=float), 1e-8, 1 - 1e-8)
    brier_delta = (q - y) ** 2 - (p - y) ** 2
    loss_p = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    loss_q = -(y * np.log(q) + (1 - y) * np.log(1 - q))
    log_delta = loss_q - loss_p
    rng = np.random.default_rng(seed)

    def ci(values):
        n = len(values)
        means = np.empty(samples)
        for start in range(0, samples, 100):
            count = min(100, samples - start)
            sample_idx = rng.integers(0, n, size=(count, n))
            means[start:start + count] = values[sample_idx].mean(axis=1)
        return [round(float(x), 7) for x in np.percentile(means, [2.5, 97.5])]

    return {
        "brierDeltaNbMinusPoisson": round(float(brier_delta.mean()), 7),
        "brierDelta95CI": ci(brier_delta),
        "logLossDeltaNbMinusPoisson": round(float(log_delta.mean()), 7),
        "logLossDelta95CI": ci(log_delta),
        "note": "Negative favors NB2; positive favors Poisson; CI crossing zero means no clear difference",
    }


def calibration_probability_bands(y, mean, alpha, distribution):
    output = []
    y = np.asarray(y, dtype=float)
    for line in LINES:
        actual = (y > line).astype(float)
        if distribution == "negative_binomial":
            probability = negative_binomial_sf(line, mean, alpha)
        else:
            probability = poisson.sf(int(line), np.maximum(mean, 1e-8))
        for lower, upper in ((0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.000001)):
            mask = (probability >= lower) & (probability < upper)
            if np.any(mask):
                output.append({
                    "line": line,
                    "predictedRange": [lower, min(upper, 1.0)],
                    "matches": int(mask.sum()),
                    "meanPredicted": round(float(probability[mask].mean()), 5),
                    "observedRate": round(float(actual[mask].mean()), 5),
                })
    return output


def train_market(rows, target, eligible_flag, baseline_key):
    usable = [r for r in rows if r[eligible_flag] == "1" and r[target] != ""]
    train = [r for r in usable if date.fromisoformat(r["date"]) < HOLDOUT_START]
    test = [r for r in usable if date.fromisoformat(r["date"]) >= HOLDOUT_START]
    if len(train) < 1000 or len(test) < 100:
        return {"status": "insufficient_data", "trainRows": len(train), "testRows": len(test)}
    x_train = np.array([[float(r.get(k) or "nan") for k in FEATURES] for r in train])
    x_test = np.array([[float(r.get(k) or "nan") for k in FEATURES] for r in test])
    y_train = np.array([float(r[target]) for r in train])
    y_test = np.array([float(r[target]) for r in test])
    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("poisson", PoissonRegressor(alpha=0.5, max_iter=1000)),
    ])
    model.fit(x_train, y_train)
    pred = np.maximum(model.predict(x_test), 1e-8)
    poisson_train_mean = np.maximum(model.predict(x_train), 1e-8)
    pearson_alpha = max(
        float(np.sum((y_train - poisson_train_mean) ** 2 - poisson_train_mean)
              / np.sum(poisson_train_mean ** 2)),
        1e-6,
    )
    # Compare count distributions while holding the conditional mean fixed.
    # This isolates the effect of overdispersion and avoids an unstable,
    # rank-deficient unregularized NB coefficient fit on correlated features.
    nb_pred = pred.copy()
    baseline = np.array([
        float(r[baseline_key]) if r.get(baseline_key) else float(np.mean(y_train))
        for r in test
    ])
    baseline = np.maximum(baseline, 1e-8)
    mae, base_mae = float(np.mean(np.abs(y_test - pred))), float(np.mean(np.abs(y_test - baseline)))
    poisson_model = model.named_steps["poisson"]
    imputer = model.named_steps["imputer"]
    scaler = model.named_steps["scale"]
    return {
        "status": "evaluated",
        "trainRows": len(train), "testRows": len(test),
        "holdoutStart": HOLDOUT_START.isoformat(),
        "target": target,
        "metrics": {
            "mae": round(mae, 4),
            "baselineMae": round(base_mae, 4),
            "poissonDeviance": round(poisson_deviance(y_test, pred), 4),
            "baselinePoissonDeviance": round(poisson_deviance(y_test, baseline), 4),
            "improvesMaeOverBaseline": mae < base_mae,
            "improvesPoissonDevianceOverBaseline": poisson_deviance(y_test, pred) < poisson_deviance(y_test, baseline),
        },
        "totalLines": evaluate_lines(y_test, pred),
        "baselineTotalLines": evaluate_lines(y_test, baseline),
        "dispersion": {
            "actualMean": round(float(np.mean(y_test)), 4),
            "actualVariance": round(float(np.var(y_test)), 4),
            "varianceToMean": round(float(np.var(y_test) / np.mean(y_test)), 4) if np.mean(y_test) > 0 else None,
        },
        "byLeague": grouped_metrics(test, y_test, pred, baseline, "league"),
        "bySeason": grouped_metrics(test, y_test, pred, baseline, "season"),
        "distributionComparison": {
            "calibrationBands": {
                "poisson": calibration_probability_bands(y_test, pred, pearson_alpha, "poisson"),
                "negativeBinomial": calibration_probability_bands(y_test, pred, pearson_alpha, "negative_binomial"),
            },
            "pairedBootstrapByLine": [
                {
                    "line": line,
                    **paired_bootstrap_ci(
                        (y_test > line).astype(float),
                        poisson.sf(int(line), pred),
                        negative_binomial_sf(line, pred, pearson_alpha),
                        seed=20261031 + index,
                    ),
                }
                for index, line in enumerate(LINES)
            ],
        },
        "negativeBinomial": {
            "status": "evaluated",
            "meanModel": "same PoissonRegressor conditional mean; NB2 distribution adds estimated overdispersion",
            "alpha": round(float(pearson_alpha), 8),
            "metrics": {
                "mae": round(float(np.mean(np.abs(y_test - nb_pred))), 4),
                "poissonDeviance": round(poisson_deviance(y_test, nb_pred), 4),
                "improvesMaeOverPoisson": float(np.mean(np.abs(y_test - nb_pred))) < mae,
                "improvesDevianceOverPoisson": poisson_deviance(y_test, nb_pred) < poisson_deviance(y_test, pred),
            },
            "totalLines": evaluate_lines(y_test, nb_pred, "negative_binomial", pearson_alpha),
            "byLeague": grouped_metrics(test, y_test, nb_pred, baseline, "league", distribution="negative_binomial", alpha=pearson_alpha),
            "bySeason": grouped_metrics(test, y_test, nb_pred, baseline, "season", distribution="negative_binomial", alpha=pearson_alpha),
        },
        "model": {
            "type": "PoissonRegressor",
            "alpha": 0.5,
            "features": FEATURES,
            "imputerMedian": [float(v) for v in imputer.statistics_],
            "scalerMean": [float(v) for v in scaler.mean_],
            "scalerScale": [float(v) for v in scaler.scale_],
            "coefficients": [float(v) for v in poisson_model.coef_],
            "intercept": float(poisson_model.intercept_),
        },
    }

def main():
    if not DATA.exists():
        raise FileNotFoundError(f"Dataset not found: {DATA}")
    with DATA.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    report = {
        "generatedAt": date.today().isoformat(),
        "status": "calibration_and_line_backtest_only_not_for_public_predictions",
        "method": "Poisson mean model with Poisson vs Negative Binomial count-distribution comparison on the same chronological holdout; Negative Binomial dispersion estimated from training-only Pearson residuals",
        "holdoutStart": HOLDOUT_START.isoformat(),
        "datasetRows": len(rows),
        "evaluatedLines": LINES,
        "corners": train_market(rows, "corners_total", "corners_model_eligible", "league_corners_avg"),
        "yellowCards": train_market(rows, "cards_total", "cards_model_eligible", "league_cards_avg"),
        "note": "Calibration bins and Brier/Log Loss are descriptive holdout diagnostics, not proof of betting value. No ROI is calculated because historical bookmaker odds are unavailable. Production use requires odds-aware walk-forward validation and market-specific calibration.",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    def line_score_summary(section):
        def scores(lines):
            return [
                {
                    "line": item["line"],
                    "over": {
                        "brierScore": item["over"]["brierScore"],
                        "logLoss": item["over"]["logLoss"],
                        "eventCount": item["over"]["eventCount"],
                    },
                    "under": {
                        "brierScore": item["under"]["brierScore"],
                        "logLoss": item["under"]["logLoss"],
                        "eventCount": item["under"]["eventCount"],
                    },
                }
                for item in lines
            ]

        nb = section.get("negativeBinomial", {})
        return {
            "poisson": scores(section.get("totalLines", [])),
            "negativeBinomial": scores(nb.get("totalLines", [])),
            "negativeBinomialAlpha": nb.get("alpha"),
        }

    print(json.dumps({
        "generatedAt": report["generatedAt"],
        "datasetRows": report["datasetRows"],
        "evaluatedLines": report["evaluatedLines"],
        "corners": {
            "pointMetrics": report["corners"].get("metrics", report["corners"]),
            "lineProbabilityScores": line_score_summary(report["corners"]),
            "distributionComparison": report["corners"].get("distributionComparison", {}),
            "byLeague": report["corners"].get("byLeague", {}),
            "bySeason": report["corners"].get("bySeason", {}),
        },
        "yellowCards": {
            "pointMetrics": report["yellowCards"].get("metrics", report["yellowCards"]),
            "lineProbabilityScores": line_score_summary(report["yellowCards"]),
            "distributionComparison": report["yellowCards"].get("distributionComparison", {}),
            "byLeague": report["yellowCards"].get("byLeague", {}),
            "bySeason": report["yellowCards"].get("bySeason", {}),
        },
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
