/* Paris Pulse — save the map for offline use.
   Downloads the map of central Paris (up to the most detailed zoom the tiles have), the area around
   every itinerary stop and saved place outside it, and the fonts and icons the map needs, into the
   same cache the service worker reads from. In the Métro or without data the map still draws.
   Loaded after app.js and trip.js. */
"use strict";

const MAP_CACHE = "pulse-map";
const PARIS_BOX = { s: 48.815, n: 48.902, w: 2.224, e: 2.470 };
const offline = { busy: false, done: 0, total: 0, error: "", info: store.get("offline-map", null) };

const lon2x = (lon, z) => Math.floor(((lon + 180) / 360) * 2 ** z);
const lat2y = (lat, z) => { const r = (lat * Math.PI) / 180; return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z); };

/** Tile coordinates covering a box at zoom z. */
function tilesIn(box, z) {
  const out = [];
  for (let x = lon2x(box.w, z); x <= lon2x(box.e, z); x++)
    for (let y = lat2y(box.n, z); y <= lat2y(box.s, z); y++) out.push([z, x, y]);
  return out;
}
const around = (p, km) => { const dLat = km / 111, dLon = km / (111 * Math.cos((p.lat * Math.PI) / 180)); return { s: p.lat - dLat, n: p.lat + dLat, w: p.lon - dLon, e: p.lon + dLon }; };
const inBox = (p, b) => p.lat >= b.s && p.lat <= b.n && p.lon >= b.w && p.lon <= b.e;

/** Every point the family will be at: itinerary stops, the hotel, saved places. */
function offlinePoints() {
  const pts = state.places.slice();
  const h = typeof tripHotel === "function" && tripHotel(); if (h) pts.push(h);
  if (trip.data) for (const d of trip.data.days) for (const s of d.stops) if (s.lat != null) pts.push(s);
  return pts;
}

async function getJSON(url, cache) {
  const res = await fetch(url, { mode: "cors" });
  if (!res.ok) throw new Error(`The map server answered ${res.status}.`);
  await cache.put(url, res.clone());
  return res.json();
}

/** Everything the map needs, as a list of URLs. */
async function offlineUrls(cache) {
  const urls = new Set(), styles = [STYLE.light, STYLE.dark];
  for (const styleUrl of styles) {
    const style = await getJSON(styleUrl, cache);
    for (const src of Object.values(style.sources || {})) {
      if (src.type !== "vector" || !src.url) continue;
      const tj = await getJSON(src.url, cache);
      const tmpl = tj.tiles && tj.tiles[0]; if (!tmpl) continue;
      const max = Math.min(tj.maxzoom ?? 14, 14);
      const add = (box, zFrom) => { for (let z = zFrom; z <= max; z++) for (const [tz, x, y] of tilesIn(box, z)) urls.add(tmpl.replace("{z}", tz).replace("{x}", x).replace("{y}", y)); };
      add(PARIS_BOX, 9);
      for (const p of offlinePoints()) if (!inBox(p, PARIS_BOX)) add(around(p, 1.2), 11);   // La Vallée, Versailles, the airport…
    }
    if (style.glyphs) {
      const stacks = new Set();
      for (const l of style.layers || []) { const f = l.layout && l.layout["text-font"]; if (Array.isArray(f) && f.every((x) => typeof x === "string")) stacks.add(f.join(",")); }
      for (const st of stacks) for (const range of ["0-255", "256-511", "8192-8447"]) urls.add(style.glyphs.replace("{fontstack}", encodeURIComponent(st)).replace("{range}", range));
    }
    for (const sp of [].concat(style.sprite || []).map((x) => (typeof x === "string" ? x : x.url))) {
      for (const sfx of ["", "@2x"]) for (const ext of [".json", ".png"]) urls.add(sp + sfx + ext);
    }
  }
  return [...urls];
}

async function saveOfflineMap() {
  if (offline.busy) return;
  if (!("caches" in window)) { offline.error = "This browser can't save the map."; return renderOfflineCard(); }
  offline.busy = true; offline.error = ""; offline.done = 0; offline.total = 0; renderOfflineCard();
  try {
    const cache = await caches.open(MAP_CACHE);
    const urls = await offlineUrls(cache);
    offline.total = urls.length; renderOfflineCard();
    let failed = 0, i = 0;
    const worker = async () => {
      while (i < urls.length) {
        const url = urls[i++];
        try {
          if (!(await cache.match(url))) { const res = await fetch(url, { mode: "cors" }); if (res.ok) await cache.put(url, res); else failed++; }
        } catch { failed++; }
        offline.done++; if (offline.done % 10 === 0) renderOfflineCard();
      }
    };
    await Promise.all([worker(), worker(), worker(), worker()]);   // a few at a time, to be gentle on the free map server
    if (failed > urls.length / 10) throw new Error("Part of the map didn't download. Try again on Wi-Fi.");
    offline.info = { at: new Date().toISOString(), count: urls.length - failed }; store.set("offline-map", offline.info);
  } catch (e) {
    offline.error = navigator.onLine === false ? "You're offline. Connect to Wi-Fi and try again." : e.message || "Saving the map didn't work.";
  }
  offline.busy = false; renderOfflineCard();
}

function renderOfflineCard() {
  const s = $("#offline-status"), btn = $("#offline-save"); if (!s) return;
  if (offline.busy) {
    const pct = offline.total ? Math.round((offline.done / offline.total) * 100) : 0;
    s.innerHTML = `<div class="bar"><span style="width:${pct}%"></span></div><p class="fine">Saving the map… ${pct}%</p>`;
    btn.textContent = "Saving…"; btn.disabled = true; return;
  }
  btn.disabled = false;
  btn.textContent = offline.info ? "Update the saved map" : "Save the map for offline";
  s.innerHTML = offline.error ? `<p class="error">${esc(offline.error)}</p>`
    : offline.info ? `<p class="fine">Saved ${esc(when(offline.info.at))}. The map works without signal.</p>` : "";
}

window.addEventListener("DOMContentLoaded", () => {
  $("#offline-save").onclick = saveOfflineMap;
  renderOfflineCard();
});
