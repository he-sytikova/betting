#!/usr/bin/env python3
"""Train and chronologically evaluate Poisson regression baselines."""
from __future__ import annotations

import csv
import json
import math
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "corners_cards_training.csv"
OUT = ROOT / "data" / "corners_cards_model_report.json"
HOLDOUT_START = date(2024, 7, 1)
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
    terms = np.where(y > 0, y * np.log(y / pred) - (y - pred), pred)
    return float(2 * np.mean(terms))

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
    poisson = model.named_steps["poisson"]
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
            "improvesMaeOverBaseline": mae < base_mae
        },
        "model": {
            "type": "PoissonRegressor",
            "alpha": 0.5,
            "features": FEATURES,
            "imputerMedian": [float(v) for v in imputer.statistics_],
            "scalerMean": [float(v) for v in scaler.mean_],
            "scalerScale": [float(v) for v in scaler.scale_],
            "coefficients": [float(v) for v in poisson.coef_],
            "intercept": float(poisson.intercept_)
        }
    }

def main():
    if not DATA.exists():
        raise FileNotFoundError(f"Dataset not found: {DATA}")
    with DATA.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    report = {
        "generatedAt": date.today().isoformat(),
        "status": "backtest_only_not_for_public_predictions",
        "method": "Poisson regression with chronological holdout",
        "holdoutStart": HOLDOUT_START.isoformat(),
        "datasetRows": len(rows),
        "corners": train_market(rows, "corners_total", "corners_model_eligible", "league_corners_avg"),
        "yellowCards": train_market(rows, "cards_total", "cards_model_eligible", "league_cards_avg"),
        "note": "Model is not approved for production unless it beats the league-average baseline and passes calibration and market-line backtests."
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("corners", "yellowCards")}, ensure_ascii=False, indent=2))
    print("Corners:", report["corners"].get("metrics", report["corners"]))
    print("Yellow cards:", report["yellowCards"].get("metrics", report["yellowCards"]))

if __name__ == "__main__":
    main()
