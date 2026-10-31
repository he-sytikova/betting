#!/usr/bin/env python3
"""Compare line-level probability calibration of feature models vs league-rate baselines."""
from __future__ import annotations

import csv
import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import poisson

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "corners_cards_training.csv"
OUT = ROOT / "data" / "corners_cards_probability_report.json"
BASELINE_PATH = Path(__file__).with_name("train-models.py")
SPEC = importlib.util.spec_from_file_location("training_diagnostics", BASELINE_PATH)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)

MARKETS = {
    "corners": ("corners_total", "corners_model_eligible", "league_corners_avg", BASE.CORNERS_LINES),
    "yellowCards": ("cards_total", "cards_model_eligible", "league_cards_avg", BASE.YELLOW_CARD_LINES),
}
FOLD_STARTS = [date(2023, 7, 1), date(2024, 7, 1), date(2025, 7, 1), date(2026, 7, 1)]


def estimate_alpha(actual, mean):
    actual = np.asarray(actual, dtype=float)
    mean = np.maximum(np.asarray(mean, dtype=float), 1e-8)
    return max(float(np.sum((actual - mean) ** 2 - mean) / np.sum(mean ** 2)), 1e-6)


def score_probability(actual, probability):
    actual = np.asarray(actual, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), 1e-8, 1 - 1e-8)
    cal = BASE.calibration_diagnostics(actual, p)
    return {
        "matches": int(len(actual)),
        "events": int(np.sum(actual)),
        "eventRate": round(float(np.mean(actual)), 6),
        "brierScore": round(float(np.mean((p - actual) ** 2)), 6),
        "logLoss": round(float(-np.mean(actual * np.log(p) + (1 - actual) * np.log(1 - p))), 6),
        "ece": cal["expectedCalibrationError"],
        "mce": cal["maximumCalibrationError"],
        "reliabilityBins": cal["bins"],
    }


def line_probability(mean, alpha, line, distribution):
    mean = np.maximum(np.asarray(mean, dtype=float), 1e-8)
    if distribution == "poisson":
        return poisson.sf(int(line), mean)
    return BASE.negative_binomial_sf(line, mean, alpha)


def compare(actual, candidate, baseline, seed, comparison_label):
    # paired_bootstrap_ci expects (Poisson, NB2); use baseline then candidate.
    boot = BASE.paired_bootstrap_ci(actual, baseline, candidate, seed=seed, samples=1000)
    brier_p = BASE.paired_permutation_pvalue(actual, baseline, candidate, "brier", seed=seed + 1000, samples=1999)
    log_p = BASE.paired_permutation_pvalue(actual, baseline, candidate, "logloss", seed=seed + 2000, samples=1999)
    return {
        "comparison": comparison_label,
        "brierDelta": boot["brierDeltaNbMinusPoisson"],
        "brierDelta95CI": boot["brierDelta95CI"],
        "logLossDelta": boot["logLossDeltaNbMinusPoisson"],
        "logLossDelta95CI": boot["logLossDelta95CI"],
        "note": "Negative favors the candidate; positive favors the baseline; CI crossing zero means no clear difference",
        "brierPermutationP": round(brier_p, 7),
        "logLossPermutationP": round(log_p, 7),
    }


