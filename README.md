# Paris Protest & Safety Navigator (MVP)

A personal, cloud-hosted dashboard for Paris travel planning. It combines:
- Paris Open Data traffic-event records (when available)
- Optional RSS feeds that you configure
- Clear source timestamps and freshness labels
- A cautious, rule-based risk flag (not a prediction of violence)
- Telegram notifications from a scheduled GitHub Actions job

**Important:** This MVP does not have privileged police data and cannot reliably predict the exact location or violence level of protests. A missing alert does not mean an area is safe. Confirm important decisions with official authorities, transport operators and current local reporting.

## What is included
- `app.py`: mobile-friendly Streamlit dashboard with a map and event list
- `monitor.py`: fetches configured sources and sends Telegram alerts for new matching items
- `.github/workflows/monitor.yml`: runs the monitor on a schedule
- `sources.json`: source configuration
- `.env.example`: local secrets template

## 1. Create the Telegram bot
1. In Telegram, open `@BotFather`.
2. Send `/newbot` and follow the instructions.
3. Copy the bot token somewhere private.
4. Open your new bot and press **Start**.
5. To get your chat ID, send a message to the bot, then open this URL in a browser (replace `BOT_TOKEN`):
   `https://api.telegram.org/botBOT_TOKEN/getUpdates`
6. Find `message.chat.id` in the response. Do not share the token or publish it in code.

If `getUpdates` is empty, send another message to the bot and refresh the URL.

## 2. Put the project on GitHub
1. Create a private repository on GitHub, for example `paris-safety-navigator`.
2. Upload all files from this folder, including the `.github/workflows` directory.
3. In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.
4. Add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Open **Actions** and enable workflows if GitHub asks.

The scheduled monitor runs every 15 minutes. GitHub scheduled jobs are best-effort and may be delayed. You can also run it manually from Actions → Paris safety monitor → Run workflow.

## 3. Deploy the dashboard to Streamlit Community Cloud
1. Visit https://share.streamlit.io/ and sign in with GitHub.
2. Choose **Create app**, select your private repository, branch, and `app.py`.
3. If the service requires repository access or a plan change for a private repo, follow the current Streamlit instructions. You can instead use a public repo, but never place secrets in it.
4. Open the app's **Settings / Secrets** area and add:
   ```toml
   TELEGRAM_BOT_TOKEN = "your-token"
   TELEGRAM_CHAT_ID = "your-chat-id"
   ```
   The dashboard itself does not need Telegram credentials to display events; these are primarily used by the monitor.
5. Open the deployed URL on your Android phone and use **Add to Home screen** in your browser.

## 4. Run locally (optional)
Requires Python 3.11 or newer.
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

To test Telegram delivery locally:
```bash
# Windows PowerShell:
$env:TELEGRAM_BOT_TOKEN="your-token"
$env:TELEGRAM_CHAT_ID="your-chat-id"
python monitor.py
```

## Sources and limitations
- Paris Open Data endpoint configured in `sources.json` is a general traffic-event dataset, not a dedicated protest feed. It may contain road events, works or closures rather than protest reports.
- RSS sources are optional and intentionally left unconfigured until you choose feeds you trust. Add only feeds that are publicly accessible and whose terms permit automated access.
- The MVP's risk flag is a transparent keyword heuristic. It is not machine learning, not a verified danger score, and not a guarantee of safety.
- This app does not continuously track your GPS location. It only uses places you enter manually.
- Respect source terms, API limits and copyright. The app stores only a short event summary and source URL.

## Recommended next improvements
1. Add official RATP / Île-de-France Mobilités feeds or API access after confirming their current API terms and credentials.
2. Add a geocoding step with a permitted provider to map RSS items with place names.
3. Add manual confirmation and duplicate detection for reports.
4. Add route checks using a routing provider with live closure data where available.
5. Test alert quality for a week before relying on it during travel.
