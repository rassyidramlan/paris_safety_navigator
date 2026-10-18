"""Scheduled monitor: fetch configured feeds and send Telegram alerts for new matching items.

This is intentionally a conservative MVP. It does not claim to confirm incidents or forecast violence.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import requests
import feedparser

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
STATE_PATH = ROOT / "data" / "sent_hashes.json"
STATE_PATH.parent.mkdir(exist_ok=True)

def text_of(item):
    return f"{item.get('title', '')} {item.get('description', '')}".lower()

def is_relevant(item):
    text = text_of(item)
    terms = [t.lower() for t in CONFIG.get("watch_terms", [])]
    places = [p.lower() for p in CONFIG.get("watch_places", [])]
    term_match = any(t in text for t in terms)
    place_match = any(p in text for p in places)
    return term_match and (place_match or item.get("kind") == "official_traffic")

def fetch_items():
    items = []
    source = CONFIG.get("paris_open_data", {})
    if source.get("enabled"):
        try:
            r = requests.get(source["url"], timeout=20, headers={"User-Agent": "PersonalParisSafetyNavigator/0.1"})
            r.raise_for_status()
            for record in r.json().get("results", []):
                title = next((record.get(k) for k in ["titre", "title", "type", "objet", "description"] if record.get(k)), "Paris traffic event")
                desc = next((record.get(k) for k in ["description", "libelle"] if record.get(k)), "")
                date = next((record.get(k) for k in ["date_debut", "date_debut_evenement", "start_date", "date", "updated_at"] if record.get(k)), "")
                items.append({
                    "title": str(title), "description": str(desc), "date": str(date),
                    "source": source.get("name", "Paris Open Data"), "url": source["url"],
                    "kind": "official_traffic"
                })
        except Exception as exc:
            print(f"WARNING: Paris Open Data fetch failed: {exc}")
    for feed in CONFIG.get("rss_feeds", []):
        if not feed.get("enabled") or not feed.get("url"):
            continue
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries[:50]:
                items.append({
                    "title": entry.get("title", "Untitled"),
                    "description": re.sub("<[^>]+>", " ", entry.get("summary", ""))[:700],
                    "date": entry.get("published", entry.get("updated", "")),
                    "source": feed.get("name", "RSS feed"),
                    "url": entry.get("link", feed["url"]), "kind": "news"
                })
        except Exception as exc:
            print(f"WARNING: RSS fetch failed for {feed.get('name')}: {exc}")
    return items

def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram credentials missing; printing alerts instead.")
        print(text)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=20)
    response.raise_for_status()

def load_state():
    try:
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()

def save_state(values):
    # Keep recent state bounded.
    STATE_PATH.write_text(json.dumps(list(values)[-1000:], indent=2), encoding="utf-8")

def main():
    seen = load_state()
    items = fetch_items()
    new_hashes = set(seen)
    alerts_sent = 0
    for item in items:
        if not is_relevant(item):
            continue
        stable = "|".join([item.get("title", ""), item.get("date", ""), item.get("url", "")])
        digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        # Keep message conservative; no automatic claim that an incident is confirmed.
        message = (
            "🛡️ Paris Safety Navigator — new item to review\n\n"
            f"{item.get('title', 'Untitled')}\n"
            f"Source: {item.get('source', 'Unknown source')}\n"
            f"Source time: {item.get('date') or 'Not provided'}\n"
            f"Classification: keyword match; verify before acting\n"
            f"Link: {item.get('url', '')}\n\n"
            "This is not confirmation of danger. Check official notices and current transport status."
        )
        try:
            send_telegram(message)
            new_hashes.add(digest)
            alerts_sent += 1
        except Exception as exc:
            print(f"Telegram send failed: {exc}")
    save_state(new_hashes)
    print(f"Checked {len(items)} items; sent {alerts_sent} new alert(s) at {datetime.now(timezone.utc).isoformat()}.")

if __name__ == "__main__":
    main()
