"""Adapter for Île-de-France Mobilités (PRIM) "Traffic Info Messages - Global Query".

Official public-transport disruption messages from the operators (RATP, SNCF, etc.)
for Métro, RER, Transilien, tram and bus lines in Île-de-France.

API:   GET https://prim.iledefrance-mobilites.fr/marketplace/disruptions_bulk/disruptions/v2
Auth:  HTTP header "apiKey" with a personal PRIM token (free account).
Terms: Île-de-France Mobilités "Mobility License"; attribution to IDFM is shown in the app.

Response shape (abridged):
    {"disruptions": [{"id", "title", "message" (HTML), "shortMessage",
                      "severity": "BLOQUANTE" | "PERTURBEE" | "INFORMATION",
                      "cause": "TRAVAUX" | "PERTURBATION" | "INFORMATION",
                      "applicationPeriods": [{"begin": "20261018T142700", "end": "..."}],
                      "lastUpdate": "20261018T153010"}],
     "lines": [{"id", "name", "shortName", "mode", "impactedObjects":
                [{"type": "line" | "stop_point" | "network", "name", "disruptionIds"}]}],
     "lastUpdatedDate": "2026-10-18T13:37:15.835Z"}

All timestamps except lastUpdatedDate are Paris local time without an offset.
The feed has no coordinates, so these items are listed, not mapped.
"""
import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

URL = "https://prim.iledefrance-mobilites.fr/marketplace/disruptions_bulk/disruptions/v2"
PAGE_URL = "https://www.iledefrance-mobilites.fr/info-trafic"
SOURCE_NAME = "Île-de-France Mobilités — traffic info"
PARIS_TZ = ZoneInfo("Europe/Paris")
TIMEOUT_SECONDS = 20

MODE_LABELS = {
    "Metro": "Métro",
    "RapidTransit": "RER",
    "LocalTrain": "Transilien",
    "Tramway": "Tram",
    "RailShuttle": "Shuttle",
    "Bus": "Bus",
}
RAIL_MODES = {"Metro", "RapidTransit", "LocalTrain", "Tramway", "RailShuttle"}
SEVERITY_LABELS = {"BLOQUANTE": "Blocking", "PERTURBEE": "Disrupted", "INFORMATION": "Information"}
CAUSE_LABELS = {"TRAVAUX": "Planned works", "PERTURBATION": "Disruption", "INFORMATION": "Information"}

# Words operators use when a demonstration or strike is the stated reason.
# English words match in any case; French words only in lowercase, so stop names
# such as "Les Grèves" or "Place de la Manifestation" don't trigger a match.
DEMO_PATTERN = re.compile(r"\b(?i:demonstrations?|protests?|rall(?:y|ies))\b|\b(?:manifestations?|rassemblements?|manifestants?)\b")
STRIKE_PATTERN = re.compile(r"\b(?i:strikes?|social movements?|industrial action)\b|\b(?:grèves?|greves?|mouvements? sociaux|mouvement social|préavis)\b")


def protest_match(text):
    """(kind, matched word): kind is 'demonstration', 'strike' or None."""
    m = DEMO_PATTERN.search(text)
    if m:
        return "demonstration", m.group(0)
    m = STRIKE_PATTERN.search(text)
    if m:
        return "strike", m.group(0)
    return None, None


def protest_kind(text):
    """'demonstration', 'strike' or None for an operator message."""
    return protest_match(text)[0]


# Kept for compatibility: matches either kind.
class _Either:
    @staticmethod
    def search(text):
        return protest_kind(text) is not None


PROTEST_PATTERN = _Either()


def parse_local(value):
    """'20261018T142700' (Paris local time) -> aware datetime, or None."""
    try:
        return datetime.strptime(str(value), "%Y%m%dT%H%M%S").replace(tzinfo=PARIS_TZ)
    except (TypeError, ValueError):
        return None


