"""Collector: fetch all sources and write site/data.json for the mobile app.

Runs in GitHub Actions every ~10 minutes. Secrets come from environment variables
(PRIM_API_KEY) and are never written to the output.

    python collect.py            # writes site/data.json
    python collect.py --out x.json

Output (all times ISO 8601 UTC):
    {"generated_at", "sources": [{"id","name","ok","error","count","feed_updated"}],
     "events": [{"id","kind","cat","title","text","lines","modes","stops":[{"n","lat","lon"}],
                 "lat","lon","pts","status","start","end","updated","sev","src","url","lt"}]}

cat is one of: "demo" (operator cites a demonstration), "strike" (operator cites a strike /
social movement), "disruption" (unplanned
transport problem), "works" (planned transport works), "road" (road works/closures).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paris_open_data
import prim_disruptions

ROOT = Path(__file__).parent
STOPS_PATH = ROOT / "data" / "idfm_stops.json"
CONFIG_PATH = ROOT / "sources.json"
HORIZON_DAYS = 7          # keep events starting within this many days
MAX_STOPS_PER_EVENT = 40


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


class StopLocator:
    """Resolve IDFM stop_point ids to coordinates.

    "stop_point:IDFM:463117"                     -> reference stop (ArRId)
    "stop_point:IDFM:monomodalStopPlace:473109"  -> stop zone (ZdAId), used by rail
    """

    def __init__(self, path=STOPS_PATH):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.arr, self.zda = data["arr"], data["zda"]

    def locate(self, stop_id):
        key = str(stop_id or "").split(":")[-1]
        if "monomodalStopPlace" in str(stop_id):
            return self.zda.get(key)
        return self.arr.get(key) or self.zda.get(key)


FRENCH_HINTS = (" le ", " la ", " les ", " des ", " du ", " à ", " pour ", " arrêt", " ligne", " travaux",
                " jusqu", " desservi", " en raison", " perturb", " à partir")


def guess_lang(text):
    """'fr' when the text reads as French, else 'en'. Cheap heuristic, good enough to label."""
    t = f" {text.lower()} "
    return "fr" if sum(h in t for h in FRENCH_HINTS) >= 2 else "en"


def category(event):
    if event.get("protest_kind") == "demonstration":
        return "demo"
    if event.get("protest_kind") == "strike":
        return "strike"
    return "works" if event.get("cause") == "Planned works" else "disruption"


def transport_events(raw, locator, now):
    out = []
    horizon = now + timedelta(days=HORIZON_DAYS)
    for e in raw:
        if e["status"] == "ended":
            continue
        if e["status"] == "upcoming" and e["start"] and e["start"] > horizon:
            continue
        # Buses are kept only when the operator cites a demonstration or strike:
        # there are hundreds of routine bus diversions for roadworks.
        if not (e.get("is_rail") or e.get("protest_related")):
            continue
        stops = []
        for stop_id, name in e.get("stop_refs", [])[:MAX_STOPS_PER_EVENT]:
            pos = locator.locate(stop_id)
            if pos and not any(s["n"] == name for s in stops):
                stops.append({"n": name, "lat": pos[0], "lon": pos[1]})
        lat = sum(s["lat"] for s in stops) / len(stops) if stops else None
        lon = sum(s["lon"] for s in stops) / len(stops) if stops else None
        out.append({
            "id": e["event_id"], "kind": "transport", "cat": category(e),
            "title": e["title"], "text": e["description"],
            "lines": e.get("lines", []), "modes": e.get("modes", []),
            "stops": stops, "lat": lat, "lon": lon, "pts": [],
            "status": e["status"], "start": iso(e["start"]), "end": iso(e["end"]),
            "updated": iso(e.get("updated")), "sev": e.get("severity"),
            "src": "IDFM official", "url": e["url"], "lt": e.get("long_term", False),
            "why": e.get("protest_word"),
            "lang": guess_lang(e["title"] + " " + e["description"]),
        })
    return out


def road_events(raw, now):
    out = []
    horizon = now + timedelta(days=HORIZON_DAYS)
    for e in raw:
        if e["status"] == "ended" or (e["status"] == "upcoming" and e["start"] and e["start"] > horizon):
            continue
        out.append({
            "id": e["event_id"], "kind": "road", "cat": "road",
            "title": e["title"], "text": e["description"], "lines": [], "modes": [],
            "stops": [], "lat": e["lat"], "lon": e["lon"],
            "pts": [[round(a, 5), round(b, 5)] for a, b in e.get("points", [])],
            "status": e["status"], "start": iso(e["start"]), "end": iso(e["end"]),
            "updated": None, "sev": None, "src": "Paris Open Data", "url": e["url"],
            "lt": e.get("long_term", False),
        })
    return out


def collect(now=None, api_key=None, locator=None, config=None):
    now = now or datetime.now(timezone.utc)
    config = config or json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    locator = locator or StopLocator()
    sources, events = [], []

    api_key = api_key if api_key is not None else os.environ.get("PRIM_API_KEY", "")
    try:
        raw, feed_updated = prim_disruptions.fetch(api_key, now)
        tev = transport_events(raw, locator, now)
        events += tev
        sources.append({"id": "idfm", "name": "Île-de-France Mobilités traffic info", "ok": True,
                        "error": None, "count": len(tev), "feed_updated": iso(feed_updated)})
    except Exception as exc:  # message never contains the key (sent as a header)
        sources.append({"id": "idfm", "name": "Île-de-France Mobilités traffic info", "ok": False,
                        "error": str(exc)[:200], "count": 0, "feed_updated": None})

    road_cfg = config.get("paris_open_data", {})
    try:
        raw, warnings = paris_open_data.fetch(road_cfg, now)
        rev = road_events(raw, now)
        events += rev
        sources.append({"id": "roads", "name": "Paris Open Data road works", "ok": True,
                        "error": "; ".join(warnings) or None, "count": len(rev), "feed_updated": None})
    except Exception as exc:
        sources.append({"id": "roads", "name": "Paris Open Data road works", "ok": False,
                        "error": str(exc)[:200], "count": 0, "feed_updated": None})

    return {"generated_at": iso(now), "sources": sources, "events": events}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "site" / "data.json"))
    args = parser.parse_args(argv)
    result = collect()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for s in result["sources"]:
        print(f"{s['id']}: ok={s['ok']} count={s['count']} error={s['error']}")
    transport = [e for e in result["events"] if e["kind"] == "transport"]
    if transport:
        fr = sum(e.get("lang") == "fr" for e in transport)
        print(f"transport notices in French: {fr} of {len(transport)}")
    print(f"wrote {len(result['events'])} events to {args.out}")
    # Fail the run only if every source failed, so the old data stays deployed.
    return 0 if any(s["ok"] for s in result["sources"]) else 1


if __name__ == "__main__":
    sys.exit(main())
