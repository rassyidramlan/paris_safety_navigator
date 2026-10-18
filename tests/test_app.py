"""Headless tests for app.py using Streamlit's AppTest.

Live sources are never called: the API is replaced with records dated relative
to today, so these tests keep working as time passes.
Run from the repository folder with:  python -m pytest -q
"""
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import streamlit as st
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")
PARIS = ZoneInfo("Europe/Paris")
BASTILLE = "48.8530 2.3690 48.8534 2.3699"


def stamp(days):
    """Source-style timestamp (with the source's fixed +02:00) `days` from now."""
    return (datetime.now(PARIS) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"


def record(rid, start, end, street="Rue_de_Test", type_="CONSTRUCTION", subtype=None, polyline=BASTILLE):
    return {"id": rid, "starttime": start, "endtime": end, "description": f"Travaux {rid}",
            "type": type_, "subtype": subtype, "street": street, "polyline": polyline}


RECORDS = [
    record("active", stamp(-2), stamp(10), street="Rue_de_la_Roquette"),
    record("upcoming", stamp(1), stamp(5), street="Bd_Voltaire"),
    record("far_future", stamp(20), stamp(30), street="Rue_Lointaine"),
    record("ended", stamp(-30), stamp(-1), street="Rue_Finie"),
    record("bus_lane", stamp(-400), stamp(1500), street="Rivoli", type_="ROAD_CLOSED",
           subtype="ROAD_CLOSED_EVENT"),
    record("no_coords", stamp(-1), stamp(3), street="Rue_Sans_Carte", polyline=""),
]


def api(records, status=200):
    def fake_get(url, params=None, **kwargs):
        response = mock.Mock()
        if status >= 400:
            response.raise_for_status.side_effect = RuntimeError(f"HTTP {status}")
        else:
            response.raise_for_status.return_value = None
        start, limit = params["offset"], params["limit"]
        response.json.return_value = {"total_count": len(records),
                                      "results": records[start:start + limit]}
        return response
    return fake_get


def run_app(fake_get):
    st.cache_data.clear()  # each test must fetch its own sample data
    with mock.patch("paris_open_data.requests.get", fake_get):
        at = AppTest.from_file(APP, default_timeout=30)
        at.run()
    return at


def all_text(at):
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption]
    parts += [str(e.body) for e in at.info] + [str(e.body) for e in at.warning]
    return "\n".join(str(p) for p in parts)


def test_readable_titles_and_default_filters():
    at = run_app(api(RECORDS))
    assert not at.exception
    text = all_text(at)
    assert "Roadworks — Rue de la Roquette" in text      # active: shown
    assert "Roadworks — Bd Voltaire" in text              # starts within 3 days: shown
    assert "Rue Lointaine" not in text                    # starts in 20 days: hidden
    assert "Rue Finie" not in text                        # ended: hidden
    assert "Rivoli" not in text                           # long-term: hidden by default
    assert "CONSTRUCTION" not in text                     # no raw type codes as titles


def test_timing_shown_in_plain_language_and_paris_time():
    text = all_text(run_app(api(RECORDS)))
    assert "Active now · until" in text
    assert "Starts " in text and "(Paris time)" in text


def test_map_counts_only_items_with_coordinates():
    text = all_text(run_app(api(RECORDS)))
    # Shown: active, upcoming, no_coords -> 3 listed, 2 mappable.
    assert "Showing 2 of 3 listed items" in text


def test_long_term_filter_can_be_switched_on():
    at = run_app(api(RECORDS))
    at.sidebar.checkbox[1].check().run()
    assert "Road closed (event) — Rivoli" in all_text(at)


def test_unused_trip_inputs_removed():
    at = run_app(api(RECORDS))
    assert len(at.text_input) == 0 and len(at.date_input) == 0


def test_source_failure_does_not_crash():
    at = run_app(api([], status=503))
    assert not at.exception
    text = all_text(at)
    assert "does not mean there are no protests or disruptions" in text
