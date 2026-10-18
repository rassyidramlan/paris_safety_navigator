"""Tests for collect.py: the file the mobile app reads."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import collect

FIX = Path(__file__).parent / "fixtures"
PRIM = json.loads((FIX / "prim_disruptions_sample.json").read_text(encoding="utf-8"))
ROADS = json.loads((FIX / "paris_open_data_sample.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 18, 13, 40, tzinfo=timezone.utc)
CONFIG = {"paris_open_data": {"enabled": True, "name": "Paris Open Data",
                              "url": "https://opendata.paris.fr/x/records", "page_url": "https://opendata.paris.fr/p"}}


def fake_get(prim_status=200):
    def get(url, params=None, headers=None, **kwargs):
        r = mock.Mock(status_code=prim_status if "prim" in url else 200)
        r.raise_for_status.side_effect = None if r.status_code < 400 else RuntimeError(f"HTTP {r.status_code}")
        if "prim" in url:
            r.json.return_value = PRIM
        else:
            o = params["offset"]
            r.json.return_value = {"total_count": len(ROADS["results"]), "results": ROADS["results"][o:o + params["limit"]]}
        return r
    return get


def run(**kw):
    with mock.patch("requests.get", fake_get(**kw)):
        return collect.collect(now=NOW, api_key="secret-token", config=CONFIG)


def test_rail_stations_get_coordinates():
    out = run()
    rail = [e for e in out["events"] if any(m in ("Metro", "RapidTransit") for m in e["modes"])]
    assert rail and sum(1 for e in rail if e["stops"]) / len(rail) > 0.6
    republique = [s for e in out["events"] for s in e["stops"] if s["n"] == "République"]
    assert republique and abs(republique[0]["lat"] - 48.867) < 0.01


def test_categories_and_filters():
    out = run()
    cats = {e["cat"] for e in out["events"]}
    assert {"demo", "road"} <= cats
    assert all(e["status"] != "ended" for e in out["events"])
    # Buses only when the operator cites a demonstration or strike.
    assert all(e["cat"] in ("demo", "strike") for e in out["events"] if e["modes"] == ["Bus"])


def test_no_secret_in_output():
    out = run()
    assert "secret-token" not in json.dumps(out)


def test_one_failed_source_keeps_the_other():
    out = run(prim_status=401)
    idfm = next(s for s in out["sources"] if s["id"] == "idfm")
    roads = next(s for s in out["sources"] if s["id"] == "roads")
    assert not idfm["ok"] and "secret-token" not in (idfm["error"] or "")
    assert roads["ok"] and roads["count"] > 0


def test_main_writes_file(tmp_path):
    target = tmp_path / "data.json"
    with mock.patch("requests.get", fake_get()), mock.patch.dict("os.environ", {"PRIM_API_KEY": "k"}):
        assert collect.main(["--out", str(target)]) == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["generated_at"] and data["events"]
