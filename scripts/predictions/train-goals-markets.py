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
    for side, target in (("homeGoals", "target_home_goals"), ("awayGoals", "target_away_goals")):
        _, tm, imp, sc = fit(train, target)
        y = np.asarray([float(r[target]) for r in test])
        train_mean = float(np.mean([float(r[target]) for r in train]))
        pred = predict(test, tm, imp, sc)
        report["markets"][side] = metrics(y, pred, train_mean)
        exported, _, _, _ = fit(rows, target)
        models[side] = exported
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
