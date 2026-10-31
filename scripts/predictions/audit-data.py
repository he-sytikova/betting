#!/usr/bin/env python3
"""Audit free football match-stat coverage for corners and yellow cards.

No API keys or third-party Python packages are required.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "corners_cards_audit.json"
OPEN_BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master"
SEASON = "2627"
MIN_MATCHES = 100
MIN_TEAM_MATCHES = 8
USER_AGENT = "betting-corners-cards-audit/1.0"

# football-data.co.uk has historical match-level stats for these competitions.
FD_LEAGUES = {
    "E0": "England - Premier League",
    "E1": "England - Championship",
    "E2": "England - League One",
    "E3": "England - League Two",
    "SC0": "Scotland - Premiership",
    "SC1": "Scotland - Championship",
    "SC2": "Scotland - League One",
    "SC3": "Scotland - League Two",
    "D1": "Germany - Bundesliga",
    "D2": "Germany - 2. Bundesliga",
    "I1": "Italy - Serie A",
    "I2": "Italy - Serie B",
    "SP1": "Spain - La Liga",
    "SP2": "Spain - Segunda",
    "F1": "France - Ligue 1",
    "F2": "France - Ligue 2",
    "N1": "Netherlands - Eredivisie",
    "B1": "Belgium - First Division",
    "P1": "Portugal - Primeira Liga",
    "T1": "Turkey - Super Lig",
    "G1": "Greece - Super League",
}

def fetch(url: str, timeout: int = 18) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()

def parse_int(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() and number >= 0 else None
    except ValueError:
        return None

def csv_rows(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    # football-data.co.uk files are sometimes semicolon-delimited in mirrors;
    # the canonical files are comma-delimited, detected from the header.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))

def match_stats(rows, home_key, away_key):
    complete = 0
    for row in rows:
        if parse_int(row.get(home_key)) is not None and parse_int(row.get(away_key)) is not None:
            complete += 1
    return complete

def audit_open_matchday():
    index_url = f"{OPEN_BASE}/LEAGUE_INDEX.md"
    text = fetch(index_url).decode("utf-8", errors="replace")
    folders = []
    for line in text.splitlines():
        match = re.match(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+)", line)
        if match:
            folders.append((match.group(1), match.group(2).strip()))
    results = []

    def one(item):
        folder, league = item
        url = f"{OPEN_BASE}/data/{folder}/results_{SEASON}.csv"
        try:
            rows = csv_rows(fetch(url))
            played = [r for r in rows if parse_int(r.get("ft_home")) is not None and parse_int(r.get("ft_away")) is not None]
            corners = match_stats(played, "home_corners", "away_corners")
            cards = match_stats(played, "home_yellow", "away_yellow")
            fouls = match_stats(played, "home_fouls", "away_fouls")
            return {
                "folder": folder, "league": league, "season": "2026/27",
                "matches": len(played), "corners_complete": corners,
                "yellow_cards_complete": cards, "fouls_complete": fouls,
                "corners_coverage": round(corners / len(played), 4) if played else 0,
                "cards_coverage": round(cards / len(played), 4) if played else 0,
                "source": "open-matchday", "status": "ok"
            }
        except Exception as exc:
            return {"folder": folder, "league": league, "season": "2026/27",
                    "matches": 0, "corners_complete": 0, "yellow_cards_complete": 0,
                    "fouls_complete": 0, "corners_coverage": 0, "cards_coverage": 0,
                    "source": "open-matchday", "status": "unavailable",
                    "error": str(exc)[:180]}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(one, item) for item in folders]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda x: x["league"].lower())

def audit_football_data():
    team_stats = {}
    aggregate = {
        code: {"league": league, "seasons_checked": 0, "matches": 0,
               "corners_complete": 0, "yellow_cards_complete": 0,
               "seasonBreakdown": [], "teamCoverage": [],
               "source": "football-data.co.uk"}
        for code, league in FD_LEAGUES.items()
    }
    errors = []
    seasons = [f"{str(y)[-2:]}{str(y + 1)[-2:]}" for y in range(2015, 2026)]
    for season in seasons:
        for code in FD_LEAGUES:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
            try:
                rows = csv_rows(fetch(url))
                played = [r for r in rows if parse_int(r.get("FTHG")) is not None and parse_int(r.get("FTAG")) is not None]
                if not played:
                    continue
                corners = match_stats(played, "HC", "AC")
                cards = match_stats(played, "HY", "AY")
                season_item = {
                    "season": f"20{season[:2]}/20{season[2:]}",
                    "matches": len(played),
                    "corners_complete": corners,
                    "yellow_cards_complete": cards,
                    "corners_coverage": round(corners / len(played), 4),
                    "cards_coverage": round(cards / len(played), 4)
                }
                item = aggregate[code]
                item["seasons_checked"] += 1
                item["matches"] += len(played)
                item["corners_complete"] += corners
                item["yellow_cards_complete"] += cards
                item["seasonBreakdown"].append(season_item)
                for row in played:
                    home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
                    for team, stat_key in ((home, "HC"), (away, "AC")):
                        if team:
                            t = team_stats.setdefault((code, team), {"appearances": 0, "corners": 0, "cards": 0, "recent": []})
                            t["appearances"] += 1
                            if parse_int(row.get(stat_key)) is not None:
                                t["corners"] += 1
                            card_key = "HY" if team == home else "AY"
                            if parse_int(row.get(card_key)) is not None:
                                t["cards"] += 1
                            date = (row.get("Date") or "").strip()
                            t["recent"].append((date, parse_int(row.get(stat_key)) is not None, parse_int(row.get(card_key)) is not None))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                errors.append({"league_code": code, "season": season, "error": str(exc)[:120]})
            time.sleep(0.03)
    for item in aggregate.values():
        item["corners_coverage"] = round(item["corners_complete"] / item["matches"], 4) if item["matches"] else 0
        item["cards_coverage"] = round(item["yellow_cards_complete"] / item["matches"], 4) if item["matches"] else 0
        code = next(k for k, v in aggregate.items() if v is item)
        teams = []
        for (team_code, team), stats in team_stats.items():
            if team_code != code:
                continue
            recent = sorted(stats["recent"], key=lambda x: x[0])[-10:]
            teams.append({
                "team": team, "appearances": stats["appearances"],
                "cornersAvailable": stats["corners"], "cardsAvailable": stats["cards"],
                "recent10CornersCoverage": round(sum(x[1] for x in recent) / len(recent), 4) if recent else 0,
                "recent10CardsCoverage": round(sum(x[2] for x in recent) / len(recent), 4) if recent else 0
            })
        item["teamCoverage"] = sorted(teams, key=lambda x: x["team"].lower())
        item["cornersEligiblePreliminary"] = (
            item["corners_complete"] >= MIN_MATCHES and
            item["corners_coverage"] >= 0.80 and
            bool(teams) and all(t["cornersAvailable"] >= MIN_TEAM_MATCHES for t in teams)
        )
        item["cardsEligiblePreliminary"] = (
            item["yellow_cards_complete"] >= MIN_MATCHES and
            item["cards_coverage"] >= 0.80 and
            bool(teams) and all(t["cardsAvailable"] >= MIN_TEAM_MATCHES for t in teams)
        )
    return list(aggregate.values()), errors

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    open_data = audit_open_matchday()
    historical, errors = audit_football_data()
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "modelStatus": "audit_only_no_predictions",
        "policy": {
            "minimumHistoricalMatches": MIN_MATCHES,
            "minimumMatchesPerTeam": MIN_TEAM_MATCHES,
            "minimumRecentStatCoverage": 0.80,
            "note": "A league is eligible separately for corners and yellow cards only after historical and per-team checks."
        },
        "sources": {
            "openMatchday": {
                "url": "https://github.com/oladeeayo/open-matchday",
                "season": "2026/27",
                "license": "non-commercial; attribution required",
                "leagues": open_data
            },
            "footballData": {
                "url": "https://www.football-data.co.uk/data.php",
                "seasons": "2015/16-2025/26",
                "leagues": historical,
                "errors": errors
            }
        },
        "summary": {
            "openMatchdayLeaguesChecked": len(open_data),
            "openMatchdayAvailable": sum(1 for x in open_data if x["status"] == "ok"),
            "openMatchdayCornerMatches": sum(x["corners_complete"] for x in open_data),
            "openMatchdayCardMatches": sum(x["yellow_cards_complete"] for x in open_data),
            "footballDataHistoricalMatches": sum(x["matches"] for x in historical),
            "footballDataCornerMatches": sum(x["corners_complete"] for x in historical),
            "footballDataCardMatches": sum(x["yellow_cards_complete"] for x in historical)
        }
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Audit saved: {OUT}")

if __name__ == "__main__":
    main()
