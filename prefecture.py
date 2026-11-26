"""Préfecture de Police orders: protest bans, security zones and police measures in Paris.

The Préfecture publishes its orders ("arrêtés") in the Paris "recueils des actes administratifs
spéciaux": PDF bulletins listed month by month on prefectures-regions.gouv.fr. Each bulletin opens
with a contents list ("Sommaire") of the orders inside:

    Préfecture de Police / Cabinet
    75-2026-10-29-00028 - Arrêté n°2026-01478 du 29 octobre 2026 portant mesures de police
    applicables à Paris du 29 octobre au 02 novembre 2026 (9 pages) Page 60

This module:
  1. reads the listing pages for this month and last month (at most every LISTING_EVERY_MIN),
  2. downloads bulletins it hasn't seen in the last KEEP_DAYS, and reads their contents list,
  3. keeps orders about demonstrations, security zones and police measures,
  4. works out the days each order covers from its title, and the places it names from its text,
     using the same place list as the news reader (data/paris_places.json).

Exact perimeters are only drawn on maps attached to the PDFs, so they aren't reproduced: an order
is placed at the streets and stations its text names, or treated as Paris-wide when it names none.

What's been read is kept in .cache/prefecture.json (restored between GitHub Actions runs), so each
bulletin is downloaded once.
"""
import html
import io
import json
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import news

ROOT = Path(__file__).parent
CACHE_PATH = ROOT / ".cache" / "prefecture.json"
PARIS_TZ = ZoneInfo("Europe/Paris")
SITE = "https://www.prefectures-regions.gouv.fr"
LISTING = SITE + "/ile-de-france/Documents-publications/Recueil-des-actes-administratifs/Raa-du-departement-de-Paris-{year}/{month}/"
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
MONTH_PAGES = ["Janvier", "Fevrier", "Mars", "Avril", "Mai", "Juin", "Juillet", "Aout", "Septembre", "Octobre", "Novembre", "Decembre"]
HEADERS = {"User-Agent": "ParisPulse/1.0 (personal travel-safety app; github.com/rassyidramlan/paris_safety_navigator)"}
TIMEOUT = 30
KEEP_DAYS = 21              # bulletins older than this are forgotten
READ_DAYS = 10              # only bulletins published in the last N days are downloaded
MAX_NEW_PER_RUN = 6         # spread a backlog over several runs
LISTING_EVERY_MIN = 30
MAX_PLACES = 8
HORIZON_DAYS = 7

REF = r"75-\d{4}-\d{2}-\d{2}-\d{5}"
ENTRY_RE = re.compile(rf"({REF})\s*-\s*(.+?)\(\s*(\d+)\s*pages?\s*\)\s*Page\s*(\d+)", re.S | re.I)
DATE_IN_LINK = re.compile(r"du\s+(\d{2})\.(\d{2})\.(\d{4})")
SIGNED_RE = re.compile(r"^\s*Arrêté\s+(?:n\s*°\s*)?[\w\s-]*?\d{4}-\d{3,5}\s+du\s+(\d{1,2})(?:er)?\s+(\w+)\s+(\d{4})\s*", re.I)
DAY_RE = re.compile(r"(?<!\d)(\d{1,2})(?:er)?\s+(" + "|".join(MONTHS + ["fevrier", "aout", "decembre"]) + r")(?:\s+(\d{4}))?", re.I)

PAIR_RE = re.compile(r"(?<!\d)(\d{1,2})(?:er)?\s+(?:au|et)\s+(?:[a-zéû]+\s+)?(\d{1,2})(?:er)?\s+(?:" + "|".join(MONTHS) + r")", re.I)

# What makes an order worth showing, and how to label it. First match wins.
KINDS = [
    ("ban", re.compile(r"interdi\w*[^.]{0,80}(manifestation|rassemblement|cortège|défilé)", re.I)),
    ("measures", re.compile(r"mesures de police applicables[^.]{0,120}(manifest|appels? à)", re.I)),
    ("zone", re.compile(r"périmètre de protection", re.I)),
    ("measures", re.compile(r"mesures de police applicables", re.I)),
    ("weapons", re.compile(r"interdiction[^.]{0,40}(port|transport)[^.]{0,60}(armes|équipements|objets)", re.I)),
    ("drones", re.compile(r"aéronefs[^.]{0,80}manifestation", re.I)),
]
SKIP = re.compile(r"délégation|repos dominical|tournage|stationnement|jury", re.I)
LABELS = {"ban": "Protest ban", "measures": "Police measures", "zone": "Security zone",
          "weapons": "Weapons and protective-gear ban", "drones": "Police drones at protests"}


