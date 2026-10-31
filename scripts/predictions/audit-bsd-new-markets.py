#!/usr/bin/env python3
"""Bounded BSD historical-statistics audit for fouls, shots, shots on target and red cards."""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = "https://sports.bzzoiro.com/api/v2"
TOKEN = os.environ.get("BSD_TOKEN", "").strip()
OUT = Path("data/bsd_new_markets_audit.json")
SAMPLE_PER_SEASON = 10
MIN_REQUEST_INTERVAL = 0.8
MAX_RETRIES = 6
FIELDS = ["fouls", "total_shots", "shots_on_target", "red_cards"]

# Exact league names are combined with an explicit country check. This prevents
# Scottish Championship/League One/League Two from matching English leagues.
TARGETS = {
    "E0": ("England", ["Premier League"]),
    "E1": ("England", ["Championship"]),
    "E2": ("England", ["League One"]),
    "E3": ("England", ["League Two"]),
    "SC0": ("Scotland", ["Scottish Premiership", "Premiership"]),
    "SC1": ("Scotland", ["Scottish Championship", "Championship"]),
    "SC2": ("Scotland", ["Scottish League One", "League One"]),
    "SC3": ("Scotland", ["Scottish League Two", "League Two"]),
    "D1": ("Germany", ["Bundesliga"]),
    "D2": ("Germany", ["2. Bundesliga", "2 Bundesliga"]),
    "I1": ("Italy", ["Serie A"]),
    "I2": ("Italy", ["Serie B"]),
    "SP1": ("Spain", ["La Liga", "LaLiga"]),
    "SP2": ("Spain", ["Segunda División", "Segunda Division", "LaLiga 2"]),
    "F1": ("France", ["Ligue 1"]),
    "F2": ("France", ["Ligue 2"]),
    "N1": ("Netherlands", ["Eredivisie"]),
    "B1": ("Belgium", ["Pro League", "First Division A"]),
    "P1": ("Portugal", ["Primeira Liga"]),
    "T1": ("Turkey", ["Super Lig", "Trendyol Super Lig"]),
    "G1": ("Greece", ["Super League"]),
}
COUNTRY_ALIASES = {
    "england": {"england", "uk", "united kingdom"},
    "scotland": {"scotland"},
    "germany": {"germany", "deutschland"},
    "italy": {"italy", "italia"},
    "spain": {"spain", "espana"},
    "france": {"france"},
    "netherlands": {"netherlands", "holland"},
    "belgium": {"belgium"},
    "portugal": {"portugal"},
    "turkey": {"turkey", "turkiye"},
    "greece": {"greece", "hellas"},
}
_last_request_at = 0.0


def norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "seasons", "events", "matches", "leagues"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if payload.get("id") is not None and ("year" in payload or "start_date" in payload):
            return [payload]
    return []


def league_name(obj):
    return str(obj.get("name") or obj.get("league_name") or obj.get("title") or "")


def country_values(obj):
    values = []
    for key in ("country", "country_name", "countryName", "country_code", "countryCode", "region"):
        value = obj.get(key)
        if isinstance(value, dict):
            values.extend(str(value.get(k) or "") for k in ("name", "code", "title", "short_name"))
        elif value is not None:
            values.append(str(value))
    return {norm(v) for v in values if v}


def country_matches(obj, expected):
    observed = country_values(obj)
    aliases = COUNTRY_ALIASES[expected.lower()]
    return any(v in {norm(a) for a in aliases} for v in observed)


