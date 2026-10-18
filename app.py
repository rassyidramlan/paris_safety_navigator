import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import paris_open_data

st.set_page_config(page_title="Paris Safety Navigator", page_icon="🛡️", layout="wide")

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "sources.json"
CACHE_TTL = 300
# All times are stored in UTC and shown in Paris local time (CET in winter, CEST in summer).
PARIS_TZ = ZoneInfo("Europe/Paris")
STATUS_ORDER = {"active": 0, "upcoming": 1, "ended": 2, None: 3}

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
    """Plain-language timing: the planned window for road events, age for news."""
    if event["kind"] == "official_traffic":
        status, start, end = event.get("status"), event.get("start"), event.get("end")
        if status == "ended":
            return f"Ended {display_date(end)}"
        if status == "upcoming":
            return f"Starts {display_time(start)}"
        until = f"until {display_date(end)}" if end else "no end date given"
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
    text = (event.get("title", "") + " " + event.get("description", "")).lower()
    watch_terms = [x.lower() for x in config.get("watch_terms", [])]
    places = [x.lower() for x in config.get("watch_places", [])]
    matched_terms = [x for x in watch_terms if x in text]
    matched_places = [x for x in places if x in text]
    if event.get("kind") == "official_traffic" and (matched_terms or matched_places):
        return "Official road event matching your watch list — verify details", "Review", matched_places
    if matched_terms and matched_places:
        return "Potential protest / disruption mention — unverified", "Watch", matched_places
    if matched_terms:
        return "Possible relevant report — location needs checking", "Watch", matched_places
    if event.get("kind") == "official_traffic":
        return "Official roadworks / closure record — not a protest report", "Info", matched_places
    return "General report — not matched by keyword rules", "Info", matched_places

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
    show_upcoming = st.checkbox("Upcoming road events (next 3 days)", value=True)
    show_long_term = st.checkbox("Long-term restrictions (over a year)", value=False,
                                 help="Mostly permanent bus/taxi-only lanes. Hidden by default to reduce clutter.")
    show_flagged_only = st.checkbox("Only items matching my watch list", value=False)
    st.divider()
    st.caption("Place and route checks are not available yet. This app does not use your location.")

config = load_config()
with st.spinner("Checking configured sources…"):
    events, errors, checked_at = get_events()

for event in events:
    label, level, places = classify(event, config)
    event["classification"] = label
    event["level"] = level
    event["matched_places"] = places
    event["timing"] = describe_timing(event)

soon = now_utc() + timedelta(days=3)

def is_shown(event):
    if event["kind"] == "official_traffic":
        if event["status"] == "ended":
            return False
        if event["status"] == "upcoming" and not (show_upcoming and event["start"] and event["start"] <= soon):
            return False
        if event.get("long_term") and not show_long_term:
            return False
    if show_flagged_only and event["level"] not in ("Review", "Watch"):
        return False
    return True

shown = [e for e in events if is_shown(e)]
active = [e for e in events if e.get("status") == "active"]
flagged = [e for e in events if e["level"] in ("Review", "Watch")]

col1, col2, col3 = st.columns(3)
col1.metric("Items from sources", len(events))
col2.metric("Road events active now", len(active))
col3.metric("Matching watch list", len(flagged))
st.caption(f"Last checked by this dashboard: {display_time(parse_datetime(checked_at))}. This is not the same as each source's publication time.")

if errors:
    with st.expander(f"Source warnings ({len(errors)})", expanded=True):
        for error in errors:
            st.write(f"• {error}")

st.subheader("Map")
m = folium.Map(location=[48.8566, 2.3522], zoom_start=12, tiles="OpenStreetMap")
mapped = 0
for event in shown:
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
    f"Showing {mapped} of {len(shown)} listed items — only items with coordinates can be mapped; "
    "see the full list below."
)

st.subheader("Reports and disruptions")
if not events:
    st.info("No records were returned by the configured sources. This does not mean there are no protests or disruptions.")
elif not shown:
    st.info("No items match the current filters. This does not mean the area is safe.")
else:
    st.caption(f"{len(shown)} items · active first, then upcoming. The road data covers works and closures only, not protests.")
    for event in sorted(shown, key=lambda e: (
            STATUS_ORDER.get(e.get("status"), 3),
            -(e.get("published") or e.get("start") or datetime.min.replace(tzinfo=timezone.utc)).timestamp())):
        with st.container(border=True):
            st.markdown(f"**{event['title']}**")
            st.caption(f"{event['source']} · {event['timing']}")
            st.write(event["classification"])
            if event.get("description"):
                st.write(event["description"])
            if event.get("matched_places"):
                st.write("Place terms found: " + ", ".join(event["matched_places"]))
            if event.get("url", "").startswith(("https://", "http://")):
                st.link_button("Open source", event["url"])

st.subheader("Before leaving")
st.markdown("""
- Check official police / prefecture notices and current transport operator alerts.
- Avoid demonstrations and move away if a crowd becomes confrontational.
- Check the report timestamp; old reports may no longer describe current conditions.
- Keep an alternative route and do not interpret an empty map as a safety guarantee.
""")
st.caption("Prototype MVP · Paris Open Data road works/closures and optional RSS only. Live protest detection and route avoidance are not yet implemented.")
