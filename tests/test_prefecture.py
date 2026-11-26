"""Tests for the Préfecture de Police orders reader.

tests/fixtures/raa_sample.pdf is a short bulletin laid out like the real Paris "recueil des actes
administratifs spécial": cover, contents list, then each order on the pages the contents list gives.
The order titles follow the wording of real 2026 bulletins.
"""
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import news
import prefecture as pp

FIX = Path(__file__).parent / "fixtures"
PDF = (FIX / "raa_sample.pdf").read_bytes()
MATCHERS = news.load_places()

LISTING = """
<ul>
 <li><a href="/ile-de-france/irecontenu/telechargement/150001/1100001/file/recueil-75-2026-701-recueil-des-actes-administratifs-special%20du%2004.12.2026.pdf">
   recueil-75-2026-701-recueil-des-actes-administratifs-special du 04.12.2026</a> PDF - 512 ko</li>
 <li><a href="/ile-de-france/irecontenu/telechargement/150002/1100002/file/recueil-75-2026-702-RAA-nominatifs%20du%2004.12.2026.pdf">
   recueil-75-2026-702-RAA-nominatifs du 04.12.2026</a></li>
 <li><a href="/ile-de-france/irecontenu/telechargement/150003/1100003/file/recueilDS-75-2026-690-recueil-des-actes-administratifs-special%20du%2001.11.2026.pdf">
   recueilDS-75-2026-690-recueil-des-actes-administratifs-special du 01.11.2026</a></li>
 <li><a href="/ile-de-france/Documents-publications/autre">Autre page</a></li>
</ul>"""


def test_listing_keeps_paris_special_bulletins_only():
    items = pp.parse_listing(LISTING)
    assert [i["date"] for i in items] == ["2026-12-04", "2026-11-01"]
    assert items[0]["url"].startswith("https://www.prefectures-regions.gouv.fr/ile-de-france/irecontenu/")


def test_listing_pages_are_this_month_and_last():
    urls = pp.listing_urls(datetime(2026, 12, 6, 10, tzinfo=timezone.utc))
    assert urls[0].endswith("/Raa-du-departement-de-Paris-2026/Decembre/")
    assert urls[1].endswith("/Raa-du-departement-de-Paris-2026/Novembre/")
    assert pp.listing_urls(datetime(2027, 1, 2, tzinfo=timezone.utc))[1].endswith("-2026/Decembre/")


def test_contents_list_is_read_from_the_pdf():
    entries = pp.contents(pp.pdf_pages(PDF))
    assert [e["ref"] for e in entries] == ["75-2026-12-03-00002", "75-2026-12-04-00011", "75-2026-12-04-00012",
                                           "75-2026-12-04-00013", "75-2026-12-04-00014"]
    assert entries[1]["page"] == 5 and entries[1]["pages"] == 3


def test_only_police_orders_about_protests_and_zones_are_kept():
    kinds = {
        "Arrêté portant délégation de signature à Mme X": None,
        "Arrêté n°2026-01704 du 4 décembre 2026 autorisant une société à réaliser un tournage": None,
        "Arrêté 2025-01410 du 24 octobre 2025 portant interdiction partielle d'une manifestation déclarée le 25 octobre 2025": "ban",
        "Arrêté n°2026-01478 du 29 octobre 2026 portant mesures de police applicables à Paris du 29 octobre au 02 novembre 2026": "measures",
        "Arrêté n°2026-01475 du 29 octobre 2026 instituant un périmètre de protection et différentes mesures de police": "zone",
        "Arrêté n°2026-01464 du 28 octobre 2026 portant interdiction temporaire de port et de transport sans motif légitime d'armes": "weapons",
        "Arrêté n° 2026-00075 autorisant la captation d'images au moyen de caméras installées sur des aéronefs à Paris à l'occasion de manifestations": "drones",
    }
    for title, kind in kinds.items():
        assert pp.kind_of(title) == kind, title


