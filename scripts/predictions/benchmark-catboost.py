#!/usr/bin/env python3
"""Research-only CatBoost benchmark against the current Poisson mean model.

Uses the existing leakage-safe training CSV and chronological walk-forward folds.
It never exports or changes browser production models.
"""
from __future__ import annotations

import csv
import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from catboost import CatBoostRegressor

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "corners_cards_training.csv"
OUT = ROOT / "data" / "corners_cards_candidate_report.json"
BASELINE_PATH = Path(__file__).with_name("train-models.py")

SPEC = importlib.util.spec_from_file_location("current_train_models", BASELINE_PATH)
BASELINE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BASELINE)

FOLDS = [
    (date(2023, 7, 1), date(2024, 7, 1)),
    (date(2024, 7, 1), date(2025, 7, 1)),
    (date(2025, 7, 1), date(2026, 7, 1)),
]
MARKETS = {
    "corners": ("corners_total", "corners_model_eligible", BASELINE.CORNERS_LINES),
    "yellowCards": ("cards_total", "cards_model_eligible", BASELINE.YELLOW_CARD_LINES),
}


def numeric(row, key):
    value = row.get(key, "")
    try:
        return float(value) if value not in ("", None) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def numeric_matrix(rows):
    return np.asarray(
        [[numeric(row, feature) for feature in BASELINE.FEATURES] for row in rows],
        dtype=float,
    )


def catboost_matrix(rows):
    # League is the only categorical feature in this first controlled benchmark.
    return [
        [numeric(row, feature) for feature in BASELINE.FEATURES]
        + [str(row.get("league") or "unknown")]
        for row in rows
    ]


def estimate_alpha(actual, predicted_mean):
    predicted_mean = np.maximum(np.asarray(predicted_mean, dtype=float), 1e-8)
    actual = np.asarray(actual, dtype=float)
    numerator = np.sum((actual - predicted_mean) ** 2 - predicted_mean)
    denominator = np.sum(predicted_mean ** 2)
    return max(float(numerator / denominator), 1e-6)


def make_catboost():
    return CatBoostRegressor(
        loss_function="Poisson",
        eval_metric="Poisson",
        iterations=650,
        depth=6,
        learning_rate=0.035,
        l2_leaf_reg=5.0,
        random_seed=20261031,
        random_strength=0.5,
        verbose=False,
        allow_writing_files=False,
        thread_count=4,
    )


def predict_catboost(model, features):
    # CatBoost's Poisson objective uses a log link; Exponent returns the mean.
    return np.maximum(
        np.asarray(model.predict(features, prediction_type="Exponent"), dtype=float),
        1e-8,
    )


def model_scores(actual, predicted_mean, alpha, lines):
    actual = np.asarray(actual, dtype=float)
    predicted_mean = np.maximum(np.asarray(predicted_mean, dtype=float), 1e-8)
    result = {
        "matches": int(len(actual)),
        "mae": round(float(np.mean(np.abs(actual - predicted_mean))), 6),
        "poissonDeviance": round(BASELINE.poisson_deviance(actual, predicted_mean), 6),
        "actualMean": round(float(np.mean(actual)), 5),
        "predictedMean": round(float(np.mean(predicted_mean)), 5),
        "negativeBinomialAlpha": round(float(alpha), 8),
        "lines": [],
    }
    for line in lines:
        event = (actual > line).astype(float)
        p_poisson = BASELINE.poisson.sf(int(line), predicted_mean)
        p_nb = BASELINE.negative_binomial_sf(line, predicted_mean, alpha)
        result["lines"].append({
            "line": line,
            "poisson": BASELINE.probability_metrics(event, p_poisson),
            "negativeBinomial": BASELINE.probability_metrics(event, p_nb),
            "observedOverRate": round(float(np.mean(event)), 5),
        })
    return result