# ---------- listing ----------
def listing_urls(now):
    local = now.astimezone(PARIS_TZ)
    prev = (local.replace(day=1) - timedelta(days=1))
    return [LISTING.format(year=d.year, month=MONTH_PAGES[d.month - 1]) for d in (local, prev)]


def parse_listing(page_html):
    """Special bulletins on a listing page: [{"url", "name", "date"}] (date = publication day)."""
    out, seen = [], set()
    for href, inner in re.findall(r'<a[^>]+href="([^"]*telechargement[^"]*)"[^>]*>(.*?)</a>', page_html, re.S | re.I):
        name = html.unescape(re.sub(r"<[^>]+>|\s+", " ", inner)).strip()
        url = html.unescape(href)
        url = url if url.startswith("http") else SITE + url
        key = (url + " " + name).lower()
        if "75-" not in key or "special" not in key.replace("é", "e"):
            continue
        m = DATE_IN_LINK.search(name) or DATE_IN_LINK.search(url.replace("%20", " "))
        if not m or url in seen:
            continue
        seen.add(url)
        d, mo, y = map(int, m.groups())
        out.append({"url": url, "name": name, "date": f"{y:04d}-{mo:02d}-{d:02d}"})
    return out


# ---------- reading a bulletin ----------
def pdf_pages(data):
    from pypdf import PdfReader      # imported here so the rest of the collector works without it
    reader = PdfReader(io.BytesIO(data))
    return [(p.extract_text() or "") for p in reader.pages]


def contents(pages):
    """Entries of the contents list: [{"ref", "title", "pages", "page"}]."""
    head = []
    for text in pages[:8]:                 # the contents list fills the first few pages
        head.append(text)
        if re.search(r"^\s*ARR[ÊE]T[ÉE]\s*:?\s*$", text, re.M):
            break
    text = re.sub(r"[ \t]+", " ", "\n".join(head))
    out = []
    for m in ENTRY_RE.finditer(text):
        title = re.sub(r"\s+", " ", m.group(2)).strip(" -")
        out.append({"ref": m.group(1), "title": title, "pages": int(m.group(3)), "page": int(m.group(4))})
    return out


def kind_of(title):
    if SKIP.search(title) and not re.search(r"manifestation", title, re.I):
        return None
    for kind, pattern in KINDS:
        if pattern.search(title):
            return kind
    return None


def _month(name):
    n = name.lower().replace("fevrier", "février").replace("aout", "août").replace("decembre", "décembre")
    return MONTHS.index(n) + 1


def covered_days(title):
    """(first day, last day) the order applies to, from its title, as dates. None when unsure."""
    signed = SIGNED_RE.match(title)
    if signed:
        sy, sm = int(signed.group(3)), _month(signed.group(2))
        sd = datetime(sy, sm, int(signed.group(1))).date()
        rest = title[signed.end():]
    else:
        sd, sy, sm, rest = None, None, None, title
    days = []
    for d, mo, y in DAY_RE.findall(rest):
        m = _month(mo)
        year = int(y) if y else (sy if sy and m >= sm else (sy + 1 if sy else None))
        if year:
            try:
                days.append(datetime(year, m, int(d)).date())
            except ValueError:
                pass
    # "les 28 et 29 octobre", "du 5 au 7 décembre": the first number borrows the second's month.
    for first, second in PAIR_RE.findall(rest):
        anchor = next((d for d in days if d.day == int(second)), None)
        if anchor:
            try:
                f = anchor.replace(day=int(first))
                days.append(f if f <= anchor else (anchor.replace(day=1) - timedelta(days=1)).replace(day=int(first)))
            except ValueError:
                pass
    if days:
        return min(days), max(days)
    return (sd, sd + timedelta(days=1)) if sd else None


def body_text(pages, entry):
    start = max(0, entry["page"] - 1)
    return re.sub(r"\s+", " ", " ".join(pages[start:start + entry["pages"]]))


def order_text(body):
    """The operative part of an order, short enough to read on a phone."""
    m = re.search(r"\bARR[ÊE]T[ÉE]\s*:?", body)
    part = body[m.end():] if m else body
    part = re.sub(r"Préfecture de Police - " + REF + r" - [^.]{0,250}?\s\d{1,3}(?=\s)", " ", part)   # page footers
    return part.strip()[:900]


