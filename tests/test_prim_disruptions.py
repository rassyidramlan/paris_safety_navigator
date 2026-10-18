"""Tests for the IDFM / PRIM transport disruption adapter.

tests/fixtures/prim_disruptions_sample.json is a trimmed real response saved on
18 Oct 2026 (rail lines, all demonstration-related notices, a few buses).
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import prim_disruptions as prim

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "prim_disruptions_sample.json").read_text(encoding="utf-8")
)
NOW = datetime(2026, 10, 18, 13, 40, tzinfo=timezone.utc)   # 15:40 in Paris


def events():
    return prim.parse_payload(FIXTURE, NOW)[0]


def test_local_times_are_paris_time():
    dt = prim.parse_local("20261203T080000")
    assert dt.strftime("%Y-%m-%d %H:%M %Z") == "2026-12-03 08:00 CET"
    assert prim.parse_local("garbage") is None


def test_feed_update_time_parsed_as_utc():
    _, updated = prim.parse_payload(FIXTURE, NOW)
    assert updated == datetime(2026, 10, 18, 13, 37, 15, 835000, tzinfo=timezone.utc)


def test_html_is_cleaned():
    text = prim.clean_message("<p><RER>Line A&nbsp;is&gt;closed<br/>Reason: work.</p>")
    assert text == "Line A is>closed\nReason: work."


def test_period_status():
    periods = [{"begin": "20261018T142700", "end": "20261018T220000"}]
    assert prim.period_status(periods, NOW)[0] == "active"
    assert prim.period_status([{"begin": "20261020T080000", "end": "20261020T090000"}], NOW)[0] == "upcoming"
    assert prim.period_status([{"begin": "20261001T080000", "end": "20261002T090000"}], NOW)[0] == "ended"
    assert prim.period_status([], NOW)[0] == "active"


def test_upcoming_picks_next_period():
    periods = [{"begin": "20261101T230000", "end": "20261102T030000"},
               {"begin": "20261019T230000", "end": "20261020T030000"}]
    status, start, _ = prim.period_status(periods, NOW)
    assert status == "upcoming" and start.day == 19


def test_lines_and_modes_attached():
    evs = events()
    assert any(e["is_rail"] and any(l.startswith("RER ") for l in e["lines"]) for e in evs)
    assert any(any(l.startswith("Métro ") for l in e["lines"]) for e in evs)
    assert all(e["event_id"].startswith("idfm:") for e in evs)


def test_demonstration_notices_detected():
    demo = [e for e in events() if e["protest_related"]]
    assert len(demo) >= 30
    bus56 = [e for e in demo if "Bus 56" in e["title"]]
    assert bus56 and bus56[0]["status"] == "active"
    assert "Saint-Ambroise" in bus56[0]["description"]


def test_ordinary_works_not_flagged_as_protest():
    works = [e for e in events() if e["cause"] == "Planned works"]
    assert works and not any(e["protest_related"] for e in works if "demonstration" not in e["description"].lower())


def test_protest_pattern_french_and_english():
    for text in ["Motif : manifestation", "en raison d'une grève", "Reason: demonstration",
                 "mouvement social", "strike action"]:
        assert prim.PROTEST_PATTERN.search(text), text
    for text in ["Travaux de voirie", "Reason: work", "manifest destiny of buses"]:
        assert not prim.PROTEST_PATTERN.search(text), text


def response(status=200, payload=None):
    r = mock.Mock(status_code=status)
    r.json.return_value = payload or {}
    r.raise_for_status.side_effect = None if status < 400 else RuntimeError(f"HTTP {status}")
    return r


def test_fetch_sends_key_in_header_only():
    with mock.patch("prim_disruptions.requests.get", return_value=response(200, FIXTURE)) as get:
        evs, _ = prim.fetch("secret-token", NOW)
    assert evs
    args, kwargs = get.call_args
    assert kwargs["headers"]["apiKey"] == "secret-token"
    assert "secret-token" not in args[0]


def test_fetch_reports_bad_key_without_revealing_it():
    with mock.patch("prim_disruptions.requests.get", return_value=response(401)):
        with pytest.raises(PermissionError) as err:
            prim.fetch("secret-token", NOW)
    assert "secret-token" not in str(err.value)


def test_fetch_requires_key():
    with pytest.raises(ValueError):
        prim.fetch("", NOW)
