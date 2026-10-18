"""Headless tests for app.py using Streamlit's AppTest.

Live sources are never called: requests.get is replaced with saved sample data.
Run from the repository folder with:  python -m pytest -q
"""
from pathlib import Path
from unittest import mock

import streamlit as st
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")

# Two sample records shaped like the Paris Open Data API response.
SAMPLE = {
    "results": [
        {
            "titre": "Fermeture de voie - Place de la Bastille",
            "description": "Circulation interrompue pour travaux",
            "date_debut": "2026-12-03T13:30:00+00:00",
            "geo_point_2d": {"lat": 48.8532, "lon": 2.3692},
        },
        {
            "titre": "Livraison de matériel",
            "description": "Stationnement réservé",
            "date_debut": "2026-12-03T08:00:00+00:00",
        },
    ]
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def run_app(response):
    st.cache_data.clear()  # each test must fetch its own sample data
    with mock.patch("requests.get", return_value=response):
        at = AppTest.from_file(APP, default_timeout=30)
        at.run()
    return at


def all_text(at):
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption]
    parts += [str(e.body) for e in at.info] + [str(e.body) for e in at.warning]
    return "\n".join(str(p) for p in parts)


def test_app_runs_and_shows_paris_time():
    at = run_app(FakeResponse(SAMPLE))
    assert not at.exception
    text = all_text(at)
    # 13:30 UTC on 3 Dec is 14:30 in Paris (CET, UTC+1).
    assert "14:30 CET (Paris time)" in text


def test_unused_trip_inputs_removed():
    at = run_app(FakeResponse(SAMPLE))
    assert len(at.text_input) == 0
    assert len(at.date_input) == 0


def test_map_caption_counts_mapped_items():
    at = run_app(FakeResponse(SAMPLE))
    assert "Showing 1 of 2 items" in all_text(at)


def test_source_failure_does_not_crash():
    at = run_app(FakeResponse({}, status=503))
    assert not at.exception
    text = all_text(at)
    assert "does not mean there are no protests or disruptions" in text
