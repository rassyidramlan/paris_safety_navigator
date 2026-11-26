"""Closed-app alerts: Web Push to the family's phones when something turns red.

Runs at the end of every collector run (every 10 minutes). Everything personal stays private:
  - PUSH_TRIP_URL (Actions secret): the secret-gist link to itinerary.json. The family's
    itinerary stops (and hotel, if the file gives its location) come from there.
  - push-devices.txt, a second file in the same gist: one line per phone, the code the phone shows
    under Trip > Places > "Alerts when the app is closed". A code holds that phone's push
    subscription, its owner's name and its always-watched places (the hotel).
  - PUSH_VAPID_PRIVATE_KEY (Actions secret): the key the organiser created in the app (Help >
    Closed-app alerts). Only the holder of this key can send to the family's phones.
  - PUSH_VAPID_SUBJECT (optional secret): a mailto: or https: contact for the push services.
Nothing personal is written to data.json; the run summary only says how many phones and alerts.

Only red situations are sent, matching the app's own rules:
  - a demonstration within 500 m of an always-watched place, happening now or within 2 hours;
  - a demonstration within 500 m of an itinerary stop, around that stop's time (one hour before to
    two hours after), for stops between one hour ago and three hours ahead;
  - a red Météo-France warning for Paris.
Each situation is sent once (remembered in .cache/push.json between runs). A phone whose code
appears for the first time gets a "closed-app alerts are on" message, so its owner knows it works.
"""
import base64
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).parent
CACHE_PATH = ROOT / ".cache" / "push.json"
PARIS_TZ = ZoneInfo("Europe/Paris")
RED_M = 500
NEAR_TERM = timedelta(hours=2)
STOP_BEFORE, STOP_AFTER = timedelta(hours=1), timedelta(hours=2)
STOPS_FROM, STOPS_TO = timedelta(hours=1), timedelta(hours=3)
KEEP = timedelta(days=4)
TIMEOUT = 20
DEFAULT_SUBJECT = "https://rassyidramlan.github.io"   # push services want a mailto: or a bare https host
CODE_PREFIX = "PP1."


# ---------- geometry and wording (same as the app) ----------
def distance(a, b):
    r = math.pi / 180
    dlat, dlon = (b["lat"] - a["lat"]) * r, (b["lon"] - a["lon"]) * r
    h = math.sin(dlat / 2) ** 2 + math.cos(a["lat"] * r) * math.cos(b["lat"] * r) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def fmt_dist(m):
    return f"{round(m / 50) * 50} m" if m < 950 else f"{m / 1000:.1f} km"


def nearest(ev, point):
    pts = ev.get("stops") or ([{"n": "", "lat": ev["lat"], "lon": ev["lon"]}] if ev.get("lat") is not None else [])
    best = None
    for s in pts:
        d = distance(point, s)
        if best is None or d < best[0]:
            best = (d, s)
    return best


