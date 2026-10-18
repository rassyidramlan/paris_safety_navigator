import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(page_title="Paris Safety Navigator", page_icon="🛡️", layout="wide")

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "sources.json"
CACHE_TTL = 300

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

def get_field(record, candidates):
    for key in candidates:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None

def fetch_paris_open_data(config):
    source = config.get("paris_open_data", {})
    if not source.get("enabled"):
        return [], "Disabled in sources.json"
    try:
        response = requests.get(source["url"], timeout=15, headers={"User-Agent": "PersonalParisSafetyNavigator/0.1"})
        response.raise_for_status()
        payload = response.json()
        records = payload.get("results", [])
        events = []
        for record in records:
            title = get_field(record, ["titre", "title", "type", "objet", "description"]) or "Paris traffic event"
            description = get_field(record, ["description", "libelle", "titre", "title"]) or ""
            date_value = get_field(record, ["date_debut", "date_debut_evenement", "start_date", "date", "updated_at"])
            lat = get_field(record, ["latitude", "lat"])
            lon = get_field(record, ["longitude", "lon", "lng"])
            # Some Open Data datasets use a geo_point_2d object.
            geo = record.get("geo_point_2d") or record.get("coordonnees_geo")
            if isinstance(geo, dict):
                lat = lat or geo.get("lat")
                lon = lon or geo.get("lon")
            elif isinstance(geo, (list, tuple)) and len(geo) >= 2:
                lat = lat or geo[0]
                lon = lon or geo[1]
            events.append({
                "title": str(title)[:240],
                "description": str(description)[:500],
                "source": source.get("name", "Paris Open Data"),
                "url": source["url"],
                "published": parse_datetime(date_value),
                "lat": safe_float(lat),
                "lon": safe_float(lon),
                "kind": "official_traffic",
            })
        return events, None
    except Exception as exc:
        return [], f"Paris Open Data fetch failed: {exc}"

def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_events():
    config = load_config()
    events, error = fetch_paris_open_data(config)
    errors = [error] if error else []
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
                        "lat": None,
                        "lon": None,
                        "kind": "news",
                    })
            except Exception as exc:
                errors.append(f"RSS error ({feed.get('name', 'feed')}): {exc}")
    except ImportError:
        errors.append("feedparser is not installed")
    return events, errors, now_utc().isoformat()

def classify(event, config):
    text = (event.get("title", "") + " " + event.get("description", "")).lower()
    watch_terms = [x.lower() for x in config.get("watch_terms", [])]
    places = [x.lower() for x in config.get("watch_places", [])]
    matched_terms = [x for x in watch_terms if x in text]
    matched_places = [x for x in places if x.lower() in text]
    if event.get("kind") == "official_traffic" and (matched_terms or matched_places):
        label = "Relevant official traffic event — verify details"
        level = "Review"
    elif matched_terms and matched_places:
        label = "Potential protest / disruption mention — unverified"
        level = "Watch"
    elif matched_terms:
        label = "Possible relevant report — location needs checking"
        level = "Watch"
    else:
        label = "General event — not classified as protest-related"
        level = "Info"
    published = event.get("published")
    age_hours = None
    if published:
        age_hours = max(0, (now_utc() - published).total_seconds() / 3600)
    if age_hours is None:
        freshness = "Timestamp unavailable"
    elif age_hours <= 3:
        freshness = f"Fresh · {age_hours:.1f}h old"
    elif age_hours <= 24:
        freshness = f"Recent · {age_hours:.0f}h old"
    else:
        freshness = f"Older · {age_hours / 24:.1f} days old"
    return label, level, freshness, matched_terms, matched_places

def display_time(dt):
    if not dt:
        return "Time not provided by source"
    try:
        return dt.astimezone().strftime("%d %b %Y, %H:%M %Z")
    except Exception:
        return str(dt)

st.title("🛡️ Paris Protest & Safety Navigator")
st.caption("Personal travel dashboard · Sources and timestamps matter · Not an emergency service")

st.warning(
    "Safety limitation: this is a prototype. It does not establish that any area is safe, "
    "and it cannot reliably predict protest size or violence. Verify alerts with official sources."
)