def run_market(rows, market_name, target, eligible_flag, lines):
    usable = [
        row for row in rows
        if row.get(eligible_flag) == "1" and row.get(target, "") != ""
    ]
    pooled = {
        "poissonBaseline": {"actual": [], "mean": [], "alpha": [], "folds": []},
        "catboost": {"actual": [], "mean": [], "alpha": [], "folds": []},
    }
    fold_reports = []

    for fold_start, fold_end in FOLDS:
        train = [r for r in usable if date.fromisoformat(r["date"]) < fold_start]
        test = [
            r for r in usable
            if fold_start <= date.fromisoformat(r["date"]) < fold_end
        ]
        if len(train) < 1000 or len(test) < 100:
            fold_reports.append({
                "seasonStart": fold_start.isoformat(),
                "seasonEndExclusive": fold_end.isoformat(),
                "status": "skipped_insufficient_data",
                "trainRows": len(train),
                "testRows": len(test),
            })
            continue

        y_train = np.asarray([float(r[target]) for r in train], dtype=float)
        y_test = np.asarray([float(r[target]) for r in test], dtype=float)

        x_train = numeric_matrix(train)
        x_test = numeric_matrix(test)
        baseline_model = BASELINE.make_poisson_pipeline()
        baseline_model.fit(x_train, y_train)
        baseline_train_mean = np.maximum(baseline_model.predict(x_train), 1e-8)
        baseline_test_mean = np.maximum(baseline_model.predict(x_test), 1e-8)
        baseline_alpha = estimate_alpha(y_train, baseline_train_mean)

        cat_train = catboost_matrix(train)
        cat_test = catboost_matrix(test)
        cat_model = make_catboost()
        cat_model.fit(cat_train, y_train, cat_features=[len(BASELINE.FEATURES)])
        cat_train_mean = predict_catboost(cat_model, cat_train)
        cat_test_mean = predict_catboost(cat_model, cat_test)
        cat_alpha = estimate_alpha(y_train, cat_train_mean)

        fold_reports.append({
            "seasonStart": fold_start.isoformat(),
            "seasonEndExclusive": fold_end.isoformat(),
            "status": "evaluated",
            "trainRows": len(train),
            "testRows": len(test),
            "trainThrough": max(r["date"] for r in train),
            "testFrom": min(r["date"] for r in test),
            "testThrough": max(r["date"] for r in test),
            "poissonBaseline": model_scores(y_test, baseline_test_mean, baseline_alpha, lines),
            "catboost": model_scores(y_test, cat_test_mean, cat_alpha, lines),
        })

        for key, mean, alpha in (
            ("poissonBaseline", baseline_test_mean, baseline_alpha),
            ("catboost", cat_test_mean, cat_alpha),
        ):
            pooled[key]["actual"].extend(y_test.tolist())
            pooled[key]["mean"].extend(mean.tolist())
            pooled[key]["alpha"].append(alpha)
            pooled[key]["folds"].append(fold_start.isoformat())

    pooled_reports = {}
    for key, values in pooled.items():
        if not values["actual"]:
            pooled_reports[key] = {"status": "insufficient_data"}
            continue
        # Fold-specific dispersion is retained in fold reports. The pooled
        # NB2 score uses the row-weighted mean alpha as a descriptive summary.
        pooled_reports[key] = model_scores(
            values["actual"],
            values["mean"],
            float(np.mean(values["alpha"])),
            lines,
        )
        pooled_reports[key]["evaluatedFolds"] = len(values["folds"])
        pooled_reports[key]["folds"] = values["folds"]

    return {
        "status": "evaluated" if any(f["status"] == "evaluated" for f in fold_reports) else "insufficient_data",
        "target": target,
        "lines": lines,
        "features": BASELINE.FEATURES,
        "categoricalFeatures": ["league"],
        "folds": fold_reports,
        "pooled": pooled_reports,
        "interpretation": "Research-only chronological benchmark. Compare CatBoost with the same-fold Poisson baseline; do not export this candidate to public predictions unless it improves held-out probability and count metrics and passes calibration review.",
    }


def main():
    if not DATA.exists():
        raise FileNotFoundError(f"Training dataset not found: {DATA}")
    with DATA.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "research_candidate_not_for_production",
        "datasetRows": len(rows),
        "method": "CatBoost Poisson objective with league categorical feature; same chronological walk-forward folds and numeric feature set as current Poisson baseline. Dispersion is estimated on training rows only.",
        "catboostParameters": {
            "lossFunction": "Poisson",
            "iterations": 650,
            "depth": 6,
            "learningRate": 0.035,
            "l2LeafReg": 5.0,
            "randomSeed": 20261031,
        },
        "markets": {
            name: run_market(rows, name, target, eligible, lines)
            for name, (target, eligible, lines) in MARKETS.items()
        },
        "productionImpact": "none; no browser model or public prediction file is written",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUT),
        "datasetRows": report["datasetRows"],
        "markets": {
            name: {
                "status": value["status"],
                "pooledRows": value.get("pooled", {}).get("catboost", {}).get("matches", 0),
                "catboostMae": value.get("pooled", {}).get("catboost", {}).get("mae"),
                "poissonMae": value.get("pooled", {}).get("poissonBaseline", {}).get("mae"),
            }
            for name, value in report["markets"].items()
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
