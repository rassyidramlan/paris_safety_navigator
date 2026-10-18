import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import paris_open_data
import prim_disruptions

st.set_page_config(page_title="Paris Safety Navigator", page_icon="🛡️", layout="wide")

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "sources.json"
CACHE_TTL = 300
# All times are stored in UTC and shown in Paris local time (CET in winter, CEST in summer).
PARIS_TZ = ZoneInfo("Europe/Paris")
STATUS_ORDER = {"active": 0, "upcoming": 1, "ended": 2, None: 3}
TRANSPORT_STALE_MINUTES = 30

st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1250px;}
.small-muted {color: #6b7280; font-size: .85rem;}
</style>
""", unsafe_allow_html=True)

def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def now_utc():
    return datetime.now(timezone.utc)

def parse_datetime(value):
    if not value:
        return None
    try:
        dt = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.to_pydatetime()
    except Exception:
        return None

def get_secret(name):
    """Read a secret from Streamlit secrets or the environment, never failing."""
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass  # no secrets file (e.g. local run or tests)
    return os.environ.get(name, "")

def fetch_paris_open_data(config):
    source = config.get("paris_open_data", {})
    if not source.get("enabled"):
        return [], ["Paris Open Data is disabled in sources.json"]
    try:
        return paris_open_data.fetch(source, now_utc())
    except Exception as exc:
        return [], [f"Paris Open Data fetch failed: {exc}"]

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_events():
    config = load_config()
    events, errors = fetch_paris_open_data(config)
    # RSS sources are configured separately; use feedparser for publicly available feeds.
    try:
        import feedparser
        for feed in config.get("rss_feeds", []):
            if not feed.get("enabled") or not feed.get("url"):
                continue
            try:
                parsed = feedparser.parse(feed["url"])
                if getattr(parsed, "bozo", False) and not parsed.entries:
                    errors.append(f"RSS feed error: {feed.get('name', feed['url'])}")
                for entry in parsed.entries[:50]:
                    published = parse_datetime(entry.get("published") or entry.get("updated"))
                    events.append({
                        "title": entry.get("title", "Untitled report")[:240],
                        "description": entry.get("summary", "")[:500],
                        "source": feed.get("name", "RSS feed"),
                        "url": entry.get("link", feed["url"]),
                        "published": published,
                        "start": None, "end": None, "status": None, "long_term": False,
                        "points": [], "lat": None, "lon": None,
                        "kind": "news",
                    })
            except Exception as exc:
                errors.append(f"RSS error ({feed.get('name', 'feed')}): {exc}")
    except ImportError:
        errors.append("feedparser is not installed")
    return events, errors, now_utc().isoformat()

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_transport(_api_key):
    """Returns (events, error_message, feed_updated). The key is only used for the request.

    The leading underscore tells Streamlit not to hash the key into the cache index.
    """
    if not _api_key:
        return [], None, None
    try:
        events, feed_updated = prim_disruptions.fetch(_api_key, now_utc())
        return events, None, feed_updated
    except Exception as exc:
        # Never include the key in messages; the exception text only has the status/URL.
        return [], f"Transport info (IDFM) fetch failed: {exc}", None

def display_time(dt):
    if not dt:
        return "Time not provided by source"
    try:
        # e.g. "Thu 03 Dec 2026, 14:30 CET (Paris time)"
        return dt.astimezone(PARIS_TZ).strftime("%a %d %b %Y, %H:%M %Z") + " (Paris time)"
    except Exception:
        return str(dt)

def display_date(dt):
    return dt.astimezone(PARIS_TZ).strftime("%a %d %b %Y") if dt else "not given"

def describe_timing(event):
    """Plain-language timing: the planned window for road/transport events, age for news."""
    if event["kind"] in ("official_traffic", "transport"):
        status, start, end = event.get("status"), event.get("start"), event.get("end")
        # Transport windows are often a few hours, so show clock times; roads show dates.
        fmt = display_time if event["kind"] == "transport" else display_date
        if status == "ended":
            return f"Ended {fmt(end)}"
        if status == "upcoming":
            return f"Starts {display_time(start)}"
        until = f"until {fmt(end)}" if end else "no end time given"
        return f"Active now · {until}" + (" · long-term restriction" if event.get("long_term") else "")
    published = event.get("published")
    if not published:
        return "Timestamp unavailable"
    age_hours = max(0, (now_utc() - published).total_seconds() / 3600)
    if age_hours <= 3:
        return f"Fresh · {age_hours:.1f}h old"
    if age_hours <= 24:
        return f"Recent · {age_hours:.0f}h old"
    return f"Older · {age_hours / 24:.1f} days old"

def classify(event, config):
    text = (event.get("title", "") + " " + event.get("description", "") + " "
            + " ".join(event.get("stops") or [])).lower()
    watch_terms = [x.lower() for x in config.get("watch_terms", [])]
    places = [x.lower() for x in config.get("watch_places", [])]
    matched_terms = [x for x in watch_terms if x in text]
    matched_places = [x for x in places if x in text]
    if event.get("kind") == "transport":
        if event.get("protest_related"):
            return "Operator cites a demonstration or strike — official transport notice", "Watch", matched_places
        if matched_places:
            return f"Official {event.get('severity', '').lower()} notice near a watched place", "Review", matched_places
        return f"Official transport notice · {event.get('severity')} · {event.get('cause')}", "Info", matched_places
    if event.get("kind") == "official_traffic" and (matched_terms or matched_places):
        return "Official road event matching your watch list — verify details", "Review", matched_places
    if matched_terms and matched_places:
        return "Potential protest / disruption mention — unverified", "Watch", matched_places
    if matched_terms:
        return "Possible relevant report — location needs checking", "Watch", matched_places
    if event.get("kind") == "official_traffic":
        return "Official roadworks / closure record — not a protest report", "Info", matched_places
    return "General report — not matched by keyword rules", "Info", matched_places

def sort_key(event):
    when = event.get("published") or event.get("start") or datetime.min.replace(tzinfo=timezone.utc)
    return (STATUS_ORDER.get(event.get("status"), 3), -when.timestamp())

def render_card(event):
    with st.container(border=True):
        st.markdown(f"**{event['title']}**")
        if event.get("lines"):
            st.markdown(" ".join(f"`{line}`" for line in event["lines"][:8]))
        st.caption(f"{event['source']} · {event['timing']}")
        st.write(event["classification"])
        if event["kind"] == "transport":
            if event.get("stops"):
                st.caption("Stops affected: " + ", ".join(event["stops"][:10]))
            if event.get("description"):
                with st.expander("Operator message"):
                    st.text(event["description"])
            if event.get("updated"):
                st.caption(f"Notice last updated: {display_time(event['updated'])}")
        elif event.get("description"):
            st.write(event["description"])
        if event.get("matched_places"):
            st.write("Place terms found: " + ", ".join(event["matched_places"]))
        if event.get("url", "").startswith(("https://", "http://")):
            st.link_button("Open source", event["url"])

st.title("🛡️ Paris Protest & Safety Navigator")
st.caption("Personal travel dashboard · Sources and timestamps matter · Not an emergency service")

st.warning(
    "Safety limitation: this is a prototype. It does not establish that any area is safe, "
    "and it cannot reliably predict protest size or violence. Verify alerts with official sources."
)

with st.sidebar:
    st.markdown("**Data refresh**")
    if st.button("Refresh data now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Automatic dashboard cache: about 5 minutes. Source update frequency may differ.")
    st.divider()
    st.markdown("**Show**")
    show_upcoming = st.checkbox("Upcoming events (next 3 days)", value=True)
    show_long_term = st.checkbox("Long-term restrictions (over a year)", value=False,
                                 help="Mostly permanent bus/taxi-only lanes. Hidden by default to reduce clutter.")
    show_flagged_only = st.checkbox("Only items matching my watch list", value=False)
    show_all_buses = st.checkbox("All bus disruptions", value=False,
                                 help="Off: buses are shown only when the operator cites a demonstration or strike.")
    st.divider()
    st.caption("Place and route checks are not available yet. This app does not use your location.")

config = load_config()
with st.spinner("Checking configured sources…"):
    events, errors, checked_at = get_events()
    prim_key = get_secret("PRIM_API_KEY")
    transport, transport_error, transport_updated = get_transport(prim_key)
if transport_error:
    errors = errors + [transport_error]

for event in events + transport:
    label, level, places = classify(event, config)
    event["classification"] = label
    event["level"] = level
    event["matched_places"] = places
    event["timing"] = describe_timing(event)

soon = now_utc() + timedelta(days=3)

def is_shown(event):
    if event["kind"] in ("official_traffic", "transport"):
        if event["status"] == "ended":
            return False
        if event["status"] == "upcoming" and not (show_upcoming and event["start"] and event["start"] <= soon):
            return False
        if event.get("long_term") and not show_long_term:
            return False
    if event["kind"] == "transport" and not event.get("is_rail"):
        if not (show_all_buses or event.get("protest_related")):
            return False
    if show_flagged_only and event["level"] not in ("Review", "Watch"):
        return False
    return True

roads_shown = [e for e in events if is_shown(e)]
transport_shown = sorted((e for e in transport if is_shown(e)), key=sort_key)
protest_notices = [e for e in transport_shown if e.get("protest_related")]
rail_now = [e for e in transport if e.get("is_rail") and e["status"] == "active"
            and e.get("severity") in ("Blocking", "Disrupted")]
roads_active = [e for e in events if e.get("status") == "active"]

col1, col2, col3 = st.columns(3)
col1.metric("Demonstration / strike notices", len(protest_notices) if prim_key else "—",
            help="Transport notices (active or within 3 days) where the operator gives a demonstration or strike as the reason.")
col2.metric("Métro / RER / tram disruptions now", len(rail_now) if prim_key else "—")
col3.metric("Road events active now", len(roads_active))
st.caption(f"Last checked by this dashboard: {display_time(parse_datetime(checked_at))}. This is not the same as each source's publication time.")

if transport_updated:
    age_min = (now_utc() - transport_updated).total_seconds() / 60
    if age_min > TRANSPORT_STALE_MINUTES:
        st.error(f"Transport feed may be stale: last updated by IDFM {age_min:.0f} minutes ago "
                 f"({display_time(transport_updated)}). Check the official IDFM / RATP apps.")

if errors:
    with st.expander(f"Source warnings ({len(errors)})", expanded=True):
        for error in errors:
            st.write(f"• {error}")

tab_transport, tab_roads = st.tabs(["🚇 Transport", "🚧 Roads & map"])

with tab_transport:
    if not prim_key:
        st.info("Live transport info is not configured (PRIM_API_KEY secret missing). "
                "Check the official IDFM or RATP apps for Métro and RER status.")
    elif transport_error:
        st.info("Transport info could not be loaded right now. This does not mean services are running normally.")
    else:
        if transport_updated:
            st.caption(f"Source: Île-de-France Mobilités (PRIM) · feed updated {display_time(transport_updated)}. "
                       "Operator notices only — not an independent safety assessment.")
        if protest_notices:
            st.subheader(f"⚠️ Demonstration / strike-related notices ({len(protest_notices)})")
            st.caption("The operator gives a demonstration or strike as the reason for these changes. "
                       "Avoid the affected area if you can and follow official instructions.")
            for event in protest_notices:
                render_card(event)
        else:
            st.caption("No transport notices currently give a demonstration or strike as the reason. "
                       "This does not mean there are none — operators may not state a reason.")
        others = [e for e in transport_shown if not e.get("protest_related")]
        st.subheader(f"Other transport disruptions ({len(others)})")
        st.caption("Métro, RER, Transilien, tram" + (" and bus" if show_all_buses else "")
                   + " · active first, then starting within 3 days. Transport notices have no map position.")
        if not others:
            st.info("No other matching transport disruptions. This does not mean services are running normally.")
        for event in others:
            render_card(event)

with tab_roads:
    st.subheader("Map")
    m = folium.Map(location=[48.8566, 2.3522], zoom_start=12, tiles="OpenStreetMap")
    mapped = 0
    for event in roads_shown:
        lat, lon = event.get("lat"), event.get("lon")
        if lat is None or lon is None:
            continue
        color = "orange" if event.get("level") in ("Review", "Watch") else "blue"
        popup_text = (f"<b>{event['title']}</b><br>{event['timing']}<br>"
                      f"{event['classification']}<br>Source: {event['source']}")
        if len(event.get("points") or []) >= 2:
            # Draw the affected stretch of road as given by the source.
            folium.PolyLine(event["points"], color=color, weight=6, opacity=.8,
                            tooltip=event["title"][:100]).add_to(m)
        # A dot at the middle of the stretch gives a larger target to tap on a phone.
        folium.CircleMarker([lat, lon], radius=7, color=color, fill=True, fill_opacity=.75,
                            tooltip=event["title"][:100], popup=folium.Popup(popup_text, max_width=320)).add_to(m)
        mapped += 1
    st_folium(m, use_container_width=True, height=430, returned_objects=[])
    st.caption(
        f"🟠 Orange: matches your watch list · 🔵 Blue: other road works / closures. "
        f"Showing {mapped} of {len(roads_shown)} listed items — only items with coordinates can be mapped; "
        "see the full list below."
    )

    st.subheader("Road works and closures")
    if not events:
        st.info("No records were returned by the configured sources. This does not mean there are no protests or disruptions.")
    elif not roads_shown:
        st.info("No items match the current filters. This does not mean the area is safe.")
    else:
        st.caption(f"{len(roads_shown)} items · active first, then upcoming. The road data covers works and closures only, not protests.")
        for event in sorted(roads_shown, key=sort_key):
            render_card(event)

st.subheader("Before leaving")
st.markdown("""
- Check official police / prefecture notices and current transport operator alerts.
- Avoid demonstrations and move away if a crowd becomes confrontational.
- Check the report timestamp; old reports may no longer describe current conditions.
- Keep an alternative route and do not interpret an empty map as a safety guarantee.
""")
st.caption("Prototype MVP · Sources: Île-de-France Mobilités (PRIM) traffic info, Paris Open Data road works/closures, "
           "optional RSS. Live protest detection and route avoidance are not implemented.")