def _t(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def paris_time(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    y, mo, d = map(int, day.split("-"))
    return datetime(y, mo, d, h, m, tzinfo=PARIS_TZ)


# ---------- inputs ----------
def parse_code(line):
    """A phone's code -> {"name", "sub", "places"}; None if it isn't one."""
    line = line.strip()
    if not line.startswith(CODE_PREFIX):
        return None
    try:
        raw = line[len(CODE_PREFIX):]
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
        sub = data["s"]
        if not str(sub.get("endpoint", "")).startswith("https://") or not sub.get("keys", {}).get("p256dh") or not sub["keys"].get("auth"):
            return None
        places = [{"name": str(p[0])[:40], "lat": float(p[1]), "lon": float(p[2])} for p in data.get("p", [])
                  if -90 <= float(p[1]) <= 90 and -180 <= float(p[2]) <= 180][:10]
        return {"name": str(data.get("n") or "A phone")[:30], "sub": sub, "places": places}
    except Exception:
        return None


def devices_url(trip_url):
    """push-devices.txt sits next to itinerary.json in the same gist."""
    base = trip_url.split("?", 1)[0]
    return re.sub(r"/[^/]*$", "/push-devices.txt", base)


def watch_points(devices, itinerary):
    """Always-watched places from every phone, plus the itinerary hotel, without near-duplicates."""
    pts = []
    hotel = (itinerary or {}).get("trip", {}).get("hotel") or {}
    if isinstance(hotel.get("lat"), (int, float)) and isinstance(hotel.get("lon"), (int, float)):
        pts.append({"name": hotel.get("name") or "the hotel", "lat": hotel["lat"], "lon": hotel["lon"]})
    for dev in devices:
        for p in dev["places"]:
            if not any(distance(p, q) < 150 for q in pts):
                pts.append(p)
    return pts


def upcoming_stops(itinerary, now):
    out = []
    for day in (itinerary or {}).get("days", []):
        for s in day.get("stops", []):
            if not isinstance(s.get("lat"), (int, float)) or not re.fullmatch(r"\d{2}:\d{2}", s.get("time", "")):
                continue
            at = paris_time(day["date"], s["time"])
            if now - STOPS_FROM <= at <= now + STOPS_TO:
                out.append({"id": s["id"], "name": s.get("title", "Stop"), "lat": s["lat"], "lon": s["lon"], "at": at, "time": s["time"]})
    return out


# ---------- what's red ----------
def red_alerts(events, places, stops, now):
    alerts = []
    for ev in events:
        if ev.get("status") == "ended":
            continue
        start, end = _t(ev.get("start")), _t(ev.get("end"))
        if ev.get("cat") == "weather" and ev.get("paris") and ev.get("tag") == "red" and ev.get("status") == "active":
            until = end.astimezone(PARIS_TZ).strftime("%a %H:%M") if end else "further notice"
            alerts.append({"key": f"w:{ev['id']}", "title": "Red weather warning in Paris",
                           "body": f"{ev.get('hazard', 'Dangerous weather').capitalize()} until {until}. Stay indoors if you can; follow official advice.",
                           "ev": ev["id"]})
            continue
        if ev.get("cat") != "demo":
            continue
        near_term = ev.get("status") == "active" or (start is not None and start - now <= NEAR_TERM)
        where = lambda n: f" · {n[1]['n']}" if n[1].get("n") else ""
        if near_term:
            for p in places:
                n = nearest(ev, p)
                if n and n[0] <= RED_M:
                    when = "now" if ev.get("status") == "active" else f"from {start.astimezone(PARIS_TZ):%H:%M}"
                    alerts.append({"key": f"p:{round(p['lat'], 3)},{round(p['lon'], 3)}:{ev['id']}",
                                   "title": f"Demonstration near {p['name']}",
                                   "body": f"Demonstration {fmt_dist(n[0])} away{where(n)} · {when}. Open Paris Pulse for details.", "ev": ev["id"]})
        for s in stops:
            lo, hi = s["at"] - STOP_BEFORE, s["at"] + STOP_AFTER
            if (end is not None and end < lo) or (start is not None and start > hi):
                continue
            n = nearest(ev, s)
            if n and n[0] <= RED_M:
                alerts.append({"key": f"s:{s['id']}:{ev['id']}", "title": f"Demonstration near {s['name']} ({s['time']})",
                               "body": f"Demonstration {fmt_dist(n[0])} away{where(n)} around that time. Consider changing plans.", "ev": ev["id"]})
    return alerts


# ---------- sending ----------
def _hash(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()[:16]


def load_cache(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {"sent": {}, "welcomed": []}


def send(dev, payload, key, subject, webpush):
    """Returns None when delivered, else a short reason."""
    try:
        webpush(subscription_info=dev["sub"], data=json.dumps(payload), vapid_private_key=key,
                vapid_claims={"sub": subject}, ttl=6 * 3600, headers={"Urgency": "high"}, timeout=TIMEOUT)
        return None
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return "expired" if status in (404, 410) else f"failed ({status or type(exc).__name__})"


def run(events, now, env=None, get=None, webpush=None, cache_path=CACHE_PATH):
    """Sends what's new. Returns a short note for the run summary (no names or places)."""
    env = os.environ if env is None else env
    key, trip_url = env.get("PUSH_VAPID_PRIVATE_KEY", "").strip(), env.get("PUSH_TRIP_URL", "").strip()
    if not key or not trip_url:
        return "off (secrets not set)"
    get = get or requests.get
    if webpush is None:
        from pywebpush import webpush
    subject = env.get("PUSH_VAPID_SUBJECT", "").strip() or DEFAULT_SUBJECT

    itinerary = None
    try:
        r = get(trip_url, timeout=TIMEOUT); r.raise_for_status(); itinerary = r.json()
    except Exception as exc:
        itinerary_note = f"itinerary unavailable ({type(exc).__name__})"
    else:
        itinerary_note = ""
    try:
        r = get(devices_url(trip_url), timeout=TIMEOUT); r.raise_for_status()
        devices = [d for d in (parse_code(l) for l in r.text.splitlines()) if d]
    except Exception as exc:
        return f"no phones (push-devices.txt unavailable: {type(exc).__name__})"
    if not devices:
        return "no phones registered yet"

    cache = load_cache(cache_path)
    sent, welcomed = cache.get("sent", {}), set(cache.get("welcomed", []))
    problems = []

    for dev in devices:                                   # first time we see a phone: confirm it works
        h = _hash(dev["sub"]["endpoint"])
        if h in welcomed:
            continue
        why = send(dev, {"title": "Closed-app alerts are on", "body": f"{dev['name']}: you'll get red alerts even when Paris Pulse is closed.",
                         "tag": "pulse-welcome", "url": "./"}, key, subject, webpush)
        if why is None:
            welcomed.add(h)
        else:
            problems.append(why)

    alerts = [a for a in red_alerts(events, watch_points(devices, itinerary), upcoming_stops(itinerary, now), now) if a["key"] not in sent]
    delivered = 0
    for a in alerts:
        payload = {"title": a["title"], "body": a["body"], "tag": a["key"][:60], "url": f"./#ev={a['ev']}", "red": True}
        ok = [send(dev, payload, key, subject, webpush) for dev in devices]
        if any(x is None for x in ok):
            sent[a["key"]] = now.isoformat(); delivered += 1
        problems += [x for x in ok if x]

    cutoff = now - KEEP
    cache = {"sent": {k: v for k, v in sent.items() if datetime.fromisoformat(v) >= cutoff}, "welcomed": sorted(welcomed)}
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cache_path).write_text(json.dumps(cache), encoding="utf-8")

    note = f"{len(devices)} phones, {delivered} new red alerts sent"
    if problems:
        exp = problems.count("expired")
        note += f"; {exp} phone(s) need to turn alerts on again" if exp else ""
        other = [p for p in problems if p != "expired"]
        note += f"; {len(other)} send errors ({other[0]})" if other else ""
    return note + (f"; {itinerary_note}" if itinerary_note else "")