def clean_message(value):
    """Strip HTML tags and entities from an operator message."""
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def period_status(periods, now):
    """Return (status, relevant_start, relevant_end) from the application periods.

    'active' if now falls in a period, otherwise 'upcoming' with the next period,
    otherwise 'ended' with the last period.
    """
    parsed = sorted(
        (p for p in ((parse_local(x.get("begin")), parse_local(x.get("end"))) for x in periods or [])
         if p[0] or p[1]),
        key=lambda p: p[0] or p[1],
    )
    if not parsed:
        return "active", None, None   # no period given: the operator lists it as current
    for begin, end in parsed:
        if (begin is None or begin <= now) and (end is None or now <= end):
            return "active", begin, end
    future = [p for p in parsed if p[0] and p[0] > now]
    if future:
        return "upcoming", future[0][0], future[0][1]
    return "ended", parsed[-1][0], parsed[-1][1]


def index_lines(lines):
    """disruption id -> {"lines": [(mode, shortName)], "stops": [names], "stop_refs": [(id, name)]}"""
    index = {}
    for line in lines or []:
        mode = line.get("mode")
        short = line.get("shortName") or line.get("name") or "?"
        for obj in line.get("impactedObjects") or []:
            for did in obj.get("disruptionIds") or []:
                entry = index.setdefault(did, {"lines": [], "stops": [], "stop_refs": []})
                if (mode, short) not in entry["lines"]:
                    entry["lines"].append((mode, short))
                if obj.get("type") == "stop_point" and obj.get("name"):
                    if obj["name"] not in entry["stops"]:
                        entry["stops"].append(obj["name"])
                    ref = (obj.get("id"), obj["name"])
                    if ref[0] and ref not in entry["stop_refs"]:
                        entry["stop_refs"].append(ref)
    return index


def line_label(mode, short):
    label = MODE_LABELS.get(mode, mode or "Line")
    return f"{label} {short}"


def normalise(disruption, impacted, now):
    status, start, end = period_status(disruption.get("applicationPeriods"), now)
    lines = impacted.get("lines", [])
    title = str(disruption.get("title") or disruption.get("shortMessage") or "Transport disruption")
    message = clean_message(disruption.get("message"))
    return {
        "event_id": f"idfm:{disruption.get('id')}",
        "title": title[:240],
        "description": message[:700],
        "source": SOURCE_NAME,
        "url": PAGE_URL,
        "start": start,
        "end": end,
        "published": None,
        "updated": parse_local(disruption.get("lastUpdate")),
        "status": status,
        "long_term": bool(start and end and (end - start).days > 365),
        "severity": SEVERITY_LABELS.get(disruption.get("severity"), "Unknown"),
        "cause": CAUSE_LABELS.get(disruption.get("cause"), "Unknown"),
        "lines": [line_label(m, s) for m, s in lines],
        "is_rail": any(m in RAIL_MODES for m, _ in lines),
        "is_bus": any(m == "Bus" for m, _ in lines),
        "stops": impacted.get("stops", [])[:20],
        "stop_refs": impacted.get("stop_refs", [])[:40],
        "modes": sorted({m for m, _ in lines if m}),
        "protest_kind": protest_match(title + " " + message)[0],
        "protest_word": protest_match(title + " " + message)[1],
        "protest_related": protest_match(title + " " + message)[0] is not None,
        "points": [],
        "lat": None,
        "lon": None,
        "kind": "transport",
    }


def parse_payload(payload, now):
    index = index_lines(payload.get("lines"))
    events = [normalise(d, index.get(d.get("id"), {}), now) for d in payload.get("disruptions") or []]
    feed_updated = None
    try:
        feed_updated = datetime.fromisoformat(str(payload.get("lastUpdatedDate")).replace("Z", "+00:00"))
    except ValueError:
        pass
    return events, feed_updated


def fetch(api_key, now):
    """Return (events, feed_updated_utc). Raises on HTTP or parsing errors."""
    if not api_key:
        raise ValueError("PRIM_API_KEY is not configured")
    response = requests.get(
        URL,
        # Ask for English; operators who only publish in French still come back in French.
        headers={"apiKey": api_key, "Accept": "application/json", "Accept-Language": "en"},
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code in (401, 403):
        raise PermissionError(f"PRIM rejected the API key (HTTP {response.status_code})")
    response.raise_for_status()
    return parse_payload(response.json(), now)
