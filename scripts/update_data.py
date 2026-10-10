#!/usr/bin/env python3
"""Fetch daily football fixtures/forecasts and publish a token-free JSON snapshot."""
import json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "forecasts.json"
TODAY = datetime.now(timezone.utc).date()


def get_json(url, headers=None, timeout=35):
    req = Request(url, headers={"User-Agent": "betting-forecast-dashboard/1.0", **(headers or {})})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_results(payload):
    if isinstance(payload, list): return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "games", "events", "predictions"):
            value = payload.get(key)
            if isinstance(value, list): return value
            if isinstance(value, dict):
                for nested in ("results", "items", "games", "events", "predictions"):
                    if isinstance(value.get(nested), list): return value[nested]
    return []


def fetch_bsd(token):
    headers = {"Authorization": f"Token {token}"}
    base = "https://sports.bzzoiro.com/api/v2"
    dates = {"date_from": str(TODAY), "date_to": str(TODAY + timedelta(days=1)), "limit": 200}
    events = get_json(f"{base}/events/?{urlencode(dates)}", headers)
    # BSD's prediction endpoint documents upcoming forecasts; fetch independently.
    predictions = get_json(f"{base}/predictions/?upcoming=true&limit=200", headers)
    return {"events": list_results(events), "predictions": list_results(predictions)}


def fetch_sstats(token):
    base = "https://api.sstats.net"
    # SStats supports Games/list and Glicko endpoints. Date filters vary by API version,
    # so request the list using the documented common date/year parameters and filter locally.
    params = {"DateFrom": str(TODAY), "DateTo": str(TODAY + timedelta(days=1)), "Year": TODAY.year, "apikey": token}
    games_payload = get_json(f"{base}/Games/list?{urlencode(params)}")
    games = list_results(games_payload)
    glicko = []
    # Keep the job bounded; Glicko enriches games with model estimates, not a guaranteed bet.
    for game in games[:80]:
        game_id = game.get("id", game.get("gameId", game.get("game_id")))
        if game_id is None: continue
        try:
            glicko.append(get_json(f"{base}/Games/glicko/{game_id}?{urlencode({'apikey': token})}"))
        except Exception as exc:
            print(f"SStats Glicko unavailable for game {game_id}: {exc}", file=sys.stderr)
    return {"games": games, "glicko": glicko}


def main():
    sources, errors = {}, []
    for name, env, fetcher in (("BSD", "BSD_TOKEN", fetch_bsd), ("SStats", "SSTATS_TOKEN", fetch_sstats)):
        token = os.environ.get(env, "").strip()
        if not token:
            errors.append(f"{env} is not configured")
            continue
        try:
            sources[name] = fetcher(token)
            print(f"{name}: fetched {sum(len(v) for v in sources[name].values() if isinstance(v, list))} records")
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            print(errors[-1], file=sys.stderr)
    # Don't erase a valid prior snapshot if both providers failed.
    if not sources:
        raise RuntimeError("No provider data retrieved. " + "; ".join(errors))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_utc": str(TODAY),
        "sources": sources,
        "note": "UTC date snapshot. Missing markets are shown as —. " + ("Warnings: " + "; ".join(errors) if errors else ""),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")

if __name__ == "__main__": main()
