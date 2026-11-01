#!/usr/bin/env python3
"""Train independent home/away goal models and export derived football markets.

All features are rolling histories available strictly before the fixture date.
The chronological holdout is reported separately from the final all-history fit.
"""
from __future__ import annotations
import csv, json
from datetime import date
from pathlib import Path
import numpy as np
from scipy.stats import poisson
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "corners_cards_training.csv"
MODEL_OUT = ROOT / "data" / "goals_markets_models.json"
REPORT_OUT = ROOT / "data" / "goals_markets_report.json"
AUDIT = ROOT / "data" / "corners_cards_audit.json"
HOLDOUT_START = date(2024, 7, 1)
FEATURES = [
    "home_goal_team_for_avg5", "home_goal_team_against_avg5",
    "home_goal_team_for_avg10", "home_goal_team_against_avg10",
    "away_goal_team_for_avg5", "away_goal_team_against_avg5",
    "away_goal_team_for_avg10", "away_goal_team_against_avg10",
    "home_shots_team_for_avg5", "home_shots_team_against_avg5",
    "home_shots_team_for_avg10", "home_shots_team_against_avg10",
    "away_shots_team_for_avg5", "away_shots_team_against_avg5",
    "away_shots_team_for_avg10", "away_shots_team_against_avg10",
    "home_shots_on_target_team_for_avg5", "home_shots_on_target_team_against_avg5",
    "home_shots_on_target_team_for_avg10", "home_shots_on_target_team_against_avg10",
    "away_shots_on_target_team_for_avg5", "away_shots_on_target_team_against_avg5",
    "away_shots_on_target_team_for_avg10", "away_shots_on_target_team_against_avg10",
    "home_team_for_avg10", "home_team_against_avg10",
    "away_team_for_avg10", "away_team_against_avg10",
    "home_foul_team_for_avg10", "home_foul_team_against_avg10",
    "away_foul_team_for_avg10", "away_foul_team_against_avg10",
    "league_goals_avg", "league_shots_avg", "league_shots_on_target_avg",
    "league_corners_avg", "league_fouls_avg", "home_advantage",
]
ALPHA = 0.5

def matrix(rows):
    x = np.asarray([[float(r[k]) if r.get(k) not in ("", None) else np.nan for k in FEATURES] for r in rows], dtype=float)
    return x

def fit(rows, target):
    x = matrix(rows)
    y = np.asarray([float(r[target]) for r in rows], dtype=float)
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    model = PoissonRegressor(alpha=ALPHA, max_iter=1000)
    xi = imputer.fit_transform(x)
    xs = scaler.fit_transform(xi)
    model.fit(xs, y)
    return {
        "type": "poisson", "features": FEATURES,
        "imputerMedian": imputer.statistics_.tolist(),
        "scalerMean": scaler.mean_.tolist(), "scalerScale": scaler.scale_.tolist(),
        "coefficients": model.coef_.tolist(), "intercept": float(model.intercept_),
        "trainingRows": len(rows), "trainingThrough": max(r["date"] for r in rows),
    }, model, imputer, scaler

def predict(rows, model, imputer, scaler):
    return model.predict(scaler.transform(imputer.transform(matrix(rows))))

def deviance(y, mu):
    mu = np.maximum(mu, 1e-9)
    term = np.where(y > 0, y * np.log(np.maximum(y, 1e-9) / mu) - (y - mu), mu)
    return float(2 * np.mean(term))

def metrics(y, pred, baseline_mean):
    baseline = np.full(len(y), float(baseline_mean), dtype=float)
    model_mae = float(np.mean(np.abs(y - pred)))
    baseline_mae = float(np.mean(np.abs(y - baseline)))
    model_deviance = deviance(y, pred)
    baseline_deviance = deviance(y, baseline)
    return {
        "rows": int(len(y)),
        "actualMean": round(float(np.mean(y)), 4),
        "predictedMean": round(float(np.mean(pred)), 4),
        "mae": round(model_mae, 5),
        "baselineMae": round(baseline_mae, 5),
        "maeImprovement": round(baseline_mae - model_mae, 5),
        "maeImprovementPct": round((baseline_mae - model_mae) / baseline_mae * 100, 3) if baseline_mae else 0,
        "poissonDeviance": round(model_deviance, 5),
        "baselinePoissonDeviance": round(baseline_deviance, 5),
        "devianceImprovement": round(baseline_deviance - model_deviance, 5),
        "devianceImprovementPct": round((baseline_deviance - model_deviance) / baseline_deviance * 100, 3) if baseline_deviance else 0,
        "baselineMean": round(float(baseline_mean), 5),
        "baselineReference": "constant mean of the training partition; no holdout information used",
    }

