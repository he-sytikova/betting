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


def line_probabilities(actual, predicted_mean, alpha, lines):
    actual = np.asarray(actual, dtype=float)
    mean = np.maximum(np.asarray(predicted_mean, dtype=float), 1e-8)
    result = {}
    for line in lines:
        event = (actual > line).astype(float)
        p_poisson = BASELINE.poisson.sf(int(line), mean)
        p_nb = BASELINE.negative_binomial_sf(line, mean, alpha)
        result[str(line)] = {
            "actual": event,
            "poisson": p_poisson,
            "negativeBinomial": p_nb,
        }
    return result


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
    probabilities = line_probabilities(actual, predicted_mean, alpha, lines)
    for line in lines:
        values = probabilities[str(line)]
        result["lines"].append({
            "line": line,
            "poisson": BASELINE.probability_metrics(values["actual"], values["poisson"]),
            "negativeBinomial": BASELINE.probability_metrics(values["actual"], values["negativeBinomial"]),
            "poissonCalibration": BASELINE.calibration_diagnostics(values["actual"], values["poisson"]),
            "negativeBinomialCalibration": BASELINE.calibration_diagnostics(values["actual"], values["negativeBinomial"]),
            "observedOverRate": round(float(np.mean(values["actual"])), 5),
        })
    return result


def compare_probability_models(actual, baseline_probabilities, candidate_probabilities, lines):
    output = {}
    for distribution in ("poisson", "negativeBinomial"):
        rows = []
        brier_p_values = []
        logloss_p_values = []
        for index, line in enumerate(lines):
            baseline_p = baseline_probabilities[str(line)][distribution]
            candidate_p = candidate_probabilities[str(line)][distribution]
            event = baseline_probabilities[str(line)]["actual"]
            bootstrap = BASELINE.paired_bootstrap_ci(
                event, baseline_p, candidate_p, seed=20264031 + index, samples=1000
            )
            brier_p = BASELINE.paired_permutation_pvalue(
                event, baseline_p, candidate_p, "brier",
                seed=20265031 + index, samples=999,
            )
            logloss_p = BASELINE.paired_permutation_pvalue(
                event, baseline_p, candidate_p, "logloss",
                seed=20266031 + index, samples=999,
            )
            brier_p_values.append(brier_p)
            logloss_p_values.append(logloss_p)
            rows.append({
                "line": line,
                "baselineBrier": round(BASELINE.probability_metrics(event, baseline_p)["brierScore"], 6),
                "candidateBrier": round(BASELINE.probability_metrics(event, candidate_p)["brierScore"], 6),
                "baselineLogLoss": round(BASELINE.probability_metrics(event, baseline_p)["logLoss"], 6),
                "candidateLogLoss": round(BASELINE.probability_metrics(event, candidate_p)["logLoss"], 6),
                **bootstrap,
                "brierPermutationP": round(float(brier_p), 6),
                "logLossPermutationP": round(float(logloss_p), 6),
            })
        adjusted_brier = BASELINE.holm_adjust(brier_p_values)
        adjusted_logloss = BASELINE.holm_adjust(logloss_p_values)
        for index, row in enumerate(rows):
            row["brierHolmAdjustedP"] = round(adjusted_brier[index], 6)
            row["logLossHolmAdjustedP"] = round(adjusted_logloss[index], 6)
        output[distribution] = rows
    return output


def run_market(rows, market_name, target, eligible_flag, lines):
    usable = [
        row for row in rows
        if row.get(eligible_flag) == "1" and row.get(target, "") != ""
    ]
    pooled = {
        "poissonBaseline": {"actual": [], "mean": [], "alphas": [], "probabilities": {"poisson": {}, "negativeBinomial": {}}, "folds": []},
        "catboost": {"actual": [], "mean": [], "alphas": [], "probabilities": {"poisson": {}, "negativeBinomial": {}}, "folds": []},
    }
    for model_data in pooled.values():
        for distribution in ("poisson", "negativeBinomial"):
            model_data["probabilities"][distribution] = {str(line): [] for line in lines}

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
            values = pooled[key]
            values["actual"].extend(y_test.tolist())
            values["mean"].extend(mean.tolist())
            values["alphas"].append(alpha)
            values["folds"].append(fold_start.isoformat())
            probabilities = line_probabilities(y_test, mean, alpha, lines)
            for line in lines:
                line_key = str(line)
                values["probabilities"]["poisson"][line_key].extend(
                    probabilities[line_key]["poisson"].tolist()
                )
                values["probabilities"]["negativeBinomial"][line_key].extend(
                    probabilities[line_key]["negativeBinomial"].tolist()
                )

    pooled_reports = {}
    for key, values in pooled.items():
        if not values["actual"]:
            pooled_reports[key] = {"status": "insufficient_data"}
            continue
        actual = np.asarray(values["actual"], dtype=float)
        mean = np.asarray(values["mean"], dtype=float)
        summary = model_scores(actual, mean, float(np.mean(values["alphas"])), lines)
        summary["evaluatedFolds"] = len(values["folds"])
        summary["folds"] = values["folds"]
        # Re-score probability distributions using each fold's training-only alpha.
        for line_result in summary["lines"]:
            line_key = str(line_result["line"])
            event = (actual > line_result["line"]).astype(float)
            for distribution in ("poisson", "negativeBinomial"):
                probability = np.asarray(values["probabilities"][distribution][line_key], dtype=float)
                line_result[distribution] = BASELINE.probability_metrics(event, probability)
                line_result[f"{distribution}Calibration"] = BASELINE.calibration_diagnostics(event, probability)
        pooled_reports[key] = summary

    if pooled["poissonBaseline"]["actual"] and pooled["catboost"]["actual"]:
        actual = np.asarray(pooled["poissonBaseline"]["actual"], dtype=float)
        baseline_mean = np.asarray(pooled["poissonBaseline"]["mean"], dtype=float)
        candidate_mean = np.asarray(pooled["catboost"]["mean"], dtype=float)
        baseline_probabilities = pooled["poissonBaseline"]["probabilities"]
        candidate_probabilities = pooled["catboost"]["probabilities"]
        candidate_comparison = {
            "maeDeltaCatBoostMinusPoisson": round(
                float(np.mean(np.abs(actual - candidate_mean)) - np.mean(np.abs(actual - baseline_mean))), 7
            ),
            "poissonDevianceDeltaCatBoostMinusPoisson": round(
                BASELINE.poisson_deviance(actual, candidate_mean)
                - BASELINE.poisson_deviance(actual, baseline_mean), 7
            ),
            "probabilityTests": compare_probability_models(
                actual, baseline_probabilities, candidate_probabilities, lines
            ),
            "interpretation": "Negative loss deltas favor CatBoost. Holm-adjusted p below 0.05 is evidence of a difference across the six market lines; confidence intervals crossing zero indicate uncertainty.",
        }
    else:
        candidate_comparison = {"status": "insufficient_data"}

    return {
        "status": "evaluated" if any(f["status"] == "evaluated" for f in fold_reports) else "insufficient_data",
        "target": target,
        "lines": lines,
        "features": BASELINE.FEATURES,
        "categoricalFeatures": ["league"],
        "folds": fold_reports,
        "pooled": pooled_reports,
        "candidateVsBaseline": candidate_comparison,
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
        "method": "CatBoost Poisson objective with league categorical feature; same chronological walk-forward folds and numeric feature set as current Poisson baseline. Dispersion is estimated on training rows only; pooled NB2 probabilities retain fold-specific training-only dispersion estimates.",
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
