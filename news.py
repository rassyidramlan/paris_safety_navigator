"""News reader: turns French news feeds into "News · unconfirmed" map items.

For each feed in sources.json -> news_feeds, it:
  1. downloads the feed (RSS) and reads each headline;
  2. keeps only stories from the last 24 hours that use protest words
     (manifestation, rassemblement, grève, heurts...);
  3. finds the Paris places each story names, using data/paris_places.json;
  4. merges the same story reported by several outlets into one item.

Only the headline, a short summary and the link are kept. News never counts as
confirmed: the app shows it as "News · unconfirmed" and it can turn a place amber, never red.
"""
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

ROOT = Path(__file__).parent
PLACES_PATH = ROOT / "data" / "paris_places.json"
MAX_AGE_HOURS = 24          # older stories are dropped
MAX_PLACES = 3              # pins per story
MAX_CITYWIDE = 12           # Paris-wide stories (no specific place) kept per run
SAME_SPOT_M = 400           # two names closer than this are one place (square + its station)
SUMMARY_CHARS = 240
TIMEOUT_SECONDS = 15
HEADERS = {"User-Agent": "ParisPulse/1.0 (personal travel app)"}

# --- 1. Protest words -------------------------------------------------------------
# Each group gives the story a label. Words are written in lowercase; a capitalised
# form only counts at the start of a headline or sentence, so a stop called
# "Les Grèves" or a street named "Rassemblement" doesn't count.
PROTEST_WORDS = [
    ("unrest", ["heurts", "échauffourées", "affrontements?", "gaz lacrymogènes?", "lacrymogènes?", "black blocs?",
                "émeutes?", "casseurs", "dégradations", "interpellations?", "canons? à eau"]),
    ("demonstration", ["manifestations?", "manifestants?", "manifest(?:ent|er|é|ait|aient)", "manifs?",
                       "rassemblements?", "rassemblé(?:e|s|es)?", "cortèges?", "défilés?",
                       "marches? (?:pour|contre|blanche)", "sit-in", "mobilisations?"]),
    ("strike", ["grèves?", "grévistes", "préavis de grève", "mouvement social", "journée d'action"]),
    ("blockade", ["blocages?", "blocus", "bloqués? par des manifestants", "occupation"]),
]
SENTENCE_START = r"(?:^|(?<=[.:!?] )|(?<=[«“\"] ))"


def _protest_pattern(words):
    lower = "|".join(words)
    upper = "|".join(w[0].upper() + w[1:] for w in words)
    return re.compile(rf"(?:(?<![\w-])(?:{lower})|{SENTENCE_START}(?:{upper}))(?![\w-])")


PROTEST_RE = [(kind, _protest_pattern(words)) for kind, words in PROTEST_WORDS]

# --- 2. Place names -----------------------------------------------------------------
# A "strict" name (République, Nation, Élysée...) only counts right after a place word,
# e.g. "place de la République", "à Nation", "devant l'Assemblée".
CUE = (r"(?:place(?: de la| du| de l'| des)?|métro|metro|station|gare|devant|abords? de(?: la| l'| du)?|"
       r"parvis(?: de la| de l'| du)?|quartier(?: de la| du| de)?|près de(?: la| l'| du)?|vers(?: la| l'| le)?|"
       r"depuis(?: la| l'| le)?|jusqu'à(?: la| l'| le)?|entre|parcours|itinéraire|trajet|à)\s+")
# Routes are written "République-Nation" or "Bastille-Concorde": a strict name joined
# by a hyphen to another capitalised name also counts.
ROUTE_BEFORE = r"(?=-[A-ZÀ-Ý])"
ROUTE_AFTER = r"(?<=[a-zà-ÿ]-)"
PARIS_RE = re.compile(r"\b(?:Paris|parisien(?:ne)?s?|francilien(?:ne)?s?|Île-de-France|Ile-de-France)\b")