def poisson_score_probabilities(home_mean, away_mean):
    """Return market probabilities from a normalized independent-Poisson score grid."""
    goals = np.arange(41)
    home_pmf = poisson.pmf(goals, max(float(home_mean), 1e-9))
    away_pmf = poisson.pmf(goals, max(float(away_mean), 1e-9))
    home_pmf /= home_pmf.sum()
    away_pmf /= away_pmf.sum()
    matrix = np.outer(home_pmf, away_pmf)
    home_goals, away_goals = np.indices(matrix.shape)
    p_home = float(matrix[home_goals > away_goals].sum())
    p_draw = float(matrix[home_goals == away_goals].sum())
    p_away = float(matrix[home_goals < away_goals].sum())
    return {
        "homePmf": home_pmf, "awayPmf": away_pmf, "matrix": matrix,
        "home": p_home, "draw": p_draw, "away": p_away,
        "btts": float(matrix[1:, 1:].sum()),
        "totalOver": {line: float(matrix[home_goals + away_goals > line].sum())
                      for line in (0.5, 1.5, 2.5, 3.5, 4.5)},
        "homeOver": {line: float(home_pmf[goals > line].sum())
                     for line in (0.5, 1.5, 2.5, 3.5)},
        "awayOver": {line: float(away_pmf[goals > line].sum())
                     for line in (0.5, 1.5, 2.5, 3.5)},
        "asian": {
            line: {
                "win": float(matrix[home_goals - away_goals + line > 0].sum()),
                "push": float(matrix[home_goals - away_goals + line == 0].sum()),
                "lose": float(matrix[home_goals - away_goals + line < 0].sum()),
            }
            for line in (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5)
        },
    }


def binary_probability_metrics(actual, probabilities, baseline_actual):
    actual = np.asarray(actual, dtype=float)
    probabilities = np.clip(np.asarray(probabilities, dtype=float), 1e-9, 1 - 1e-9)
    baseline_actual = np.asarray(baseline_actual, dtype=float)
    baseline_probability = float(np.mean(baseline_actual))
    baseline_probability = float(np.clip(baseline_probability, 1e-9, 1 - 1e-9))
    brier = float(np.mean((probabilities - actual) ** 2))
    baseline_brier = float(np.mean((baseline_probability - actual) ** 2))
    log_loss = float(-np.mean(actual * np.log(probabilities) + (1 - actual) * np.log(1 - probabilities)))
    baseline_log_loss = float(-np.mean(actual * np.log(baseline_probability) + (1 - actual) * np.log(1 - baseline_probability)))
    bins = []
    ece = 0.0
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        mask = (probabilities >= low) & ((probabilities < high) if index < 9 else (probabilities <= high))
        if not np.any(mask):
            continue
        mean_predicted = float(np.mean(probabilities[mask]))
        observed = float(np.mean(actual[mask]))
        weight = float(np.mean(mask))
        ece += weight * abs(mean_predicted - observed)
        bins.append({"range": f"{low:.1f}-{high:.1f}", "rows": int(np.sum(mask)),
                     "predicted": round(mean_predicted, 4), "observed": round(observed, 4)})
    return {
        "rows": int(len(actual)), "baselineProbability": round(baseline_probability, 5),
        "brier": round(brier, 5), "baselineBrier": round(baseline_brier, 5),
        "brierSkill": round(1 - brier / baseline_brier, 5) if baseline_brier else None,
        "logLoss": round(log_loss, 5), "baselineLogLoss": round(baseline_log_loss, 5),
        "logLossImprovement": round(baseline_log_loss - log_loss, 5),
        "expectedCalibrationError10": round(float(ece), 5), "reliabilityBins": bins,
    }


def multiclass_probability_metrics(actual_classes, probabilities, baseline_classes):
    actual_classes = np.asarray(actual_classes, dtype=int)
    probabilities = np.clip(np.asarray(probabilities, dtype=float), 1e-9, 1)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    baseline_classes = np.asarray(baseline_classes, dtype=int)
    baseline = np.bincount(baseline_classes, minlength=3).astype(float)
    baseline /= baseline.sum()
    one_hot = np.eye(3)[actual_classes]
    brier = float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))
    baseline_matrix = np.tile(baseline, (len(actual_classes), 1))
    baseline_brier = float(np.mean(np.sum((baseline_matrix - one_hot) ** 2, axis=1)))
    log_loss = float(-np.mean(np.log(probabilities[np.arange(len(actual_classes)), actual_classes])))
    baseline_log_loss = float(-np.mean(np.log(baseline[actual_classes])))
    return {
        "rows": int(len(actual_classes)), "baselineProbabilities": [round(float(x), 5) for x in baseline],
        "brier": round(brier, 5), "baselineBrier": round(baseline_brier, 5),
        "brierSkill": round(1 - brier / baseline_brier, 5) if baseline_brier else None,
        "logLoss": round(log_loss, 5), "baselineLogLoss": round(baseline_log_loss, 5),
        "logLossImprovement": round(baseline_log_loss - log_loss, 5),
    }


