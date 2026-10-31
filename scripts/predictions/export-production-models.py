#!/usr/bin/env python3
"""Export selected Poisson models and latest pre-match team features for the browser."""
from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "corners_cards_training.csv"
AUDIT = ROOT / "data" / "corners_cards_audit.json"
OUT = ROOT / "data" / "corners_cards_models.json"
ALPHA = 0.5

CORNERS_FEATURES = [
    "home_team_for_avg10", "home_team_against_avg10",
    "away_team_for_avg10", "away_team_against_avg10",
    "league_corners_avg", "home_advantage",
]
# Selected reduced card feature set from the model review.
CARDS_FEATURES = [
    "home_team_for_avg5", "home_team_against_avg5",
    "home_team_for_avg10", "away_team_against_avg5",
    "away_team_for_avg10", "home_card_team_for_avg10",
    "home_card_team_against_avg10", "away_card_team_for_avg5",
    "away_card_team_against_avg5", "away_card_team_for_avg10",
    "away_card_team_against_avg10", "league_corners_avg",
    "league_cards_avg", "home_advantage",
]
LINES = [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]


def norm(value: str) -> str:
    value = (value or "").strip().casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def number(row, key):
    value = row.get(key, "")
    try:
        return float(value) if value not in ("", None) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def export_market(rows, target, eligible_flag, features):
    usable = [r for r in rows if r.get(eligible_flag) == "1" and r.get(target) not in ("", None)]
    if len(usable) < 1000:
        raise RuntimeError(f"Not enough rows to fit {target}: {len(usable)}")
    x = np.asarray([[number(r, key) for key in features] for r in usable], dtype=float)
    y = np.asarray([float(r[target]) for r in usable], dtype=float)
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    model = PoissonRegressor(alpha=ALPHA, max_iter=1000)
    x = imputer.fit_transform(x)
    x = scaler.fit_transform(x)
    model.fit(x, y)
    return {
        "type": "poisson",
        "features": features,
        "imputerMedian": [float(v) for v in imputer.statistics_],
        "scalerMean": [float(v) for v in scaler.mean_],
        "scalerScale": [float(v) for v in scaler.scale_],
        "coefficients": [float(v) for v in model.coef_],
        "intercept": float(model.intercept_),
        "trainingRows": len(usable),
        "trainingThrough": max(r["date"] for r in usable),
    }


def main():
    with DATA.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    latest_team = {}
    latest_league = {}
    for row in sorted(rows, key=lambda r: (r["date"], r["league"])):
        league = row.get("league", "").strip()
        if not league:
            continue
        latest_league[league] = row
        for side, team_key in (("home", "home_team"), ("away", "away_team")):
            team = row.get(team_key, "").strip()
            if not team:
                continue
            stats = {}
            prefix = "home" if side == "home" else "away"
            for feature in (
                "team_for_avg5", "team_against_avg5",
                "team_for_avg10", "team_against_avg10",
            ):
                stats[feature] = number(row, f"{prefix}_{feature}")
            card_prefix = f"{side}_card"
            for feature in (
                "team_for_avg5", "team_against_avg5",
                "team_for_avg10", "team_against_avg10",
            ):
                stats[feature.replace("team_", "card_team_")] = number(row, f"{card_prefix}_{feature}")
            latest_team[(league, norm(team))] = {
                "name": team, "stats": stats, "matchesBefore": int(float(row.get(f"{side}_matches_before") or 0))
            }

    teams = defaultdict(dict)
    for (league, team), value in latest_team.items():
        teams[league][team] = value

    league_stats = {}
    for league, row in latest_league.items():
        league_stats[league] = {
            "league_corners_avg": number(row, "league_corners_avg"),
            "league_cards_avg": number(row, "league_cards_avg"),
        }

    league_names = {}
    if AUDIT.exists():
        audit = json.loads(AUDIT.read_text(encoding="utf-8"))
        for item in audit.get("sources", {}).get("footballData", {}).get("leagues", []):
            code = str(item.get("code", "")).strip()
            name = str(item.get("league", "")).strip()
            if code and name:
                league_names[code] = name

    # Map normalized fixture competition names to football-data league codes.
    # Canonical names from the audit are added automatically; aliases cover common
    # labels used by the fixture feed.
    league_aliases = {norm(name): code for code, name in league_names.items() if code and name}
    league_aliases.update({
        "premier league": "E0",
        "championship": "E1",
        "league one": "E2",
        "league two": "E3",
        "scottish premiership": "SC0",
        "eredivisie": "N1",
        "bundesliga": "D1",
        "2 bundesliga": "D2",
        "serie a": "I1",
        "la liga": "SP1",
        "ligue 1": "F1",
    })

    output = {
        "schemaVersion": 1,
        "generatedAt": date.today().isoformat(),
        "status": "experimental_research_model",
        "disclaimer": "Experimental model estimates, not betting advice. Only use fixtures matched to a supported league and both teams with historical features.",
        "lines": LINES,  # Backward-compatible default: corner totals\n        "marketLines": {\n            "corners": LINES,\n            "yellowCards": [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],\n        },
        "leagueNames": league_names,
        "leagueAliases": league_aliases,
        "models": {
            "corners": export_market(rows, "corners_total", "corners_model_eligible", CORNERS_FEATURES),
            "yellowCards": export_market(rows, "cards_total", "cards_model_eligible", CARDS_FEATURES),
        },
        "leagueStats": league_stats,
        "teams": {league: values for league, values in teams.items()},
        "featurePolicy": {
            "teamStats": "Latest available pre-match rolling features from the historical training dataset",
            "unsupportedFixture": "Do not generate a model probability if league or either team cannot be matched",
        },
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUT),
        "cornersTrainingRows": output["models"]["corners"]["trainingRows"],
        "cardsTrainingRows": output["models"]["yellowCards"]["trainingRows"],
        "leagues": len(teams),
        "teams": sum(len(v) for v in teams.values()),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
