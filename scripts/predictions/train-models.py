#!/usr/bin/env python3
"""Train Poisson baselines and evaluate total-goal-style market lines/calibration."""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import numpy as np
from scipy.stats import poisson
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

def evaluate_lines(y, prediction):
    results = []
    for line in LINES:
        # For half-point lines there is no push: over means integer total > line.
        over_actual = (y > line).astype(float)
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
        "method": "Poisson regression with chronological holdout; half-point totals evaluated as Poisson tail probabilities",
        "holdoutStart": HOLDOUT_START.isoformat(),
        "datasetRows": len(rows),
        "evaluatedLines": LINES,
        "corners": train_market(rows, "corners_total", "corners_model_eligible", "league_corners_avg"),
        "yellowCards": train_market(rows, "cards_total", "cards_model_eligible", "league_cards_avg"),
        "note": "Calibration bins and Brier/Log Loss are descriptive holdout diagnostics, not proof of betting value. No ROI is calculated because historical bookmaker odds are unavailable. Production use requires odds-aware walk-forward validation and market-specific calibration.",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "generatedAt": report["generatedAt"],
        "datasetRows": report["datasetRows"],
        "evaluatedLines": report["evaluatedLines"],
        "corners": report["corners"].get("metrics", report["corners"]),
        "yellowCards": report["yellowCards"].get("metrics", report["yellowCards"]),
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
