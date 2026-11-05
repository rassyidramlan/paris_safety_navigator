"""Tests for news.py. Headlines here are made up, in the style of real Paris news feeds."""
from datetime import datetime, timedelta, timezone
from unittest import mock

import news

NOW = datetime(2026, 12, 4, 10, 0, tzinfo=timezone.utc)
MATCHERS = news.load_places()


def entry(title, summary="", hours_ago=1, outlet="Le Parisien — Paris", link=None, date_only=False):
    return {"title": title, "summary": summary, "link": link or f"https://example.org/{abs(hash(title))}",
            "published": NOW - timedelta(hours=hours_ago), "date_only": date_only, "outlet": outlet}


def names(event):
    return [s["n"] for s in event["stops"]]


def test_protest_near_a_landmark_is_placed():
    [ev] = news.build([entry("Rassemblement place de la Bastille contre la réforme")], MATCHERS, NOW)
    assert names(ev) == ["Place de la Bastille"]
    assert ev["cat"] == "news" and ev["tag"] == "demonstration" and ev["why"] == "Rassemblement"


def test_route_written_with_a_hyphen():
    [ev] = news.build([entry("Manifestation : le parcours République-Nation confirmé")], MATCHERS, NOW)
    assert set(names(ev)) == {"Place de la République", "Place de la Nation"}


def test_strict_names_need_a_place_word():
    # "président de la République" is not the square.
    assert news.build([entry("Manifestation : le président de la République réagit", "Il parle à Lyon.")], MATCHERS, NOW) == []
    [ev] = news.build([entry("Les manifestants se dirigent vers la Nation", "Départ à République.")], MATCHERS, NOW)
    assert "Place de la République" in names(ev) or "République" in names(ev)


def test_no_protest_word_is_ignored():
    assert news.build([entry("Marché de Noël place de la Bastille : les horaires")], MATCHERS, NOW) == []


def test_capitalised_place_name_is_not_a_strike():
    # A stop called "Les Grèves" must not count as a strike.
    assert news.protest_kind("Travaux : l'arrêt Les Grèves déplacé") == (None, None)
    assert news.protest_kind("La grève des cheminots continue")[0] == "strike"


def test_unrest_words():
    kind, word = news.protest_kind("Heurts entre manifestants et forces de l'ordre")
    assert kind == "unrest" and word == "Heurts"


def test_old_stories_are_dropped():
    assert news.build([entry("Rassemblement place de la Bastille", hours_ago=30)], MATCHERS, NOW) == []


def test_date_only_stories_keep_today_and_yesterday():
    ev = entry("Grève : rassemblement place de la Nation", hours_ago=30, date_only=True)
    assert len(news.build([ev], MATCHERS, NOW)) == 1


def test_same_story_from_two_outlets_is_merged():
    a = entry("Manifestation place de la République : des milliers de personnes", outlet="Le Parisien")
    b = entry("Des milliers de personnes manifestent place de la République", outlet="France 3", hours_ago=2)
    [ev] = news.build([a, b], MATCHERS, NOW)
    assert ev["reports"] == 2 and "Le Parisien" in ev["src"] and "France 3" in ev["src"]


def test_square_and_its_station_make_one_pin():
    [ev] = news.build([entry("Rassemblement place de la République, métro République fermé")], MATCHERS, NOW)
    assert names(ev) == ["Place de la République"]


def test_paris_wide_story_needs_paris_in_the_headline():
    [ev] = news.build([entry("Grève du 5 décembre à Paris : 20 stations de métro fermées")], MATCHERS, NOW)
    assert ev["precision"] == "city" and ev["stops"] == [] and ev["lat"] is None
    assert news.build([entry("Manifestation à Rouen", "Comme à Paris la semaine dernière.")], MATCHERS, NOW) == []


def test_arrondissement_only_gives_an_area():
    [ev] = news.build([entry("Rassemblement dans le 11e arrondissement")], MATCHERS, NOW)
    assert ev["precision"] == "area" and names(ev) == ["11e arrondissement"]


def test_le_parisien_date_from_link():
    when, date_only = news.published_at({"link": "https://www.leparisien.fr/paris-75/titre-04-12-2026-ABC.php"})
    assert date_only and when.date().isoformat() == "2026-12-04"


def test_google_news_outlet_suffix_removed():
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
      <item><title>Rassemblement place de la Bastille - Le Monde</title><link>https://example.org/a</link>
      <pubDate>Fri, 04 Dec 2026 09:00:00 GMT</pubDate><source url="https://lemonde.fr">Le Monde</source></item>
    </channel></rss>"""
    [item] = news.read_entries({"name": "Google News"}, xml)
    assert item["title"] == "Rassemblement place de la Bastille" and item["outlet"] == "Le Monde"


def test_one_failing_feed_does_not_stop_the_others():
    good = mock.Mock(content=b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
      <item><title>Manifestation place de la Nation</title><link>https://example.org/b</link>
      <pubDate>Fri, 04 Dec 2026 09:30:00 GMT</pubDate></item></channel></rss>""")
    good.raise_for_status.return_value = None

    def get(url, **kw):
        if "broken" in url:
            raise TimeoutError("feed too slow")
        return good

    feeds = [{"id": "broken", "name": "Broken", "url": "https://broken.example", "enabled": True},
             {"id": "ok", "name": "OK", "url": "https://ok.example", "enabled": True}]
    events, status = news.fetch(feeds, NOW, MATCHERS, get=get)
    assert [s["ok"] for s in status] == [False, True] and len(events) == 1