def test_days_covered_come_from_the_title_not_the_signing_date():
    t = pp.covered_days
    assert t("Arrêté n°2026-01703 du 4 décembre 2026 portant mesures de police applicables à Paris du 5 au 7 décembre 2026") == (date(2026, 12, 5), date(2026, 12, 7))
    assert t("Arrêté n°2026-01478 du 29 octobre 2026 portant mesures de police applicables à Paris du 29 octobre au 02 novembre 2026") == (date(2026, 10, 29), date(2026, 11, 2))
    assert t("Arrêté n°2026-01475 du 29 octobre 2026 instituant un périmètre les 28 et 29 octobre 2026") == (date(2026, 10, 28), date(2026, 10, 29))
    assert t("Arrêté 2026-01539 du 06 novembre 2026 portant mesures de police le samedi 7 novembre 2026") == (date(2026, 11, 7), date(2026, 11, 7))
    assert t("Arrêté n°2026-01800 du 30 décembre 2026 portant mesures de police le 1er janvier") == (date(2027, 1, 1), date(2027, 1, 1))
    # No day in the title: the signing day and the next.
    assert t("Arrêté n°2026-01801 du 3 décembre 2026 portant interdiction de manifestation") == (date(2026, 12, 3), date(2026, 12, 4))


def test_bulletin_becomes_placed_orders():
    orders = pp.read_bulletin({"url": "https://x/raa.pdf", "date": "2026-12-04"}, PDF, MATCHERS)
    assert [o["kind"] for o in orders] == ["ban", "measures", "zone"]
    ban, measures, zone = orders
    assert {"Place de la Concorde", "Champs-Élysées"} <= {p["n"] for p in ban["places"]}
    assert {"Place de la République", "Place de la Bastille"} <= {p["n"] for p in measures["places"]}
    assert [p["n"] for p in zone["places"]] == ["Gare de Lyon"]
    assert ban["url"] == "https://x/raa.pdf#page=5"
    assert ban["text"].startswith("Article 1er - La manifestation déclarée")


def test_events_cover_whole_days_and_drop_finished_orders():
    orders = pp.read_bulletin({"url": "https://x/raa.pdf", "date": "2026-12-04"}, PDF, MATCHERS)
    evs = pp.to_events(orders, datetime(2026, 12, 6, 10, tzinfo=timezone.utc))
    assert [e["tag"] for e in evs] == ["measures", "zone"]          # Saturday's ban is over
    m = evs[0]
    assert m["cat"] == "order" and m["status"] == "active" and m["lang"] == "fr"
    assert m["start"] == "2026-12-04T23:00:00+00:00" and m["end"] == "2026-12-07T22:59:00+00:00"
    assert m["title"].startswith("Police measures — mesures de police applicables à Paris")
    assert evs[1]["status"] == "upcoming"


def fake_get(calls):
    def get(url, timeout=None, headers=None):
        calls.append(url)
        r = mock.Mock(status_code=200)
        r.raise_for_status.return_value = None
        if url.endswith("/Decembre/"):
            r.text = LISTING
        elif url.endswith("/Novembre/"):
            r.text = "<p>Rien</p>"
        else:
            r.content = PDF
        return r
    return get


def test_fetch_reads_each_bulletin_once(tmp_path):
    cache = tmp_path / "prefecture.json"
    now = datetime(2026, 12, 6, 10, tzinfo=timezone.utc)
    calls = []
    evs, note = pp.fetch(now, get=fake_get(calls), matchers=MATCHERS, cache_path=cache)
    assert len(evs) == 2 and "1 bulletins read" in note
    assert sum(".pdf" in c for c in calls) == 1                     # the November one is too old to read
    calls.clear()
    evs2, _ = pp.fetch(now.replace(minute=10), get=fake_get(calls), matchers=MATCHERS, cache_path=cache)
    assert calls == [] and len(evs2) == 2                           # listing checked recently, PDF cached


def test_fetch_keeps_last_orders_when_the_site_is_down(tmp_path):
    cache = tmp_path / "prefecture.json"
    now = datetime(2026, 12, 6, 10, tzinfo=timezone.utc)
    pp.fetch(now, get=fake_get([]), matchers=MATCHERS, cache_path=cache)
    def down(url, **kw):
        raise ConnectionError("down")
    evs, note = pp.fetch(now.replace(hour=12), get=down, matchers=MATCHERS, cache_path=cache)
    assert len(evs) == 2 and "ConnectionError" in note