def run_market(rows, market, target, eligible, league_key, lines):
    usable = [r for r in rows if r.get(eligible) == "1" and r.get(target, "") != ""]
    pooled = {
        model: {dist: {str(line): {"actual": [], "prob": []} for line in lines}
                for dist in ("poisson", "negativeBinomial")}
        for model in ("featureModel", "leagueBaseline")
    }
    folds = []
    for index, start in enumerate(FOLD_STARTS):
        end = FOLD_STARTS[index + 1] if index + 1 < len(FOLD_STARTS) else None
        train = [r for r in usable if date.fromisoformat(r["date"]) < start]
        test = [r for r in usable if date.fromisoformat(r["date"]) >= start and
                (end is None or date.fromisoformat(r["date"]) < end)]
        if len(train) < 1000 or len(test) < 100:
            folds.append({"seasonStart": start.isoformat(), "status": "skipped_insufficient_data",
                          "trainRows": len(train), "testRows": len(test)})
            continue
        x_train = np.asarray([[float(r.get(k) or "nan") for k in BASE.FEATURES] for r in train])
        x_test = np.asarray([[float(r.get(k) or "nan") for k in BASE.FEATURES] for r in test])
        y_train = np.asarray([float(r[target]) for r in train])
        y_test = np.asarray([float(r[target]) for r in test])
        model = BASE.make_poisson_pipeline()
        model.fit(x_train, y_train)
        feature_train_mean = np.maximum(model.predict(x_train), 1e-8)
        feature_test_mean = np.maximum(model.predict(x_test), 1e-8)
        league_train_mean = np.asarray([
            float(r[league_key]) if r.get(league_key) else float(np.mean(y_train)) for r in train
        ])
        league_test_mean = np.asarray([
            float(r[league_key]) if r.get(league_key) else float(np.mean(y_train)) for r in test
        ])
        league_train_mean = np.maximum(league_train_mean, 1e-8)
        league_test_mean = np.maximum(league_test_mean, 1e-8)
        alphas = {
            "featureModel": estimate_alpha(y_train, feature_train_mean),
            "leagueBaseline": estimate_alpha(y_train, league_train_mean),
        }
        means = {"featureModel": feature_test_mean, "leagueBaseline": league_test_mean}
        for model_name, mean in means.items():
            for dist in ("poisson", "negativeBinomial"):
                for line in lines:
                    key = str(line)
                    probability = line_probability(mean, alphas[model_name], line, dist)
                    pooled[model_name][dist][key]["actual"].extend((y_test > line).astype(float).tolist())
                    pooled[model_name][dist][key]["prob"].extend(probability.tolist())
        folds.append({
            "seasonStart": start.isoformat(), "seasonEndExclusive": end.isoformat() if end else None,
            "status": "evaluated", "trainRows": len(train), "testRows": len(test),
            "trainThrough": max(r["date"] for r in train), "testFrom": min(r["date"] for r in test),
            "testThrough": max(r["date"] for r in test),
            "featureModelAlpha": round(alphas["featureModel"], 8),
            "leagueBaselineAlpha": round(alphas["leagueBaseline"], 8),
        })

    evaluated = sum(f["status"] == "evaluated" for f in folds)
    if not evaluated:
        return {"status": "insufficient_data", "folds": folds}
    report = {"status": "evaluated", "target": target, "lines": lines, "folds": folds,
              "evaluatedFolds": evaluated, "distributions": {}}
    for dist in ("poisson", "negativeBinomial"):
        line_rows = []
        brier_p, log_p = [], []
        for i, line in enumerate(lines):
            key = str(line)
            b = pooled["leagueBaseline"][dist][key]
            c = pooled["featureModel"][dist][key]
            actual = np.asarray(c["actual"], dtype=float)
            base_p = np.asarray(b["prob"], dtype=float)
            cand_p = np.asarray(c["prob"], dtype=float)
            comparison = compare(actual, cand_p, base_p, 20261031 + i + (0 if dist == "poisson" else 500), "featureModel minus leagueBaseline")
            brier_p.append(comparison["brierPermutationP"])
            log_p.append(comparison["logLossPermutationP"])
            line_rows.append({
                "line": line,
                "over": {
                    "featureModel": score_probability(actual, cand_p),
                    "leagueBaseline": score_probability(actual, base_p),
                    "comparisonFeatureMinusBaseline": comparison,
                },
                "under": {
                    "featureModel": score_probability(1 - actual, 1 - cand_p),
                    "leagueBaseline": score_probability(1 - actual, 1 - base_p),
                },
            })
        brier_adj, log_adj = BASE.holm_adjust(brier_p), BASE.holm_adjust(log_p)
        for i, item in enumerate(line_rows):
            item["over"]["comparisonFeatureMinusBaseline"]["brierHolmAdjustedP"] = round(brier_adj[i], 7)
            item["over"]["comparisonFeatureMinusBaseline"]["logLossHolmAdjustedP"] = round(log_adj[i], 7)
        report["distributions"][dist] = line_rows

    # Direct paired test of NB2 against Poisson using the same feature-model mean.
    distribution_rows = []
    distribution_brier_p, distribution_log_p = [], []
    for i, line in enumerate(lines):
        key = str(line)
        actual = np.asarray(pooled["featureModel"]["poisson"][key]["actual"], dtype=float)
        poisson_probability = np.asarray(pooled["featureModel"]["poisson"][key]["prob"], dtype=float)
        nb_probability = np.asarray(pooled["featureModel"]["negativeBinomial"][key]["prob"], dtype=float)
        comparison = compare(actual, nb_probability, poisson_probability, 20262031 + i,
                             "NB2 minus Poisson (feature-model mean)")
        distribution_brier_p.append(comparison["brierPermutationP"])
        distribution_log_p.append(comparison["logLossPermutationP"])
        distribution_rows.append({"line": line, "NB2MinusPoisson": comparison})
    distribution_brier_adj = BASE.holm_adjust(distribution_brier_p)
    distribution_log_adj = BASE.holm_adjust(distribution_log_p)
    for i, item in enumerate(distribution_rows):
        item["NB2MinusPoisson"]["brierHolmAdjustedP"] = round(distribution_brier_adj[i], 7)
        item["NB2MinusPoisson"]["logLossHolmAdjustedP"] = round(distribution_log_adj[i], 7)
    report["distributionComparison"] = distribution_rows

    report["interpretation"] = (
        "Negative Brier/log-loss deltas favor the feature model. Holm-adjusted p<0.05 indicates "
        "evidence of a difference across the market's tested lines. Reliability bins and ECE/MCE "
        "describe calibration. This is not an ROI test; historical bookmaker odds are unavailable."
    )
    return report


