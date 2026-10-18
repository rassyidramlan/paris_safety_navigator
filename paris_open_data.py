"""Adapter for the Paris Open Data "circulation_evenement" dataset.

The dataset lists road works and road closures in Paris. It is NOT a protest
or public-safety feed. Each record looks like:

    {"id": "20261019T000000_4436_1352",
     "starttime": "2026-10-19T00:00:00+02:00", "endtime": "2026-11-20T00:00:00+02:00",
     "description": "...", "type": "CONSTRUCTION" | "ROAD_CLOSED",
     "subtype": null | "ROAD_CLOSED_EVENT" | "ROAD_CLOSED_CONSTRUCTION",
     "street": "Av_Victor_Hugo", "polyline": "lat lon lat lon ...",
     "direction": "ONE_DIRECTION"}

This module only fetches and normalises records; it does not judge relevance.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

PARIS_TZ = ZoneInfo("Europe/Paris")
PAGE_SIZE = 100          # maximum records the API returns per request
MAX_RECORDS = 500        # safety cap: stop paging after this many records
TIMEOUT_SECONDS = 15
HEADERS = {"User-Agent": "PersonalParisSafetyNavigator/0.2"}

TYPE_LABELS = {"CONSTRUCTION": "Roadworks", "ROAD_CLOSED": "Road closed"}
SUBTYPE_LABELS = {
    "ROAD_CLOSED_EVENT": "Road closed (event)",
    "ROAD_CLOSED_CONSTRUCTION": "Road closed (works)",
}
LONG_TERM_DAYS = 365     # restrictions longer than this are treated as long-term


def parse_paris_time(value):
    """Read a source timestamp as Paris local time.

    The source writes every timestamp with a fixed "+02:00" offset, even for
    winter dates when Paris is actually on +01:00. Trusting that offset would
    shift December times by an hour, so the offset is ignored and the clock
    time is interpreted in Europe/Paris (which applies CET/CEST correctly).
    """
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return None
    return naive.replace(tzinfo=PARIS_TZ)


def parse_polyline(text):
    """Turn "lat lon lat lon ..." into [(lat, lon), ...]; [] if unusable."""
    numbers = []
    for part in str(text or "").split():
        try:
            numbers.append(float(part))
        except ValueError:
            return []
    if len(numbers) < 2 or len(numbers) % 2:
        return []
    points = [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers), 2)]
    # Reject anything outside the Paris region rather than mapping it wrongly.
    if not all(48.0 <= lat <= 49.5 and 1.0 <= lon <= 4.0 for lat, lon in points):
        return []
    return points


def tidy_street(value):
    return " ".join(str(value or "").replace("_", " ").split())


def time_status(start, end, now):
    """'ended', 'upcoming' or 'active', based only on the source's start/end."""
    if end and end < now:
        return "ended"
    if start and start > now:
        return "upcoming"
    return "active"


def normalise(record, source, now):
    start = parse_paris_time(record.get("starttime"))
    end = parse_paris_time(record.get("endtime"))
    type_code = str(record.get("type") or "")
    label = SUBTYPE_LABELS.get(record.get("subtype")) or TYPE_LABELS.get(type_code) \
        or (type_code.replace("_", " ").capitalize() or "Traffic event")
    street = tidy_street(record.get("street"))
    points = parse_polyline(record.get("polyline"))
    middle = points[len(points) // 2] if points else (None, None)
    return {
        "event_id": f"paris_od:{record.get('id')}",
        "title": f"{label} — {street}" if street else label,
        "description": str(record.get("description") or "")[:500],
        "source": source.get("name", "Paris Open Data"),
        "url": source.get("page_url") or source["url"],
        "start": start,
        "end": end,
        "published": None,  # the source gives a planned time window, not a publication time
        "status": time_status(start, end, now),
        "long_term": bool(start and end and (end - start).days > LONG_TERM_DAYS),
        "points": points,
        "lat": middle[0],
        "lon": middle[1],
        "kind": "official_traffic",
    }


def fetch(source, now):
    """Fetch every record (up to MAX_RECORDS) and return (events, warnings)."""
    base_url = source["url"].split("?")[0]
    records, warnings, total, offset = [], [], None, 0
    while offset < MAX_RECORDS:
        response = requests.get(
            base_url,
            params={"limit": PAGE_SIZE, "offset": offset},
            timeout=TIMEOUT_SECONDS,
            headers=HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("results") or []
        total = payload.get("total_count", total)
        records.extend(batch)
        offset += len(batch)
        if not batch or (total is not None and offset >= total):
            break
    if total is not None and total > len(records):
        warnings.append(
            f"Paris Open Data: showing {len(records)} of {total} records (safety cap reached)."
        )
    return [normalise(r, source, now) for r in records], warnings
