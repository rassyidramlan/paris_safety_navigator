# Paris Pulse

A personal phone app for situational awareness in Paris: where demonstrations are affecting the city right now, which Métro/RER lines are disrupted, and whether any of that is close to the places you care about.

It is a personal tool, not an emergency service. In danger, call **112**.

## How it works

```
Official feeds ──► collect.py (GitHub Actions, every ~10 min) ──► site/data.json ──► GitHub Pages
                                                                                      │
                                                    phone: Paris Pulse web app ◄──────┘
                                                    (works out red / amber / green for your places on the phone)
```

- **`collect.py`** fetches the sources, puts transport notices on the map using IDFM stop coordinates, and writes `site/data.json`. Secrets are read from environment variables and never written to the output.
- **`site/`** is the app: `index.html`, `style.css`, `app.js`, a service worker for offline use, and a manifest so it installs to the home screen.
- **Your places stay on your phone** (browser storage). They are never sent anywhere.

## Sources

| Source | What it gives | Access |
|---|---|---|
| Île-de-France Mobilités — PRIM *Traffic Info Messages* | Official Métro, RER, tram, bus notices, including when the operator gives a demonstration or strike as the reason | Free token, secret `PRIM_API_KEY`; Mobility License |
| Île-de-France Mobilités — *Référentiel des arrêts* | Stop coordinates (`data/idfm_stops.json`) | Licence Ouverte 2.0 |
| Paris Open Data — `circulation_evenement` | Road works and closures | Open data |
| Map tiles | © OpenStreetMap contributors, © CARTO | Free tier |

## Status colours

- **Red** — an operator notice cites a demonstration within 500 m of a place, now or in the next 2 hours.
- **Amber** — the same within 2 km, a demonstration within 1 km later today, or a Métro/RER line blocked within 500 m.
- **Green** — nothing like that in the feeds. Feeds can miss things.

Strikes are listed and mapped separately and don't turn places red: they affect transport, not street safety.

## Setup

1. Repo **Settings → Secrets and variables → Actions**: add `PRIM_API_KEY`.
2. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions** (needs the repo to be public on the free plan).
3. **Actions → Paris Pulse — collect and publish → Run workflow**. The app appears at `https://<user>.github.io/paris_safety_navigator/`.
4. Scheduled runs are best effort. For reliable 10-minute updates, have an external timer (e.g. cron-job.org) call the workflow's `dispatches` API with a fine-grained token limited to Actions on this repo.

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q                      # tests use saved samples, never live calls
PRIM_API_KEY=... python collect.py       # writes site/data.json
python -m http.server -d site 8000       # open http://localhost:8000
```

On Windows PowerShell: `$env:PRIM_API_KEY="..."` before `python collect.py`.

## Trip tab

The Trip tab shows the family itinerary day by day: you can tick off each stop, see a pre-trip checklist, and open any stop in Google Maps. Stops that have coordinates also appear in **Today**, where they get the same demonstration check as your own stops.

The itinerary says where the family will be, hour by hour, so it is **never committed to this public repo or published with the site**. Each phone loads it in one of two ways:

- **Open file**: pick the `itinerary.json` file. Good for a one-off load.
- **Use link**: paste a private `https://` link to the file, for example the raw URL of a secret GitHub gist. The app fetches it every time it opens and keeps the last copy for offline use. To change the plan mid-trip, edit the gist and every phone picks the change up. A banner lists what changed, for example "Day 4: Galeries Lafayette now 15:00", and a notification is sent if alerts are on.

The itinerary's `trip.hotel` can carry `lat`, `lon` and `address`. If it does, the hotel is watched automatically and fills in the taxi card. If it doesn't, the Trip tab asks you to pin the hotel.

Stops get a "Road closed … Tell the driver" note when Paris Open Data lists a road closure within 150 m around that time. The note never changes the colour.

### Préfecture de Police orders

`prefecture.py` reads the Paris special bulletins of administrative orders ("recueils des actes administratifs spéciaux"), which are published as PDFs on prefectures-regions.gouv.fr. From each bulletin's contents list it keeps orders about demonstrations, security zones, police measures, weapons bans and police drones at protests. For each order it works out the days covered from the title, and finds the streets and stations its text names (using `data/paris_places.json`). Each bulletin is downloaded once, and `.cache/prefecture.json` is kept between runs. The listing pages are checked at most every 30 minutes.

In the app, orders are a **Police orders** category. An order naming a place within 800 m of your place or stop turns it amber, never red. An order restricting protests across Paris with no place named turns the status card amber ("Police protest restrictions in Paris today"). Exact perimeters are only drawn on maps attached to the PDFs, so the app links to the official order. **Help → Sources** shows how many bulletins and orders were read.

### Itinerary on the map

The map shows one day of the itinerary at a time: numbered pins in visiting order, joined by a dashed line, next to the hotel. During the trip it shows today; before the trip, use **‹ ›** to change day, or tap **Show this day on the map** in the Trip tab. Pins are coloured by the safety check for that stop's time. Ticked stops fade, and the next stop is outlined. Tap a pin for the time, any warning, notes, and **Directions**. The **Trip** chip hides or shows the layer. Only stops with `lat`/`lon` appear.

### Now / Next card

During the trip, the list at the bottom of the map starts with what's happening now and what comes next. It shows the stop's time, a countdown, any warning or road closure, and the item after it. **Done** ticks the stop, the same as in the Trip tab, and the pin button shows the stop on the map. In the evening the card shows tomorrow's first item.

### Offline map

**Help → Save the map for offline** downloads central Paris down to street level, the area around every itinerary stop outside Paris (La Vallée, Versailles, CDG), and the map's fonts and icons, about 15–25 MB in all. The service worker serves them when there's no signal. Map areas you've browsed are kept too, up to 3,000 files.

### Family setup link

**Help → Share setup link** builds a link that sets up another phone in one tap: the itinerary link, always-watched places, the taxi address, the meeting point, the embassy details, and alerts. The details sit after the `#` in the link, which browsers never send to a server. Treat it like the gist link and share it in the family chat only.

Each stop has a fixed `id`, so ticks survive updates. The file format is `schema_version: 1` with `days[].stops[]` (`id`, `time`, `title`, optional `end`, `notes`, `cost`, `ticket`, `maps_query`, `lat`, `lon`) and `prep[]` (`id`, `category`, `task`, `due`).

## Older files

`app.py` (Streamlit dashboard) and `monitor.py` (Telegram alerts) are the first prototype. They still work but are no longer the main app; `monitor.yml` no longer runs on a schedule.