def get(path):
    global _last_request_at
    url = path if path.startswith("https://") else BASE + path
    for attempt in range(MAX_RETRIES + 1):
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Token {TOKEN}",
            "Accept": "application/json",
            "User-Agent": "betting-bsd-historical-audit/2.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read()
                _last_request_at = time.monotonic()
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            _last_request_at = time.monotonic()
            if exc.code != 429 or attempt >= MAX_RETRIES:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = None
            if retry_after:
                try:
                    delay = max(0.0, float(retry_after))
                except ValueError:
                    try:
                        delay = max(0.0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                    except Exception:
                        delay = None
            if delay is None:
                delay = min(60.0, 2 ** attempt) + random.uniform(0.1, 0.7)
            time.sleep(delay)
    raise RuntimeError("Request retry loop exhausted")


def extract_values(payload, field):
    """Collect non-null scalar values across keyed and row-oriented BSD stats payloads."""
    found = []
    label_keys = ("key", "name", "stat_key", "statistic", "type")
    value_keys = ("home", "away", "home_value", "away_value", "value")

    def walk(node):
        if isinstance(node, dict):
            labels = [norm(node.get(k)) for k in label_keys if node.get(k) is not None]
            if norm(field) in labels:
                for key in value_keys:
                    value = node.get(key)
                    if value is not None and not isinstance(value, (dict, list)):
                        found.append(value)
            for key, value in node.items():
                if norm(key) == norm(field):
                    if value is not None and not isinstance(value, (dict, list)):
                        found.append(value)
                    elif isinstance(value, dict):
                        for nested in value.values():
                            if nested is not None and not isinstance(nested, (dict, list)):
                                found.append(nested)
                elif key not in label_keys:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def stat_keys(payload):
    keys = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if norm(key) in {norm(x) for x in FIELDS}:
                    keys.add(norm(key).replace(" ", "_"))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return keys


def main():
    if not TOKEN:
        raise SystemExit("BSD_TOKEN secret is missing")

    league_payload = get("/leagues/?limit=200&offset=0")
    league_list = rows(league_payload)
    while isinstance(league_payload, dict) and league_payload.get("next") and len(league_list) < 1000:
        next_url = league_payload["next"]
        league_payload = get(next_url if str(next_url).startswith("https://") else urllib.parse.urljoin(BASE + "/", str(next_url)))
        batch = rows(league_payload)
        if not batch:
            break
        league_list.extend(batch)

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "BSD API v2",
        "samplePerSeason": SAMPLE_PER_SEASON,
        "scope": "bounded recent-season sample; not a full historical census",
        "markets": FIELDS,
        "referees": {"included": False, "reason": "Excluded by project decision"},
        "competitions": [],
        "errors": [],
        "unmatchedCompetitions": [],
    }

    for code, (country, aliases) in TARGETS.items():
        candidates = [
            league for league in league_list
            if norm(league_name(league)) in {norm(a) for a in aliases}
            and country_matches(league, country)
        ]
        if len(candidates) != 1:
            report["unmatchedCompetitions"].append({
                "code": code,
                "expectedCountry": country,
                "expectedNames": aliases,
                "candidateCount": len(candidates),
                "reason": "No exact country-qualified league match" if not candidates else "Ambiguous country-qualified league match",
            })
            continue

        league = candidates[0]
        lid = league.get("id")
        item = {
            "code": code,
            "expectedCountry": country,
            "bsdLeagueId": lid,
            "league": league_name(league),
            "country": sorted(country_values(league)),
            "seasons": [],
        }
        try:
            seasons = [s for s in rows(get(f"/leagues/{lid}/seasons/")) if s.get("id") is not None]
            seasons.sort(key=lambda s: (int(s.get("year") or 0), str(s.get("start_date") or "")), reverse=True)
            for season in seasons[:2]:
                sid = season["id"]
                season_item = {
                    "seasonId": sid,
                    "season": season.get("name") or season.get("year"),
                    "matchesSampled": 0,
                    "availabilityKeys": {f: 0 for f in FIELDS},
                    "actualStatsValues": {f: 0 for f in FIELDS},
                    "actualStatsBothSides": {f: 0 for f in FIELDS},
                    "actualStatsNullOrMissing": {f: 0 for f in FIELDS},
                    "statsPayloadKeys": [],
                    "errors": 0,
                }
                event_payload = get(
                    f"/events/?league_id={lid}&season_id={sid}&status=finished&limit=200&offset=0"
                )
                events = rows(event_payload)
                events.sort(
                    key=lambda e: str(e.get("event_date") or e.get("date") or e.get("start_time") or ""),
                    reverse=True,
                )
                for event in events[:SAMPLE_PER_SEASON]:
                    event_id = event.get("id") or event.get("event_id")
                    if event_id is None:
                        continue
                    try:
                        availability = get(f"/events/{event_id}/availability/")
                        available = set(availability.get("stat_keys") or []) if isinstance(availability, dict) else set()
                        stats = get(f"/events/{event_id}/stats/")
                        if not season_item["statsPayloadKeys"]:
                            season_item["statsPayloadKeys"] = sorted(
                                list(stats.keys()) if isinstance(stats, dict) else
                                (list(stats[0].keys()) if isinstance(stats, list) and stats and isinstance(stats[0], dict) else [])
                            )[:40]
                        season_item["matchesSampled"] += 1
                        for field in FIELDS:
                            if field in available:
                                season_item["availabilityKeys"][field] += 1
                            vals = extract_values(stats, field)
                            season_item["actualStatsValues"][field] += len(vals)
                            if len(vals) >= 2:
                                season_item["actualStatsBothSides"][field] += 1
                            else:
                                season_item["actualStatsNullOrMissing"][field] += 1
                    except Exception as exc:
                        season_item["errors"] += 1
                        report["errors"].append({
                            "league": league_name(league),
                            "season": season_item["season"],
                            "eventId": event_id,
                            "error": str(exc)[:180],
                        })
                n = season_item["matchesSampled"]
                season_item["coverage"] = {
                    field: {
                        "availabilityKey": round(season_item["availabilityKeys"][field] / n, 3) if n else None,
                        "bothTeamValues": round(season_item["actualStatsBothSides"][field] / n, 3) if n else None,
                    }
                    for field in FIELDS
                }
                item["seasons"].append(season_item)
        except Exception as exc:
            item["error"] = str(exc)[:200]
            report["errors"].append({"league": league_name(league), "error": str(exc)[:200]})
        report["competitions"].append(item)

    report["summary"] = {
        "targetCompetitions": len(TARGETS),
        "matchedCompetitions": len(report["competitions"]),
        "unmatchedCompetitions": len(report["unmatchedCompetitions"]),
        "sampledMatches": sum(s["matchesSampled"] for c in report["competitions"] for s in c["seasons"]),
        "errors": len(report["errors"]),
        "markets": FIELDS,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"Report: {OUT}")


if __name__ == "__main__":
    main()