def evaluate_probability_markets(train_rows, test_rows, home_predictions, away_predictions):
    """Chronological holdout scoring; all constant baselines use training outcomes only."""
    train_home = np.asarray([int(float(r["target_home_goals"])) for r in train_rows])
    train_away = np.asarray([int(float(r["target_away_goals"])) for r in train_rows])
    test_home = np.asarray([int(float(r["target_home_goals"])) for r in test_rows])
    test_away = np.asarray([int(float(r["target_away_goals"])) for r in test_rows])
    train_classes = np.where(train_home > train_away, 0, np.where(train_home == train_away, 1, 2))
    test_classes = np.where(test_home > test_away, 0, np.where(test_home == test_away, 1, 2))
    predictions = [poisson_score_probabilities(h, a) for h, a in zip(home_predictions, away_predictions)]
    outcome_probs = np.asarray([[p["home"], p["draw"], p["away"]] for p in predictions])
    result = {"1X2": multiclass_probability_metrics(test_classes, outcome_probs, train_classes)}

    match_total_train = train_home + train_away
    match_total_test = test_home + test_away
    home_train_decisive = train_home != train_away
    home_test_decisive = test_home != test_away
    result["matchTotals"] = {}
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        result["matchTotals"][str(line)] = binary_probability_metrics(
            match_total_test > line, [p["totalOver"][line] for p in predictions], match_total_train > line)
    result["homeTeamTotals"] = {}
    result["awayTeamTotals"] = {}
    for line in (0.5, 1.5, 2.5, 3.5):
        result["homeTeamTotals"][str(line)] = binary_probability_metrics(
            test_home > line, [p["homeOver"][line] for p in predictions], train_home > line)
        result["awayTeamTotals"][str(line)] = binary_probability_metrics(
            test_away > line, [p["awayOver"][line] for p in predictions], train_away > line)
    result["bothTeamsToScore"] = binary_probability_metrics(
        (test_home > 0) & (test_away > 0), [p["btts"] for p in predictions],
        (train_home > 0) & (train_away > 0))

    result["drawNoBet"] = {}
    for side, train_margin, test_margin in (
        ("home", train_home - train_away, test_home - test_away),
        ("away", train_away - train_home, test_away - test_home),
    ):
        train_decisive = train_margin != 0
        test_decisive = test_margin != 0
        probs = [p[side] / (p["home"] + p["away"]) for p in predictions]
        result["drawNoBet"][side] = binary_probability_metrics(
            test_margin[test_decisive] > 0,
            np.asarray(probs)[test_decisive],
            train_margin[train_decisive] > 0)
        result["drawNoBet"][side]["coverageRows"] = int(np.sum(test_decisive))
        result["drawNoBet"][side]["pushRate"] = round(float(np.mean(~test_decisive)), 5)

    result["asianHandicapHome"] = {}
    for line in (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5):
        train_result = train_home - train_away + line
        test_result = test_home - test_away + line
        train_decisive = train_result != 0
        test_decisive = test_result != 0
        conditional_probs = [
            p["asian"][line]["win"] / max(1e-12, p["asian"][line]["win"] + p["asian"][line]["lose"])
            for p in predictions
        ]
        result["asianHandicapHome"][str(line)] = binary_probability_metrics(
            test_result[test_decisive] > 0,
            np.asarray(conditional_probs)[test_decisive],
            train_result[train_decisive] > 0)
        result["asianHandicapHome"][str(line)]["coverageRows"] = int(np.sum(test_decisive))
        result["asianHandicapHome"][str(line)]["pushRate"] = round(float(np.mean(~test_decisive)), 5)
    return result

