"""Tests for the Paris Open Data adapter, using a real saved API response.

tests/fixtures/paris_open_data_sample.json is 20 real records saved on 18 Oct 2026.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import paris_open_data as pod

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "paris_open_data_sample.json").read_text(encoding="utf-8")
)
SOURCE = {
    "name": "Paris Open Data — traffic events",
    "url": "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/circulation_evenement/records?limit=100",
    "page_url": "https://opendata.paris.fr/explore/dataset/circulation_evenement/",
}
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def by_id(record_id):
    return next(r for r in FIXTURE["results"] if r["id"] == record_id)


def test_winter_time_ignores_wrong_source_offset():
    # Source says +02:00 for a November date; Paris is really on CET (+01:00) then.
    dt = pod.parse_paris_time("2026-11-20T00:00:00+02:00")
    assert dt.strftime("%Y-%m-%d %H:%M %Z") == "2026-11-20 00:00 CET"


def test_summer_time_kept_as_cest():
    dt = pod.parse_paris_time("2027-03-29T02:00:00+02:00")
    assert dt.strftime("%H:%M %Z") == "02:00 CEST"


def test_missing_or_bad_times_are_none():
    assert pod.parse_paris_time(None) is None
    assert pod.parse_paris_time("not a date") is None


def test_polyline_parsed_into_points():
    pts = pod.parse_polyline(by_id("20261019T000000_4436_1352")["polyline"])
    assert len(pts) == 2
    assert round(pts[0][0], 4) == 48.8711 and round(pts[0][1], 4) == 2.2889


def test_bad_polyline_is_not_mapped():
    assert pod.parse_polyline("") == []
    assert pod.parse_polyline("48.8 2.3 48.9") == []        # odd number of values
    assert pod.parse_polyline("40.0 -74.0 40.1 -74.1") == []  # not in Paris


def test_normalised_record_is_readable():
    event = pod.normalise(by_id("20261019T000000_4436_1352"), SOURCE, NOW)
    assert event["title"] == "Roadworks — Av Victor Hugo"
    assert event["event_id"] == "paris_od:20261019T000000_4436_1352"
    assert event["status"] == "upcoming"          # starts 19 Oct, after NOW
    assert event["url"] == SOURCE["page_url"]
    assert event["lat"] is not None


def test_status_active_ended_and_long_term():
    active = pod.normalise(by_id("20260914T080112_212_3291"), SOURCE, NOW)
    assert active["status"] == "active"
    ended = pod.normalise(by_id("20260914T080112_212_3291"), SOURCE,
                          datetime(2026, 12, 3, tzinfo=timezone.utc))
    assert ended["status"] == "ended"
    bus_lane = pod.normalise(by_id("20251119T094336_25_1062"), SOURCE, NOW)
    assert bus_lane["long_term"] is True
    assert bus_lane["title"] == "Road closed (event) — Rivoli"


def test_open_ended_record_is_active():
    event = pod.normalise(by_id("20261013T130815_6022_2371"), SOURCE, NOW)  # endtime is null
    assert event["end"] is None and event["status"] == "active"


class PagedAPI:
    """Fake API that honours limit/offset like the real one."""

    def __init__(self, records, total=None):
        self.records, self.total, self.calls = records, total, 0

    def __call__(self, url, params=None, **kwargs):
        self.calls += 1
        start, limit = params["offset"], params["limit"]
        payload = {"total_count": self.total if self.total is not None else len(self.records),
                   "results": self.records[start:start + limit]}
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        return response


def test_fetch_pages_through_all_records():
    records = [dict(FIXTURE["results"][i % 20], id=f"r{i}") for i in range(162)]
    api = PagedAPI(records)
    with mock.patch("paris_open_data.requests.get", api):
        events, warnings = pod.fetch(SOURCE, NOW)
    assert len(events) == 162 and api.calls == 2 and warnings == []


def test_fetch_stops_at_safety_cap_and_warns():
    records = [dict(FIXTURE["results"][0], id=f"r{i}") for i in range(600)]
    with mock.patch("paris_open_data.requests.get", PagedAPI(records)):
        events, warnings = pod.fetch(SOURCE, NOW)
    assert len(events) == pod.MAX_RECORDS
    assert "showing 500 of 600" in warnings[0]