def read_bulletin(item, data, matchers):
    pages = pdf_pages(data)
    orders = []
    for e in contents(pages):
        kind = kind_of(e["title"])
        if not kind:
            continue
        body = body_text(pages, e)
        days = covered_days(e["title"])
        places = news.find_places(e["title"] + " " + body, matchers)[:MAX_PLACES]
        orders.append({
            "ref": e["ref"], "kind": kind, "title": e["title"],
            "first": days[0].isoformat() if days else None, "last": days[1].isoformat() if days else None,
            "places": [{"n": p["name"], "lat": p["lat"], "lon": p["lon"]} for p in places],
            "text": order_text(body), "url": f"{item['url']}#page={e['page']}", "published": item["date"],
        })
    return orders


# ---------- events ----------
def short_title(title):
    """Drop the "Arrêté n°… du … portant" preamble: what's left says what the order does."""
    m = re.search(r"\b(portant|instituant|autorisant|modifiant|renouvelant|prolongeant)\b\s*", title, re.I)
    return (title[m.end():] if m and m.start() < 90 else title).strip()

def to_events(orders, now):
    out, seen = [], set()
    horizon = now + timedelta(days=HORIZON_DAYS)
    for o in orders:
        if o["ref"] in seen or not o["first"]:
            continue
        seen.add(o["ref"])
        first = datetime.fromisoformat(o["first"]).date()
        last = datetime.fromisoformat(o["last"]).date()
        start = datetime.combine(first, time(0, 0), PARIS_TZ).astimezone(timezone.utc)
        end = datetime.combine(last, time(23, 59), PARIS_TZ).astimezone(timezone.utc)
        if end < now or start > horizon:
            continue
        stops = o["places"]
        out.append({
            "id": f"pp:{o['ref']}", "kind": "order", "cat": "order", "tag": o["kind"],
            "title": f"{LABELS[o['kind']]} — {short_title(o['title'])}", "text": o["text"], "lines": [], "modes": [],
            "stops": stops, "lat": stops[0]["lat"] if stops else None, "lon": stops[0]["lon"] if stops else None, "pts": [],
            "status": "active" if start <= now else "upcoming",
            "start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
            "updated": None, "sev": None, "src": "Préfecture de Police", "url": o["url"],
            "lt": False, "why": None, "lang": "fr", "precision": "area" if stops else "city",
        })
    return out


# ---------- fetch ----------
def load_cache(path=CACHE_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {"bulletins": {}, "listing_checked": None, "known": []}


def save_cache(cache, path=CACHE_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def fetch(now, get=None, matchers=None, cache_path=CACHE_PATH):
    """Returns (events, note). Raises only when nothing at all could be read."""
    get = get or requests.get
    cache = load_cache(cache_path)
    cache.setdefault("bulletins", {}); cache.setdefault("known", [])
    note = []

    checked = cache.get("listing_checked")
    due = not checked or now - datetime.fromisoformat(checked) >= timedelta(minutes=LISTING_EVERY_MIN)
    listing_ok = not due
    if due:
        known = {}
        for url in listing_urls(now):
            try:
                r = get(url, timeout=TIMEOUT, headers=HEADERS)
                r.raise_for_status()
                for item in parse_listing(r.text):
                    known[item["url"]] = item
                listing_ok = True
            except Exception as exc:
                note.append(f"listing {url.rsplit('/', 2)[-2]}: {type(exc).__name__}")
        if listing_ok:
            cache["known"] = list(known.values())
            cache["listing_checked"] = now.isoformat()

    today = now.astimezone(PARIS_TZ).date()
    recent = [i for i in cache["known"] if (today - datetime.fromisoformat(i["date"]).date()).days <= READ_DAYS]
    todo = [i for i in sorted(recent, key=lambda i: i["date"], reverse=True) if i["url"] not in cache["bulletins"]][:MAX_NEW_PER_RUN]
    if todo:
        if matchers is None:
            matchers = news.load_places()
        for item in todo:
            try:
                r = get(item["url"], timeout=TIMEOUT, headers=HEADERS)
                r.raise_for_status()
                cache["bulletins"][item["url"]] = {"date": item["date"], "orders": read_bulletin(item, r.content, matchers)}
            except Exception as exc:
                note.append(f"{item['name'][:40]}: {type(exc).__name__}")

    for url, b in list(cache["bulletins"].items()):           # forget old bulletins
        if (today - datetime.fromisoformat(b["date"]).date()).days > KEEP_DAYS:
            del cache["bulletins"][url]
    save_cache(cache, cache_path)

    if not listing_ok and not cache["bulletins"]:
        raise RuntimeError("; ".join(note) or "listing unavailable")
    orders = [o for b in sorted(cache["bulletins"].values(), key=lambda b: b["date"], reverse=True) for o in b["orders"]]
    events = to_events(orders, now)
    summary = f"{len(cache['bulletins'])} bulletins read, {len(orders)} police orders, {len(events)} current"
    return events, "; ".join([summary] + note)
