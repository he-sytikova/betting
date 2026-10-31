#!/usr/bin/env python3
"""Build leakage-safe historical training rows for corners and yellow cards.

Features for a match use only earlier dated matches in the same competition.
Matches on the same calendar date are evaluated before any of that date's
results are added to team histories.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "data" / "corners_cards_audit.json"
OUT = ROOT / "data" / "corners_cards_training.csv"
BASE = "https://www.football-data.co.uk/mmz4281"
USER_AGENT = "betting-corners-cards-dataset/1.0"
WINDOWS = (5, 10)

def fetch_rows(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = response.read()
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))

def number(value):
    try:
        v = float(str(value).strip())
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None

def match_date(value):
    value = (value or "").strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None

def mean(values):
    return sum(values) / len(values) if values else None

def team_features(history, prefix):
    result = {}
    for window in WINDOWS:
        recent = list(history)[-window:]
        result[f"{prefix}_for_avg{window}"] = mean([x[0] for x in recent])
        result[f"{prefix}_against_avg{window}"] = mean([x[1] for x in recent])
        result[f"{prefix}_n{window}"] = len(recent)
    return result

def main():
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    eligible = {
        x["code"] for x in audit["sources"]["footballData"]["leagues"]
        if x.get("cornersEligiblePreliminary") or x.get("cardsEligiblePreliminary")
    }
    # Keep independent market flags; do not discard a league eligible for only one market.
    market_flags = {
        x["code"]: (bool(x.get("cornersEligiblePreliminary")), bool(x.get("cardsEligiblePreliminary")))
        for x in audit["sources"]["footballData"]["leagues"]
    }
    seasons = [f"{y:02d}{(y + 1) % 100:02d}" for y in range(15, 26)]
    output = []
    skipped = {"invalid_date": 0, "missing_teams": 0, "no_market_stats": 0}

    for code in sorted(eligible):
        corner_ok, card_ok = market_flags[code]
        all_matches = []
        for season in seasons:
            try:
                rows = fetch_rows(f"{BASE}/{season}/{code}.csv")
            except Exception as exc:
                print(f"Skip {code}/{season}: {exc}")
                continue
            for row in rows:
                date = match_date(row.get("Date"))
                home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
                if date is None:
                    skipped["invalid_date"] += 1
                    continue
                if not home or not away:
                    skipped["missing_teams"] += 1
                    continue
                hg, ag = number(row.get("FTHG")), number(row.get("FTAG"))
                if hg is None or ag is None:
                    continue
                all_matches.append({
                    "date": date, "season": season, "league": code,
                    "home": home, "away": away,
                    "hc": number(row.get("HC")), "ac": number(row.get("AC")),
                    "hy": number(row.get("HY")), "ay": number(row.get("AY")),
                })

        all_matches.sort(key=lambda x: (x["date"], x["home"], x["away"]))
        histories = defaultdict(lambda: {"corners": deque(maxlen=10), "cards": deque(maxlen=10)})
        league_history = {"corners": [], "cards": []}
        index = 0
        while index < len(all_matches):
            day = all_matches[index]["date"]
            end = index
            while end < len(all_matches) and all_matches[end]["date"] == day:
                end += 1
            day_matches = all_matches[index:end]
            pending = []
            for m in day_matches:
                hkey, akey = (code, m["home"]), (code, m["away"])
                row = {
                    "date": m["date"].isoformat(), "season": m["season"],
                    "league": code, "home_team": m["home"], "away_team": m["away"],
                    "home_matches_before": len(histories[hkey]["corners"]),
                    "away_matches_before": len(histories[akey]["corners"]),
                    "league_matches_before": len(league_history["corners"]),
                    "home_advantage": 1,
                }
                row.update({f"home_{k}": v for k, v in team_features(histories[hkey]["corners"], "team").items()})
                row.update({f"away_{k}": v for k, v in team_features(histories[akey]["corners"], "team").items()})
                row.update({f"home_card_{k}": v for k, v in team_features(histories[hkey]["cards"], "team").items()})
                row.update({f"away_card_{k}": v for k, v in team_features(histories[akey]["cards"], "team").items()})
                row["league_corners_avg"] = mean(league_history["corners"][-100:])
                row["league_cards_avg"] = mean(league_history["cards"][-100:])
                row["target_home_corners"], row["target_away_corners"] = m["hc"], m["ac"]
                row["target_home_yellow"], row["target_away_yellow"] = m["hy"], m["ay"]
                row["corners_available"] = int(m["hc"] is not None and m["ac"] is not None)
                row["cards_available"] = int(m["hy"] is not None and m["ay"] is not None)
                row["corners_total"] = (m["hc"] + m["ac"]) if row["corners_available"] else ""
                row["cards_total"] = (m["hy"] + m["ay"]) if row["cards_available"] else ""
                if (corner_ok and row["corners_available"]) or (card_ok and row["cards_available"]):
                    pending.append((row, m))
                else:
                    skipped["no_market_stats"] += 1

            # Update histories only after every fixture on this date has features computed.
            for row, m in pending:
                hkey, akey = (code, m["home"]), (code, m["away"])
                if m["hc"] is not None and m["ac"] is not None:
                    histories[hkey]["corners"].append((m["hc"], m["ac"]))
                    histories[akey]["corners"].append((m["ac"], m["hc"]))
                    league_history["corners"].append(m["hc"] + m["ac"])
                if m["hy"] is not None and m["ay"] is not None:
                    histories[hkey]["cards"].append((m["hy"], m["ay"]))
                    histories[akey]["cards"].append((m["ay"], m["hy"]))
                    league_history["cards"].append(m["hy"] + m["ay"])
                output.append(row)
            index = end

    if not output:
        raise RuntimeError("No eligible training rows were produced")
    # Preserve a stable column order and blank values for unavailable history.
    fields = list(output[0].keys())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)
    print(json.dumps({
        "rows": len(output), "eligibleLeagues": sorted(eligible),
        "cornerRows": sum(r["corners_available"] for r in output),
        "cardRows": sum(r["cards_available"] for r in output),
        "skipped": skipped, "output": str(OUT)
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