with st.sidebar:
    st.header("Trip settings")
    trip_date = st.date_input("Travel date (optional)")
    hotel_area = st.text_input("Hotel area / nearest Metro (optional)", placeholder="e.g. Opéra")
    destination = st.text_input("Destination to check (optional)", placeholder="e.g. Louvre")
    st.caption("Locations entered here are not sent to a server by this prototype.")
    st.divider()
    st.markdown("**Data refresh**")
    if st.button("Refresh data now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Automatic dashboard cache: about 5 minutes. Source update frequency may differ.")

config = load_config()
with st.spinner("Checking configured sources…"):
    events, errors, checked_at = get_events()

for event in events:
    label, level, freshness, terms, places = classify(event, config)
    event["classification"] = label
    event["level"] = level
    event["freshness"] = freshness
    event["matched_places"] = places

relevant = [e for e in events if e["level"] in ("Review", "Watch")]
fresh_relevant = [e for e in relevant if e["freshness"].startswith("Fresh") or e["freshness"].startswith("Recent")]

col1, col2, col3 = st.columns(3)
col1.metric("Configured source items", len(events))
col2.metric("Potentially relevant items", len(relevant))
col3.metric("Recent relevant items", len(fresh_relevant))
st.caption(f"Last checked by this dashboard: {display_time(parse_datetime(checked_at))}. This is not the same as each source's publication time.")

if errors:
    with st.expander(f"Source warnings ({len(errors)})", expanded=True):
        for error in errors:
            st.write(f"• {error}")

st.subheader("Map")
m = folium.Map(location=[48.8566, 2.3522], zoom_start=12, tiles="OpenStreetMap")
folium.Marker([48.8566, 2.3522], tooltip="Central Paris reference point",
              popup="Reference point only — not a safety assessment").add_to(m)
mapped = 0
for event in events:
    lat, lon = event.get("lat"), event.get("lon")
    if lat is None or lon is None or not (48.0 <= lat <= 49.5 and 1.0 <= lon <= 4.0):
        continue
    color = "orange" if event.get("level") in ("Review", "Watch") else "blue"
    popup_text = f"<b>{event['title']}</b><br>{event['classification']}<br>{event['freshness']}<br>Source: {event['source']}"
    folium.CircleMarker([lat, lon], radius=7, color=color, fill=True, fill_opacity=.75,
                        tooltip=event["title"][:100], popup=folium.Popup(popup_text, max_width=320)).add_to(m)
    mapped += 1
st_folium(m, use_container_width=True, height=430, returned_objects=[])

st.subheader("Reports and disruptions")
if not events:
    st.info("No records were returned by the configured sources. This does not mean there are no protests or disruptions.")
else:
    show_relevant_only = st.checkbox("Show only items flagged for review", value=True)
    displayed = relevant if show_relevant_only else events
    if not displayed:
        st.info("No items matched the current keyword rules. This does not mean the area is safe.")
    for event in sorted(displayed, key=lambda e: e.get("published") or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        with st.container(border=True):
            st.markdown(f"**{event['title']}**")
            st.caption(f"{event['source']} · {display_time(event.get('published'))} · {event['freshness']}")
            st.write(event["classification"])
            if event.get("description"):
                st.write(event["description"])
            if event.get("matched_places"):
                st.write("Place terms found: " + ", ".join(event["matched_places"]))
            if event.get("url", "").startswith(("https://", "http://")):
                st.link_button("Open source", event["url"])
            st.caption("Classification is a keyword-based hint, not confirmation of a protest or danger.")

st.subheader("Before leaving")
st.markdown("""
- Check official police / prefecture notices and current transport operator alerts.
- Avoid demonstrations and move away if a crowd becomes confrontational.
- Check the report timestamp; old reports may no longer describe current conditions.
- Keep an alternative route and do not interpret an empty map as a safety guarantee.
""")
st.caption("Prototype MVP · Paris Open Data and optional RSS only. Live protest detection and route avoidance are not yet fully implemented.")
