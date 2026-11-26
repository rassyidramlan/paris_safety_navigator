"""Tests for weather.py (Météo-France warnings via MeteoAlarm).

The fixture follows the MeteoAlarm legacy Atom layout: one <entry> per warning and département,
with CAP fields (cap:areaDesc, cap:event, cap:severity, cap:onset, cap:expires, cap:geocode).
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import weather

XML = (Path(__file__).parent / "fixtures" / "meteoalarm_france_sample.xml").read_text(encoding="utf-8")
NOW = datetime(2026, 12, 9, 10, tzinfo=timezone.utc)


def test_keeps_ile_de_france_warnings_only_in_effect():
    evs = weather.parse(XML, NOW)
    assert [(e["region"], e["tag"], e["hazard"]) for e in evs] == [("Paris", "orange", "snow and ice"), ("Seine-et-Marne", "yellow", "strong wind")]


def test_paris_warning_is_city_wide_and_active():
    paris = weather.parse(XML, NOW)[0]
    assert paris["cat"] == "weather" and paris["paris"] and paris["status"] == "active"
    assert paris["title"] == "Orange warning: snow and ice · Paris"
    assert paris["stops"] == [] and paris["end"] == "2026-12-10T06:00:00+00:00"
    assert weather.parse(XML, NOW)[1]["status"] == "upcoming"


def test_region_from_name_or_code():
    assert weather.region_of("Paris et petite couronne", None) == "Paris"
    assert weather.region_of("", "FR095") == "Val-d'Oise"
    assert weather.region_of("Val-d’Oise", None) == "Val-d'Oise"
    assert weather.region_of("Finistère", "FR029") is None


def test_fetch_uses_the_public_feed():
    r = mock.Mock(text=XML); r.raise_for_status.return_value = None
    get = mock.Mock(return_value=r)
    assert len(weather.fetch(NOW, get=get)) == 2
    assert get.call_args[0][0] == weather.FEED