def main():
    if not DATA.exists():
        raise FileNotFoundError(f"Training data not found: {DATA}")
    with DATA.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "research_only_probability_validation",
        "datasetRows": len(rows),
        "method": (
            "Expanding-window seasonal walk-forward; each fold fits the feature Poisson model and "
            "estimates dispersion using training rows only. The benchmark is the pre-match league "
            "rolling mean. Poisson and NB2 probabilities are compared on identical held-out matches. "
            "Paired bootstrap CIs and sign-flip permutation tests are reported; Holm correction is "
            "applied across lines separately for Brier and Log Loss."
        ),
        "markets": {
            name: run_market(rows, name, *config)
            for name, config in MARKETS.items()
        },
        "productionImpact": "none; research report only, no production model or public page is changed",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUT), "datasetRows": len(rows),
        "markets": {name: {
            "status": value["status"],
            "evaluatedFolds": value.get("evaluatedFolds", 0),
            "lines": {
                dist: [{
                    "line": item["line"],
                    "featureBrier": item["over"]["featureModel"]["brierScore"],
                    "baselineBrier": item["over"]["leagueBaseline"]["brierScore"],
                    "featureLogLoss": item["over"]["featureModel"]["logLoss"],
                    "baselineLogLoss": item["over"]["leagueBaseline"]["logLoss"],
                    "brierHolmP": item["over"]["comparisonFeatureMinusBaseline"]["brierHolmAdjustedP"],
                    "logLossHolmP": item["over"]["comparisonFeatureMinusBaseline"]["logLossHolmAdjustedP"],
                } for item in rows]
                for dist, rows in value.get("distributions", {}).items()
            },
        } for name, value in report["markets"].items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
