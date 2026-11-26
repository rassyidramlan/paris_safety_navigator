"""Météo-France weather warnings for Paris and the Île-de-France départements, via MeteoAlarm.

Météo-France's "vigilance" warnings are republished on MeteoAlarm, the European weather-warning
service, as a public Atom feed with no key: one entry per warning and département, with CAP fields
for the hazard, level and times. MeteoAlarm levels map to Météo-France colours by CAP severity:
Moderate = yellow, Severe = orange, Extreme = red ("Minor" means no warning and is skipped).

Paris itself (département 75) affects every place and stop. The other Île-de-France départements
matter for day trips: La Vallée Village (Seine-et-Marne), Versailles (Yvelines) and CDG
(Val-d'Oise), so they're kept and marked with their département.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

FEED = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-france"
HEADERS = {"User-Agent": "ParisPulse/1.0 (personal travel-safety app; github.com/rassyidramlan/paris_safety_navigator)"}
TIMEOUT = 30
LEVELS = {"moderate": "yellow", "severe": "orange", "extreme": "red"}
REGION = {"paris": "Paris", "hauts-de-seine": "Hauts-de-Seine", "seine-saint-denis": "Seine-Saint-Denis",
          "val-de-marne": "Val-de-Marne", "seine-et-marne": "Seine-et-Marne", "yvelines": "Yvelines",
          "essonne": "Essonne", "val-d'oise": "Val-d'Oise", "val d'oise": "Val-d'Oise"}
HAZARDS = [("snow", "snow and ice"), ("ice", "snow and ice"), ("verglas", "snow and ice"), ("wind", "strong wind"),
           ("thunder", "thunderstorms"), ("orage", "thunderstorms"), ("flood", "flooding"), ("rain", "heavy rain"),
           ("cold", "extreme cold"), ("heat", "heatwave"), ("canicule", "heatwave"), ("avalanche", "avalanches"),
           ("wave", "high waves"), ("fog", "fog")]


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _fields(entry):
    """Child elements of an Atom entry by local name (cap:event -> 'event'), plus links."""
    out = {}
    for el in entry.iter():
        name = _local(el.tag)
        if name == "link" and el.get("href"):
            out.setdefault("links", []).append(el.get("href"))
        elif el.text and el.text.strip() and name not in out:
            out[name] = el.text.strip()
    return out


def _when(text):
    if not text:
        return None
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


CODES = {"75": "Paris", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis", "94": "Val-de-Marne",
         "77": "Seine-et-Marne", "78": "Yvelines", "91": "Essonne", "95": "Val-d'Oise"}


def region_of(area, code):
    """Île-de-France département for a warning, from its area name or EMMA code (FR075…). None elsewhere."""
    a = (area or "").lower().replace("’", "'")
    for key, name in REGION.items():
        if a == key or a.startswith(key + " "):
            return name
    if "paris" in a:                       # "Paris et petite couronne"
        return "Paris"
    m = re.fullmatch(r"FR0?(\d{2})", (code or "").strip(), re.I)
    return CODES.get(m.group(1)) if m else None


def hazard(event):
    e = (event or "").lower()
    return next((label for key, label in HAZARDS if key in e), re.sub(r"(?i)\b(yellow|orange|red|warning|moderate|severe|extreme)\b", "", event or "").strip().lower() or "weather")


def parse(xml_text, now):
    root = ET.fromstring(xml_text)
    out, seen = [], set()
    for entry in (el for el in root.iter() if _local(el.tag) == "entry"):
        f = _fields(entry)
        region = region_of(f.get("areadesc"), f.get("value"))
        level = LEVELS.get((f.get("severity") or "").lower())
        if not region or not level or (f.get("message_type") or f.get("msgtype") or "").lower() == "cancel":
            continue
        start = _when(f.get("onset")) or _when(f.get("effective"))
        end = _when(f.get("expires"))
        if not end or end < now:
            continue
        what = hazard(f.get("event"))
        key = (region, level, what, start, end)
        if key in seen:
            continue
        seen.add(key)
        title = f"{level.capitalize()} warning: {what} · {region}"
        out.append({
            "id": f"wx:{region}:{what}:{level}:{(start or now).strftime('%Y%m%d%H')}",
            "kind": "weather", "cat": "weather", "tag": level, "hazard": what, "region": region, "paris": region == "Paris",
            "title": title, "title_en": title,
            "text": f"Météo-France {level} warning for {what} in {region}." + (" Orange and red warnings mean dangerous weather: follow official advice." if level != "yellow" else " Be careful if you're outdoors."),
            "lines": [], "modes": [], "stops": [], "lat": None, "lon": None, "pts": [],
            "status": "active" if not start or start <= now else "upcoming",
            "start": (start or now).isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
            "updated": None, "sev": None, "src": "Météo-France via MeteoAlarm",
            "url": "https://vigilance.meteofrance.fr/fr", "lt": False, "why": None, "lang": "en",
        })
    rank = {"red": 0, "orange": 1, "yellow": 2}
    return sorted(out, key=lambda e: (not e["paris"], rank[e["tag"]], e["start"]))


def fetch(now, get=None):
    get = get or requests.get
    r = get(FEED, timeout=TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    return parse(r.text, now)
