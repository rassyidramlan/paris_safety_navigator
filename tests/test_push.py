"""Tests for push.py (closed-app alerts). Network and Web Push are replaced by fakes."""
import base64
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import push

NOW = datetime(2026, 12, 9, 11, 0, tzinfo=timezone.utc)          # 12:00 in Paris
HOTEL = ["Hotel", 48.8858, 2.3700]
SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPk", "auth": "au"}}
SUB2 = {"endpoint": "https://web.push.apple.com/xyz", "keys": {"p256dh": "BQq", "auth": "bu"}}


def code(name, sub, places):
    raw = json.dumps({"n": name, "s": sub, "p": places}).encode()
    return push.CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


ITIN = {"schema_version": 1, "trip": {"hotel": {"name": "Moxy Paris La Villette"}},
        "days": [{"id": "d4", "date": "2026-12-09", "stops": [
            {"id": "d4-s6", "time": "14:45", "title": "Galeries Lafayette Haussmann", "lat": 48.8738, "lon": 2.3320},
            {"id": "d4-s8", "time": "17:15", "title": "Montparnasse 56", "lat": 48.8421, "lon": 2.3219}]}]}


def demo(id, lat, lon, status="active", start="2026-12-09T10:30:00+00:00", end="2026-12-09T17:00:00+00:00", name="X"):
    return {"id": id, "cat": "demo", "status": status, "start": start, "end": end, "stops": [{"n": name, "lat": lat, "lon": lon}]}


def test_code_round_trip_and_rejects_junk():
    d = push.parse_code(code("Aisyah", SUB, [HOTEL]))
    assert d["name"] == "Aisyah" and d["sub"]["endpoint"] == SUB["endpoint"] and d["places"][0]["lat"] == 48.8858
    assert push.parse_code("hello") is None
    assert push.parse_code(push.CODE_PREFIX + "!!!") is None
    assert push.parse_code(code("x", {"endpoint": "http://insecure", "keys": {"p256dh": "a", "auth": "b"}}, [])) is None


def test_devices_file_sits_next_to_the_itinerary():
    assert push.devices_url("https://gist.githubusercontent.com/u/abc/raw/itinerary.json?t=1") == \
        "https://gist.githubusercontent.com/u/abc/raw/push-devices.txt"


def test_red_only_by_the_apps_rules():
    places = [{"name": "Hotel", "lat": 48.8858, "lon": 2.3700}]
    stops = push.upcoming_stops(ITIN, NOW)
    assert [s["id"] for s in stops] == ["d4-s6"]                                       # 14:45 is within 3 h; 17:15 isn't yet
    evs = [demo("near-hotel", 48.8843, 2.3684, name="Stalingrad"),                       # ~200 m from the hotel: red
           demo("far", 48.8700, 2.3500),                                               # ~2 km: amber in the app, not sent
           demo("opera", 48.8711, 2.3317, status="upcoming", start="2026-12-09T13:30:00+00:00", name="Opéra"),  # near the 14:45 stop
           {"id": "wx", "cat": "weather", "paris": True, "tag": "red", "status": "active", "hazard": "strong wind", "end": "2026-12-10T06:00:00+00:00"},
           {"id": "wy", "cat": "weather", "paris": True, "tag": "orange", "status": "active", "hazard": "snow and ice"},
           {"id": "ord", "cat": "order", "status": "active", "stops": [{"n": "", "lat": 48.8858, "lon": 2.37}]}]
    got = {a["key"]: a for a in push.red_alerts(evs, places, stops, NOW)}
    assert set(got) == {"p:48.886,2.37:near-hotel", "s:d4-s6:opera", "w:wx"}
    assert got["p:48.886,2.37:near-hotel"]["title"] == "Demonstration near Hotel"
    assert "200 m away · Stalingrad · now" in got["p:48.886,2.37:near-hotel"]["body"]
    assert got["s:d4-s6:opera"]["title"] == "Demonstration near Galeries Lafayette Haussmann (14:45)"
    assert "until Thu 07:00" in got["w:wx"]["body"]


def test_demonstration_hours_away_isnt_red_for_places_yet():
    later = demo("later", 48.8843, 2.3684, status="upcoming", start="2026-12-09T16:00:00+00:00")
    assert push.red_alerts([later], [{"name": "Hotel", "lat": 48.8858, "lon": 2.3700}], [], NOW) == []


def fake_get(devices_text, itin=ITIN):
    def get(url, timeout=None):
        r = mock.Mock(); r.raise_for_status.return_value = None
        if url.endswith("push-devices.txt"):
            r.text = devices_text
        else:
            r.json.return_value = itin
        return r
    return get


ENV = {"PUSH_VAPID_PRIVATE_KEY": "k", "PUSH_TRIP_URL": "https://gist.githubusercontent.com/u/abc/raw/itinerary.json"}


def test_run_welcomes_each_phone_once_and_sends_each_alert_once(tmp_path):
    cache = tmp_path / "push.json"
    devices = "\n".join(["# family phones", code("Rassyid", SUB, [HOTEL]), code("Aisyah", SUB2, [])])
    sent = []
    wp = lambda **kw: sent.append((kw["subscription_info"]["endpoint"], json.loads(kw["data"])["title"]))
    evs = [demo("near-hotel", 48.8843, 2.3684, name="Stalingrad")]
    note = push.run(evs, NOW, env=ENV, get=fake_get(devices), webpush=wp, cache_path=cache)
    assert note.startswith("2 phones, 1 new red alerts sent")
    assert [t for _, t in sent].count("Closed-app alerts are on") == 2
    assert [t for _, t in sent].count("Demonstration near Hotel") == 2              # both phones
    sent.clear()
    note = push.run(evs, NOW + timedelta(minutes=10), env=ENV, get=fake_get(devices), webpush=wp, cache_path=cache)
    assert sent == [] and "0 new red alerts" in note                               # nothing repeated


def test_expired_phone_is_reported_without_names(tmp_path):
    class Gone(Exception):
        response = mock.Mock(status_code=410)
    def wp(**kw):
        if "apple" in kw["subscription_info"]["endpoint"]:
            raise Gone()
    devices = "\n".join([code("Rassyid", SUB, [HOTEL]), code("Aisyah", SUB2, [])])
    note = push.run([demo("near-hotel", 48.8843, 2.3684)], NOW, env=ENV, get=fake_get(devices), webpush=wp, cache_path=tmp_path / "c.json")
    assert "need to turn alerts on again" in note and "Aisyah" not in note and "Hotel" not in note


def test_off_without_secrets_and_quiet_without_phones(tmp_path):
    assert push.run([], NOW, env={}) == "off (secrets not set)"
    assert push.run([], NOW, env=ENV, get=fake_get(""), webpush=lambda **k: None, cache_path=tmp_path / "c.json") == "no phones registered yet"


def test_hotel_location_from_the_itinerary_counts_too():
    itin = json.loads(json.dumps(ITIN)); itin["trip"]["hotel"].update({"lat": 48.8858, "lon": 2.37})
    pts = push.watch_points([{"places": [{"name": "Hotel (mine)", "lat": 48.8859, "lon": 2.3701}]}], itin)
    assert [p["name"] for p in pts] == ["Moxy Paris La Villette"]                  # same spot counted once