def load_places(path=PLACES_PATH):
    """Read paris_places.json and build one search pattern per name."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    matchers = []
    for p in data["places"]:
        loose = ([] if p["strict"] else [p["name"]]) + p.get("aliases", [])
        strict = ([p["name"]] if p["strict"] else []) + p.get("strict_aliases", [])
        for name in loose:
            matchers.append((re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE), p))
        for name in strict:
            n = re.escape(name)
            matchers.append((re.compile(rf"(?:{CUE}{n}(?!\w)|(?<!\w){n}{ROUTE_BEFORE}|{ROUTE_AFTER}{n}(?!\w))"), p))
    return matchers


def find_places(text, matchers):
    """Places a story names, most specific first: landmarks and stations before districts."""
    found = {}
    for pattern, place in matchers:
        if place["name"] not in found and pattern.search(text):
            found[place["name"]] = place
    places = list(found.values())
    precise = [p for p in places if p["kind"] != "district"]
    chosen = []
    for p in precise or places:            # landmarks come first in the file, so they win
        if all(distance(p, c) > SAME_SPOT_M for c in chosen):
            chosen.append(p)
    return chosen[:MAX_PLACES]


def distance(a, b):
    """Metres between two places (good enough at city scale)."""
    from math import cos, radians, sqrt
    dx = radians(b["lon"] - a["lon"]) * cos(radians((a["lat"] + b["lat"]) / 2))
    dy = radians(b["lat"] - a["lat"])
    return 6371000 * sqrt(dx * dx + dy * dy)


def protest_kind(text):
    """(label, matched word) for the first protest word found, or (None, None)."""
    for kind, pattern in PROTEST_RE:
        m = pattern.search(text)
        if m:
            return kind, m.group(0)
    return None, None


# --- 3. Reading one feed ----------------------------------------------------------
def clean(text):
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


URL_DATE = re.compile(r"-(\d{2})-(\d{2})-(20\d{2})-")


def published_at(entry):
    """Publication time in UTC. Le Parisien gives none, but its links contain the date
    (…-04-11-2026-…): that becomes midday Paris time that day, marked as date-only."""
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    if stamp:
        return datetime(*stamp[:6], tzinfo=timezone.utc), False
    m = URL_DATE.search(entry.get("link", ""))
    if m:
        day, month, year = (int(x) for x in m.groups())
        try:
            return datetime(year, month, day, 11, 0, tzinfo=timezone.utc), True   # ~12:00 Paris
        except ValueError:
            pass
    return None, False


def read_entries(feed, content):
    """Turn raw feed XML into simple dicts: title, summary, link, published, outlet."""
    parsed = feedparser.parse(content)
    out = []
    for e in parsed.entries:
        title = clean(e.get("title"))
        outlet = clean((e.get("source") or {}).get("title")) or feed["name"]
        # Google News adds " - Outlet" to every headline; keep the outlet separately.
        if outlet and title.endswith(f" - {outlet}"):
            title = title[: -len(outlet) - 3]
        when, date_only = published_at(e)
        out.append({"title": title, "summary": clean(e.get("summary"))[:SUMMARY_CHARS * 2],
                    "link": e.get("link", ""), "published": when, "date_only": date_only, "outlet": outlet})
    return out


# --- 4. Merging the same story from several outlets ---------------------------------
STOP = set("le la les un une des du de d l à au aux et en sur pour par avec dans ce cette ces qui que est sont a ont "
           "après avant contre plus paris".split())


def words(text):
    return {w for w in re.findall(r"[a-zà-ÿ0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def same_story(a, b):
    """Same story: most headline words shared, and (if both are placed) a shared place."""
    wa, wb = words(a["title"]), words(b["title"])
    if not (wa and wb):
        return False
    overlap = len(wa & wb) / min(len(wa), len(wb))
    if a["places"] and b["places"]:
        return overlap >= 0.5 and bool({p["name"] for p in a["places"]} & {p["name"] for p in b["places"]})
    return overlap >= 0.6


# --- 5. Putting it together ----------------------------------------------------------
def to_event(item):
    places = item["places"]
    lat = round(sum(p["lat"] for p in places) / len(places), 5) if places else None
    lon = round(sum(p["lon"] for p in places) / len(places), 5) if places else None
    summary = item["summary"]
    if summary.lower().startswith(item["title"].lower()[:40]):
        summary = ""                       # some feeds repeat the headline as the summary
    return {
        "id": "news:" + hashlib.sha1((item["link"] or item["title"]).encode("utf-8")).hexdigest()[:16],
        "kind": "news", "cat": "news",
        "title": item["title"], "text": summary[:SUMMARY_CHARS],
        "lines": [], "modes": [],
        "stops": [{"n": p["name"], "lat": p["lat"], "lon": p["lon"]} for p in places],
        "lat": lat, "lon": lon, "pts": [],
        "status": "active", "start": item["published"].isoformat(timespec="seconds"), "end": None,
        "updated": item["published"].isoformat(timespec="seconds"),
        "sev": None, "src": " · ".join(item["outlets"]), "url": item["link"], "lt": False,
        "why": item["word"], "tag": item["kind"],
        "precision": "city" if not places else "area" if all(p["kind"] == "district" for p in places) else "place",
        "reports": len(item["outlets"]), "lang": "fr", "date_only": item.get("date_only", False),
    }


def build(entries, matchers, now):
    """From raw entries (all feeds) to merged news events."""
    kept = []
    for e in entries:
        # Date-only stories get a wider window: "today or yesterday".
        oldest = now - timedelta(hours=MAX_AGE_HOURS + (24 if e.get("date_only") else 0))
        if not e["published"] or not (oldest <= e["published"] <= now + timedelta(hours=12 if e.get("date_only") else 1)):
            continue
        text = f'{e["title"]} {e["summary"]}'
        kind, word = protest_kind(text)
        if not kind:
            continue
        places = find_places(text, matchers)
        if not places and not PARIS_RE.search(e["title"]):
            continue                       # no Paris place, and the headline isn't about Paris: skip
        item = {**e, "kind": kind, "word": word, "places": places, "outlets": [e["outlet"]]}
        twin = next((k for k in kept if same_story(k, item)), None)
        if twin:
            if item["outlet"] not in twin["outlets"]:
                twin["outlets"].append(item["outlet"])
            if not item.get("date_only") and (twin.get("date_only") or item["published"] < twin["published"]):
                twin["published"], twin["date_only"] = item["published"], False
            continue
        kept.append(item)
    kept.sort(key=lambda i: i["published"], reverse=True)
    placed = [i for i in kept if i["places"]]
    citywide = [i for i in kept if not i["places"]][:MAX_CITYWIDE]
    return [to_event(i) for i in placed + citywide]


def fetch(feeds, now, matchers=None, get=None):
    """Read every enabled feed. Returns (events, per-feed status list). Never raises."""
    get = get or requests.get
    matchers = matchers or load_places()
    entries, status = [], []
    for feed in feeds:
        if not feed.get("enabled") or not feed.get("url"):
            continue
        try:
            r = get(feed["url"], timeout=TIMEOUT_SECONDS, headers=HEADERS)
            r.raise_for_status()
            got = read_entries(feed, r.content)
            entries += got
            status.append({"id": feed["id"], "ok": True, "items": len(got)})
        except Exception as exc:
            status.append({"id": feed["id"], "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    return build(entries, matchers, now), status
