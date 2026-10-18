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

## Older files

`app.py` (Streamlit dashboard) and `monitor.py` (Telegram alerts) are the first prototype. They still work but are no longer the main app; `monitor.yml` no longer runs on a schedule.