def main():
    with DATA.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("target_home_goals") not in ("", None) and r.get("target_away_goals") not in ("", None)]
    if len(rows) < 5000:
        raise SystemExit(f"Not enough goal rows: {len(rows)}")
    rows.sort(key=lambda r: r["date"])
    train = [r for r in rows if date.fromisoformat(r["date"]) < HOLDOUT_START]
    test = [r for r in rows if date.fromisoformat(r["date"]) >= HOLDOUT_START]
    if len(train) < 3000 or len(test) < 500:
        raise SystemExit(f"Insufficient chronological split: train={len(train)}, test={len(test)}")
    report = {"generatedAt": date.today().isoformat(), "datasetRows": len(rows),
              "holdoutStart": HOLDOUT_START.isoformat(), "method": "Chronological holdout; no random split; features are prior-match rolling histories.",
              "markets": {}, "status": "experimental_research_model"}
    models = {}
    holdout_predictions = {}
    for side, target in (("homeGoals", "target_home_goals"), ("awayGoals", "target_away_goals")):
        _, tm, imp, sc = fit(train, target)
        y = np.asarray([float(r[target]) for r in test])
        train_mean = float(np.mean([float(r[target]) for r in train]))
        pred = predict(test, tm, imp, sc)
        holdout_predictions[side] = pred
        report["markets"][side] = metrics(y, pred, train_mean)
        exported, _, _, _ = fit(rows, target)
        models[side] = exported
    report["probabilityMarkets"] = evaluate_probability_markets(
        train, test, holdout_predictions["homeGoals"], holdout_predictions["awayGoals"])
    latest_team, latest_league = {}, {}
    team_feature_names = [
        "goal_team_for_avg5", "goal_team_against_avg5", "goal_team_for_avg10", "goal_team_against_avg10",
        "team_for_avg5", "team_against_avg5", "team_for_avg10", "team_against_avg10",
        "card_team_for_avg5", "card_team_against_avg5", "card_team_for_avg10", "card_team_against_avg10",
        "foul_team_for_avg5", "foul_team_against_avg5", "foul_team_for_avg10", "foul_team_against_avg10",
        "shots_team_for_avg5", "shots_team_against_avg5", "shots_team_for_avg10", "shots_team_against_avg10",
        "shots_on_target_team_for_avg5", "shots_on_target_team_against_avg5",
        "shots_on_target_team_for_avg10", "shots_on_target_team_against_avg10",
    ]
    for row in rows:
        league = row.get("league", "")
        latest_league[league] = {
            k: (float(row[k]) if row.get(k) not in ("", None) else None)
            for k in ("league_goals_avg", "league_corners_avg", "league_cards_avg",
                      "league_fouls_avg", "league_shots_avg", "league_shots_on_target_avg")
        }
        for side, team_key in (("home", "home_team"), ("away", "away_team")):
            team = row.get(team_key, "").strip()
            if not team:
                continue
            latest_team[(league, team)] = {
                "name": team,
                "stats": {
                    name: (float(row[f"{side}_{name}"]) if row.get(f"{side}_{name}") not in ("", None) else None)
                    for name in team_feature_names
                },
                "matchesBefore": int(float(row.get(f"{side}_matches_before") or 0)),
            }
    teams = {}
    for (league, team), value in latest_team.items():
        teams.setdefault(league, {})[team] = value
    league_names = {}
    if AUDIT.exists():
        audit = json.loads(AUDIT.read_text(encoding="utf-8"))
        for item in audit.get("sources", {}).get("footballData", {}).get("leagues", []):
            if item.get("code") and item.get("league"):
                league_names[str(item["code"])] = str(item["league"])
    aliases = {name.strip().casefold(): code for code, name in league_names.items()}
    aliases.update({
        "premier league":"E0", "championship":"E1", "league one":"E2", "league two":"E3",
        "scottish premiership":"SC0", "bundesliga":"D1", "2 bundesliga":"D2",
        "serie a":"I1", "serie b":"I2", "la liga":"SP1", "segunda division":"SP2",
        "ligue 1":"F1", "ligue 2":"F2", "eredivisie":"N1", "pro league":"B1",
        "super lig":"T1",
    })
    payload = {
      "schemaVersion": 1, "generatedAt": date.today().isoformat(),
      "status": "experimental_research_model",
      "disclaimer": "Independent statistical estimates; no bookmaker probabilities or picks are imported. Red-card forecasts and exact-score forecasts are intentionally excluded.",
      "models": models, "features": FEATURES, "teams": teams,
      "leagueNames": league_names, "leagueAliases": aliases, "leagueStats": latest_league,
      "scoreGridMax": 10,
      "markets": ["1X2","doubleChance","drawNoBet","matchTotals","teamTotals","bothTeamsToScore","asianHandicap"],
      "trainingRows": len(rows), "trainingThrough": max(r["date"] for r in rows),
      "method": "Independent Poisson models for home and away goals; scoreline probabilities derived from their joint score matrix."
    }
    MODEL_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    REPORT_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Exported {MODEL_OUT}")

if __name__ == "__main__":
    main()
