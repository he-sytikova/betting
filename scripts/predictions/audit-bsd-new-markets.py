#!/usr/bin/env python3
"""Bounded BSD historical-statistics reconnaissance; never stores raw match data."""
from __future__ import annotations
import json, os, re, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://sports.bzzoiro.com/api/v2"
TOKEN = os.environ.get("BSD_TOKEN", "").strip()
OUT = Path("data/bsd_new_markets_audit.json")
SAMPLE_PER_SEASON = 5
TARGETS = {
    "E0": ["premier league", "england premier league"],
    "E1": ["championship", "england championship"],
    "E2": ["league one", "england league one"],
    "E3": ["league two", "england league two"],
    "SC0": ["scottish premiership", "scotland premiership"],
    "SC1": ["scottish championship", "scotland championship"],
    "SC2": ["scottish league one", "scotland league one"],
    "SC3": ["scottish league two", "scotland league two"],
    "D1": ["bundesliga", "germany bundesliga"],
    "D2": ["2. bundesliga", "2 bundesliga"],
    "I1": ["serie a", "italy serie a"],
    "I2": ["serie b", "italy serie b"],
    "SP1": ["la liga", "spain la liga"],
    "SP2": ["segunda", "segunda division"],
    "F1": ["ligue 1", "france ligue 1"],
    "F2": ["ligue 2", "france ligue 2"],
    "N1": ["eredivisie", "netherlands eredivisie"],
    "B1": ["belgian pro league", "first division a", "belgium first division"],
    "P1": ["primeira liga", "portugal primeira liga"],
    "T1": ["super lig", "turkey super lig"],
    "G1": ["super league greece", "greece super league"],
}
FIELDS = ["fouls", "total_shots", "shots_on_target", "red_cards"]

def get(path):
    url = path if path.startswith("https://") else BASE + path
    req = urllib.request.Request(url, headers={
        "Authorization": f"Token {TOKEN}", "Accept": "application/json",
        "User-Agent": "betting-bsd-coverage-audit/1.0"
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))

def rows(payload):
    if isinstance(payload, list): return payload
    if isinstance(payload, dict):
        for k in ("results", "data", "items", "seasons", "events", "matches"): 
            if isinstance(payload.get(k), list): return payload[k]
    if isinstance(payload, dict) and payload.get("id") is not None and ("year" in payload or "start_date" in payload):
        return [payload]
    return []

def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()

def name(obj):
    return str(obj.get("name") or obj.get("league_name") or obj.get("title") or "")

def main():
    if not TOKEN: raise SystemExit("BSD_TOKEN secret is missing")
    league_rows = []
    offset = 0
    while True:
        p = get(f"/leagues/?limit=200&offset={offset}")
        batch = rows(p); league_rows.extend(batch)
        if not isinstance(p, dict) or not p.get("next") or not batch: break
        offset += len(batch)
        if offset >= 1000: break

    selected = {}
    for code, aliases in TARGETS.items():
        candidates = []
        for league in league_rows:
            nm = norm(name(league))
            if any(norm(a) == nm or norm(a) in nm or nm in norm(a) for a in aliases):
                candidates.append(league)
        if candidates:
            # Prefer exact alias match, then the first stable API result.
            candidates.sort(key=lambda x: (0 if norm(name(x)) in [norm(a) for a in aliases] else 1, norm(name(x))))
            selected[code] = candidates[0]

    report = {"generatedAt": datetime.now(timezone.utc).isoformat(),
              "source": "BSD API v2", "samplePerSeason": SAMPLE_PER_SEASON,
              "scope": "bounded sample; not a full historical census",
              "competitions": [], "errors": []}

    for code, league in selected.items():
        lid = league.get("id")
        item = {"code": code, "bsdLeagueId": lid, "league": name(league), "seasons": []}
        try:
            seasons_payload = get(f"/leagues/{lid}/seasons/")
            seasons = rows(seasons_payload)
            seasons = [s for s in seasons if s.get("id") is not None]
            seasons.sort(key=lambda s: (int(s.get("year") or 0), str(s.get("start_date") or "")), reverse=True)
            for season in seasons[:2]:
                sid = season["id"]
                season_item = {"seasonId": sid, "season": season.get("name") or season.get("year"),
                               "matchesSampled": 0, "fields": {f: 0 for f in FIELDS},
                               "refereeAvailable": 0, "errors": 0}
                p = get(f"/events/?league_id={lid}&season_id={sid}&status=finished&limit=200&offset=0")
                events = rows(p)
                # Recent finished matches are the most relevant initial signal.
                events.sort(key=lambda e: str(e.get("event_date") or e.get("date") or ""), reverse=True)
                for ev in events[:SAMPLE_PER_SEASON]:
                    eid = ev.get("id") or ev.get("event_id")
                    if eid is None: continue
                    try:
                        av = get(f"/events/{eid}/availability/")
                        keys = set(av.get("stat_keys") or [])
                        detail = get(f"/events/{eid}/")
                        referee = detail.get("referee")
                        has_ref = bool(referee and (not isinstance(referee, dict) or referee.get("id") or referee.get("name")))
                        season_item["matchesSampled"] += 1
                        for field in FIELDS:
                            if field in keys: season_item["fields"][field] += 1
                        if has_ref: season_item["refereeAvailable"] += 1
                    except Exception as exc:
                        season_item["errors"] += 1
                        report["errors"].append({"league": name(league), "eventId": eid, "error": str(exc)[:160]})
                    time.sleep(0.08)
                n = season_item["matchesSampled"]
                season_item["coverage"] = {f: round(c/n, 3) if n else None for f,c in season_item["fields"].items()}
                season_item["refereeCoverage"] = round(season_item["refereeAvailable"]/n, 3) if n else None
                item["seasons"].append(season_item)
        except Exception as exc:
            item["error"] = str(exc)[:200]
            report["errors"].append({"league": name(league), "error": str(exc)[:200]})
        report["competitions"].append(item)

    report["summary"] = {
        "targetCompetitions": len(TARGETS), "matchedCompetitions": len(selected),
        "sampledMatches": sum(s["matchesSampled"] for c in report["competitions"] for s in c["seasons"]),
        "errors": len(report["errors"]),
        "warning": "No match records sampled; check BSD seasons/events response shapes." if sum(s["matchesSampled"] for c in report["competitions"] for s in c["seasons"]) == 0 else None
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"Report: {OUT}")

if __name__ == "__main__":
    main()
