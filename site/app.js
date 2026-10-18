/* Paris Pulse — personal situational-awareness app.
   Reads data.json (built every ~10 min by collect.py) and works out red/amber/green
   for the user's saved places entirely on this phone. */
"use strict";

const TZ = "Europe/Paris";
const REFRESH_MS = 2 * 60 * 1000;
const STALE_MIN = 30;          // data older than this is flagged
const DEAD_MIN = 90;           // older than this: the status can't be trusted
const NEAR_TERM_H = 2;
const R = { red: 500, amber: 2000, laterAmber: 1000, blocked: 500 };   // metres
const LEVELS = ["green", "amber", "red"];
const PARIS = { lat: 48.8566, lon: 2.3522 };
const PARIS_RADIUS = 8000;     // metres from the centre counted as "Paris" for the headline numbers

const LINE_COLORS = {
  "Métro 1": "#FFCD00", "Métro 2": "#003CA6", "Métro 3": "#837902", "Métro 3B": "#6EC4E8",
  "Métro 4": "#CF009E", "Métro 5": "#FF7E2E", "Métro 6": "#6ECA97", "Métro 7": "#FA9ABA",
  "Métro 7B": "#6ECA97", "Métro 8": "#E19BDF", "Métro 9": "#B6BD00", "Métro 10": "#C9910D",
  "Métro 11": "#704B1C", "Métro 12": "#007852", "Métro 13": "#6EC4E8", "Métro 14": "#62259D",
  "RER A": "#E3051C", "RER B": "#5291CE", "RER C": "#FFCE00", "RER D": "#00814F", "RER E": "#C04191",
};
const DARK_TEXT = new Set(["Métro 1", "Métro 3B", "Métro 5", "Métro 6", "Métro 7", "Métro 7B", "Métro 8",
  "Métro 9", "Métro 13", "RER C"]);

/* ---------- storage (may be unavailable; app must still work) ---------- */
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

const state = {
  data: null, loadedAt: null, error: null,
  places: store.get("places", []),
  gps: store.get("gps", false), me: null,
  layers: { demo: true, strike: true, disruption: true, works: false, road: false, places: true, ...store.get("layers", {}) },
  feedFilter: "all", wideArea: false, lastLevels: store.get("lastLevels", {}),
  picking: false,
};

/* ---------- helpers ---------- */
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (iso) => iso ? new Intl.DateTimeFormat("en-GB", { timeZone: TZ, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "";
const fmtClock = (iso) => iso ? new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "";
const sameParisDay = (a, b) => new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(a) === new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(b);
const minutesSince = (iso) => (Date.now() - new Date(iso).getTime()) / 60000;

function ago(iso) {
  const m = Math.max(0, Math.round(minutesSince(iso)));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h} h ago` : `${Math.floor(h / 24)} d ago`;
}

function distance(a, b) {
  const rad = Math.PI / 180, dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}
const fmtDist = (m) => m < 950 ? `${Math.round(m / 50) * 50} m` : `${(m / 1000).toFixed(1)} km`;

function timing(ev) {
  if (ev.status === "active") return ev.end ? `Now until ${sameParisDay(new Date(), new Date(ev.end)) ? fmtClock(ev.end) : fmtTime(ev.end)}` : "Now";
  if (ev.status === "upcoming") return `Starts ${fmtTime(ev.start)}`;
  return "";
}
const isNearTerm = (ev) => ev.status === "active" || (ev.start && new Date(ev.start) - Date.now() <= NEAR_TERM_H * 3600e3);
const isToday = (ev) => isNearTerm(ev) || (ev.start && new Date(ev.start) - Date.now() <= 24 * 3600e3);

function badge(line) {
  const color = LINE_COLORS[line];
  const isRer = line.startsWith("RER") || line.startsWith("Transilien");
  const isBus = line.startsWith("Bus");
  const label = line.replace(/^(Métro|RER|Transilien|Tram|Bus|Shuttle) /, "");
  const style = color ? `style="background:${color};color:${DARK_TEXT.has(line) ? "#14141a" : "#fff"}"` : "";
  return `<span class="badge${isRer ? " rer" : ""}${isBus ? " bus" : ""}" ${style} title="${esc(line)}">${esc(isBus ? line : label)}</span>`;
}

/* ---------- status levels for a point ---------- */
function nearestStop(ev, point) {
  const pts = ev.stops && ev.stops.length ? ev.stops : (ev.lat != null ? [{ n: "", lat: ev.lat, lon: ev.lon }] : []);
  let best = null;
  for (const s of pts) {
    const d = distance(point, s);
    if (!best || d < best.d) best = { d, stop: s };
  }
  return best;
}

function assess(point, events) {
  let level = "green", why = "Nothing flagged nearby", cause = null, bestRank = -1;
  for (const ev of events) {
    if (ev.status === "ended") continue;
    const near = nearestStop(ev, point);
    if (!near) continue;
    let l = null, rank = 0;
    if (ev.cat === "demo") {
      if (isNearTerm(ev) && near.d <= R.red) { l = "red"; rank = 30 - near.d / 1000; }
      else if (isNearTerm(ev) && near.d <= R.amber) { l = "amber"; rank = 20 - near.d / 1000; }
      else if (isToday(ev) && near.d <= R.laterAmber) { l = "amber"; rank = 15 - near.d / 1000; }
    } else if (ev.cat === "disruption" && ev.sev === "Blocking" && ev.status === "active" && near.d <= R.blocked) {
      l = "amber"; rank = 10 - near.d / 1000;
    }
    if (l && rank > bestRank) {
      bestRank = rank; level = l; cause = ev;
      const what = ev.cat === "demo" ? "Demonstration" : "Line blocked";
      const where = near.stop.n ? ` · ${near.stop.n}` : "";
      why = `${what} ${fmtDist(near.d)} away${where} · ${ev.status === "active" ? "now" : "from " + fmtClock(ev.start)}`;
    }
  }
  return { level, why, cause };
}

function inParis(ev) {
  const near = nearestStop(ev, PARIS);
  return !near || near.d <= PARIS_RADIUS;   // no coordinates (e.g. a whole line): keep
}

function worst(levels) { return levels.reduce((a, b) => (LEVELS.indexOf(b) > LEVELS.indexOf(a) ? b : a), "green"); }

/* ---------- data loading ---------- */
async function load() {
  try {
    const res = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
    state.error = null;
  } catch (err) {
    state.error = "Couldn't reach the server. Showing the last saved data.";
    if (!state.data) {
      try { const cached = await caches.match("data.json", { ignoreSearch: true }); if (cached) state.data = await cached.json(); } catch { /* none */ }
    }
  }
  state.loadedAt = new Date();
  render();
}

const events = () => (state.data ? state.data.events : []);
const watchPoints = () => {
  const pts = state.places.map((p) => ({ ...p, kind: "place" }));
  if (state.gps && state.me) pts.unshift({ id: "me", name: "You", lat: state.me.lat, lon: state.me.lon, kind: "me" });
  return pts;
};

/* ---------- plaque ---------- */
function renderPlaque() {
  const el = $("#plaque"), title = $("#plaque-title"), sub = $("#plaque-sub"), age = $("#plaque-age");
  if (!state.data) {
    el.dataset.level = "loading"; title.textContent = state.error ? "No data yet" : "Loading…";
    sub.textContent = state.error || "Checking official feeds"; age.textContent = ""; return;
  }
  const genAge = minutesSince(state.data.generated_at);
  age.textContent = (genAge > STALE_MIN ? "Old data · " : "") + ago(state.data.generated_at);
  if (genAge > DEAD_MIN) {
    el.dataset.level = "stale"; title.textContent = "Data is out of date";
    sub.textContent = "Check the official apps (SOS tab) until it updates"; return;
  }
  const evs = events();
  const demosNow = evs.filter((e) => e.cat === "demo" && isNearTerm(e) && inParis(e)).length;
  const pts = watchPoints();
  let level, t, s;
  if (pts.length) {
    const results = pts.map((p) => ({ p, ...assess(p, evs) }));
    level = worst(results.map((r) => r.level));
    const top = results.find((r) => r.level === level) || results[0];
    t = level === "red" ? `Red near ${top.p.name}` : level === "amber" ? `Amber near ${top.p.name}` : "Clear near your places";
    s = level === "green" ? `${demosNow} demonstration notice${demosNow === 1 ? "" : "s"} elsewhere in Paris` : top.why;
  } else {
    level = demosNow ? "amber" : "green";
    t = demosNow ? `${demosNow} demonstration notice${demosNow === 1 ? "" : "s"} in Paris` : "No demonstration notices";
    s = "Add your hotel in Places for alerts near you";
  }
  if (state.error) s = `Offline · ${s}`;
  const was = el.dataset.level;
  el.dataset.level = level; title.textContent = t; sub.textContent = s;
  el.classList.toggle("fresh", level === "red" && was !== "red");
}

/* ---------- map ---------- */
let map, layerGroups = {};
function initMap() {
  map = L.map("map", { zoomControl: false, preferCanvas: true, attributionControl: true }).setView([48.8566, 2.3522], 13);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19, subdomains: "abcd",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
  }).addTo(map);
  map.attributionControl.setPosition("topright");
  for (const k of ["road", "works", "disruption", "strike", "demo", "places"]) layerGroups[k] = L.layerGroup().addTo(map);
  map.on("click", (e) => { if (state.picking) finishPick(e.latlng); });
}

function hull(points) {
  const p = points.map((s) => [s.lon, s.lat]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return null;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const pt of p) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], pt) <= 0) lower.pop(); lower.push(pt); }
  for (const pt of p.slice().reverse()) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], pt) <= 0) upper.pop(); upper.push(pt); }
  return lower.slice(0, -1).concat(upper.slice(0, -1)).map(([lon, lat]) => [lat, lon]);
}

function renderMap() {
  if (!map) return;
  Object.values(layerGroups).forEach((g) => g.clearLayers());
  for (const ev of events()) {
    const g = layerGroups[ev.cat];
    if (!g || !state.layers[ev.cat]) continue;
    const open = () => showDetail(ev);
    if (ev.cat === "road") {
      if (ev.lt) continue;
      if (ev.pts && ev.pts.length > 1) L.polyline(ev.pts, { color: "#8a97ad", weight: 4, opacity: 0.7 }).on("click", open).addTo(g);
      continue;
    }
    const stops = ev.stops || [];
    if (!stops.length) continue;
    if (ev.cat === "demo") {
      const soon = isNearTerm(ev);
      const color = soon ? "#ff2e48" : "#ff8a3d";
      // A soft disc around every affected stop: overlapping discs form the affected area.
      stops.forEach((s) => {
        L.circle([s.lat, s.lon], { radius: 200, stroke: false, fillColor: color, fillOpacity: soon ? 0.28 : 0.16, interactive: false }).addTo(g);
        L.circleMarker([s.lat, s.lon], { radius: 5, color: "#fff", weight: 1.5, fillColor: color, fillOpacity: 1 })
          .bindTooltip(s.n, { direction: "top" }).on("click", open).addTo(g);
      });
    } else {
      const color = { disruption: "#f2a20c", strike: "#b98cff" }[ev.cat] || "#6f8fc9";
      stops.forEach((s) => L.circleMarker([s.lat, s.lon], { radius: ev.cat === "works" ? 5 : 7, color: "#0c2147", weight: 2, fillColor: color, fillOpacity: 0.95 })
        .bindTooltip(s.n, { direction: "top" }).on("click", open).addTo(g));
    }
  }
  if (state.layers.places) {
    for (const p of watchPoints()) {
      const html = p.kind === "me" ? '<div class="me"></div>' : `<div class="pin">${esc(p.name)}</div>`;
      const icon = L.divIcon({ html, className: "", iconSize: null, iconAnchor: p.kind === "me" ? [8, 8] : [20, 12] });
      L.marker([p.lat, p.lon], { icon, keyboard: false }).addTo(layerGroups.places);
    }
  }
}

function renderLayerChips() {
  const items = [["demo", "Demonstrations"], ["strike", "Strikes"], ["disruption", "Métro/RER problems"], ["works", "Planned works"], ["road", "Roadworks"], ["places", "My places"]];
  $("#layers").innerHTML = items.map(([k, label]) => `<button class="layer" data-layer="${k}" aria-pressed="${!!state.layers[k]}">${label}</button>`).join("");
}

/* ---------- lists ---------- */
function rank(ev) {
  const cat = { demo: 0, strike: 1, disruption: 2, works: 3, road: 4 }[ev.cat] ?? 5;
  const time = ev.status === "active" ? 0 : 1;
  return [cat, time, ev.start ? new Date(ev.start).getTime() : 0];
}
const byRank = (a, b) => { const x = rank(a), y = rank(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i]; return 0; };

function row(ev, extra = "") {
  const when = ev.status === "active" ? '<span class="tag now">Now</span>' : (isToday(ev) ? '<span class="tag soon">Today</span>' : "");
  const lines = (ev.lines || []).slice(0, 6).map(badge).join("");
  const where = ev.stops && ev.stops.length ? ev.stops.slice(0, 3).map((s) => s.n).join(", ") : "";
  return `<button class="ev" data-id="${esc(ev.id)}" data-cat="${ev.cat}">
    <span class="ev-title">${when}${esc(ev.title)}</span>
    <span class="ev-meta">${esc(timing(ev))}${where ? " · " + esc(where) : ""}${extra}</span>
    ${lines ? `<span class="ev-lines">${lines}</span>` : ""}
  </button>`;
}

function renderNow() {
  const evs = events().filter(inParis);
  const demosNow = evs.filter((e) => e.cat === "demo" && isNearTerm(e));
  const demosLater = evs.filter((e) => e.cat === "demo" && !isNearTerm(e));
  const blocked = evs.filter((e) => (e.cat === "disruption" || e.cat === "strike") && e.status === "active");
  let html = `<div class="stats">
    <div class="stat red"><b>${demosNow.length}</b><span>demos now or within 2 h</span></div>
    <div class="stat"><b>${demosLater.length}</b><span>demos later this week</span></div>
    <div class="stat amber"><b>${blocked.length}</b><span>Métro/RER problems or strikes</span></div></div>
    <p class="lede">Central Paris (within 8 km of Notre-Dame).</p>`;
  const pts = watchPoints();
  if (pts.length) {
    html += `<p class="section-label">Your places</p>`;
    html += pts.map((p) => { const a = assess(p, events()); return `<div class="place"><span class="dot ${a.level}"></span><div class="place-main"><div class="place-name">${esc(p.name)}</div><div class="place-why">${esc(a.why)}</div></div></div>`; }).join("");
  }
  const top = demosNow.concat(blocked).sort(byRank);
  html += `<p class="section-label">Happening now</p>`;
  html += top.length ? top.map((e) => row(e)).join("") : `<p class="empty">No demonstration, strike or Métro/RER notices right now.</p>`;
  if (demosLater.length) html += `<p class="section-label">Coming up</p>` + demosLater.sort(byRank).slice(0, 8).map((e) => row(e)).join("");
  $("#now-body").innerHTML = html;
}

function renderFeed() {
  const evs = events().filter((e) => state.wideArea || inParis(e));
  const filters = [["all", "All"], ["demo", "Demonstrations"], ["strike", "Strikes"], ["disruption", "Métro/RER"], ["works", "Planned works"], ["road", "Roads"]];
  const count = (k) => k === "all" ? evs.filter((e) => e.cat !== "road").length : evs.filter((e) => e.cat === k && !(k === "road" && e.lt)).length;
  $("#feed-filters").innerHTML = filters.map(([k, label]) => `<button class="chip" role="tab" data-filter="${k}" aria-selected="${state.feedFilter === k}">${label} <span class="count">${count(k)}</span></button>`).join("")
    + `<button class="chip" data-wide="1" aria-selected="${state.wideArea}">Include suburbs</button>`;
  const list = evs.filter((e) => state.feedFilter === "all" ? e.cat !== "road" : e.cat === state.feedFilter && !e.lt).sort(byRank);
  const sources = (state.data?.sources || []).map((s) => `${esc(s.name)}: ${s.ok ? `${s.count} items` : "unavailable"}`).join(" · ");
  $("#feed-list").innerHTML = (list.length ? list.map((e) => row(e)).join("") : `<p class="empty">Nothing in this category right now.</p>`)
    + `<p class="lede" style="margin-top:16px">${sources}</p>`;
}

function renderPlaces() {
  const evs = events();
  $("#places-list").innerHTML = state.places.length ? state.places.map((p) => {
    const a = assess(p, evs);
    return `<div class="place"><span class="dot ${a.level}"></span><div class="place-main"><div class="place-name">${esc(p.name)}</div><div class="place-why">${esc(a.why)}</div></div><button class="icon-btn" data-remove="${esc(p.id)}" aria-label="Remove ${esc(p.name)}">Remove</button></div>`;
  }).join("") : `<p class="empty">Add your hotel first, then the places you'll visit.</p>`;
  $("#gps-toggle").checked = !!state.gps;
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  const btn = $("#alerts-btn");
  btn.textContent = perm === "granted" ? "Alerts are on" : perm === "denied" ? "Alerts blocked in browser settings" : perm === "unsupported" ? "Alerts not supported here" : "Turn on alerts";
}

function render() {
  renderPlaque(); renderMap(); renderNow(); renderFeed(); renderPlaces(); checkAlerts();
}

/* ---------- detail ---------- */
function showDetail(ev) {
  const lines = (ev.lines || []).map(badge).join(" ");
  const stops = (ev.stops || []).map((s) => s.n).join(", ");
  const near = watchPoints().map((p) => { const n = nearestStop(ev, p); return n ? `${esc(p.name)}: ${fmtDist(n.d)}` : ""; }).filter(Boolean).join(" · ");
  $("#detail-body").innerHTML = `
    <p class="kv"><span class="tag official">${esc(ev.src)}</span>${ev.sev ? esc(ev.sev) : ""}</p>
    <h2 class="detail-title" id="detail-title">${esc(ev.title)}</h2>
    ${lines ? `<p>${lines}</p>` : ""}
    <p class="kv"><b>${esc(timing(ev))}</b>${ev.end && ev.status !== "active" ? ` · until ${esc(fmtTime(ev.end))}` : ""}</p>
    ${near ? `<p class="kv">Distance: <b>${near}</b></p>` : ""}
    ${stops ? `<p class="kv">Stops: ${esc(stops)}</p>` : ""}
    ${ev.text ? `<div class="detail-text">${esc(ev.text)}</div>` : ""}
    <p class="kv">${ev.updated ? `Notice updated ${esc(fmtTime(ev.updated))} · ` : ""}All times Paris time</p>
    <div class="row"><button class="btn" id="detail-map">Show on map</button>${ev.url ? `<a class="btn" style="text-align:center;text-decoration:none" href="${esc(ev.url)}" target="_blank" rel="noopener">Official source</a>` : ""}</div>`;
  $("#detail").hidden = false;
  $("#detail-map").onclick = () => {
    $("#detail").hidden = true; switchView("now");
    const pts = (ev.stops || []).map((s) => [s.lat, s.lon]).concat(ev.pts || []);
    if (pts.length) map.fitBounds(pts, { padding: [60, 60], maxZoom: 16 });
  };
}

/* ---------- places ---------- */
function savePlaces() { store.set("places", state.places); render(); }
function addPlace(lat, lon) {
  const name = $("#place-name").value.trim() || `Place ${state.places.length + 1}`;
  state.places.push({ id: String(Date.now()), name, lat, lon });
  $("#place-name").value = ""; $("#place-error").hidden = true; savePlaces();
}
function placeError(msg) { const e = $("#place-error"); e.textContent = msg; e.hidden = false; }
function finishPick(latlng) {
  state.picking = false; $("#pick-hint").hidden = true;
  addPlace(+latlng.lat.toFixed(5), +latlng.lng.toFixed(5)); switchView("places");
}

let watchId = null;
function startGps() {
  if (!navigator.geolocation) return;
  watchId = navigator.geolocation.watchPosition((p) => { state.me = { lat: p.coords.latitude, lon: p.coords.longitude }; render(); },
    () => { state.me = null; }, { enableHighAccuracy: false, maximumAge: 60000, timeout: 20000 });
}

/* ---------- alerts (system notifications while the app is open or recently used) ---------- */
async function checkAlerts() {
  if (!state.data || minutesSince(state.data.generated_at) > DEAD_MIN) return;
  const evs = events(), now = {}, worse = [];
  for (const p of watchPoints()) {
    const a = assess(p, evs); now[p.id] = a.level;
    const before = state.lastLevels[p.id] || "green";
    if (LEVELS.indexOf(a.level) > LEVELS.indexOf(before)) worse.push({ p, a });
  }
  state.lastLevels = now; store.set("lastLevels", now);
  if (!worse.length || !("Notification" in window) || Notification.permission !== "granted") return;
  const reg = await navigator.serviceWorker?.getRegistration();
  for (const { p, a } of worse) {
    const title = `${a.level === "red" ? "Red" : "Amber"} near ${p.name}`;
    const opts = { body: a.why, tag: `place-${p.id}`, renotify: true, icon: "icons/icon-192.png", vibrate: a.level === "red" ? [300, 120, 300] : [200] };
    try { reg ? reg.showNotification(title, opts) : new Notification(title, opts); } catch { /* ignore */ }
  }
}

/* ---------- navigation and events ---------- */
function switchView(name) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  if (name === "now" && map) setTimeout(() => map.invalidateSize(), 50);
}

function bind() {
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => switchView(b.dataset.view)));
  $("#plaque").onclick = () => { switchView("now"); load(); };
  $("#sheet-handle").onclick = () => { const s = $("#sheet"); s.dataset.state = s.dataset.state === "peek" ? "full" : "peek"; };
  $("#layers").onclick = (e) => {
    const k = e.target.closest("[data-layer]")?.dataset.layer; if (!k) return;
    state.layers[k] = !state.layers[k]; store.set("layers", state.layers); renderLayerChips(); renderMap();
  };
  $("#feed-filters").onclick = (e) => {
    if (e.target.closest("[data-wide]")) { state.wideArea = !state.wideArea; return renderFeed(); }
    const k = e.target.closest("[data-filter]")?.dataset.filter; if (k) { state.feedFilter = k; renderFeed(); }
  };
  document.addEventListener("click", (e) => {
    const evEl = e.target.closest(".ev[data-id]");
    if (evEl) { const ev = events().find((x) => x.id === evEl.dataset.id); if (ev) showDetail(ev); }
    const rm = e.target.closest("[data-remove]");
    if (rm) { state.places = state.places.filter((p) => p.id !== rm.dataset.remove); savePlaces(); }
  });
  $("#detail-close").onclick = () => ($("#detail").hidden = true);
  $("#detail").onclick = (e) => { if (e.target.id === "detail") $("#detail").hidden = true; };
  $("#add-here").onclick = () => {
    if (!navigator.geolocation) return placeError("This browser can't share your location. Use Pick on map.");
    navigator.geolocation.getCurrentPosition((p) => addPlace(+p.coords.latitude.toFixed(5), +p.coords.longitude.toFixed(5)),
      () => placeError("Location permission was refused. Use Pick on map instead."), { timeout: 15000 });
  };
  $("#add-pick").onclick = () => { state.picking = true; switchView("now"); $("#pick-hint").hidden = false; $("#sheet").dataset.state = "peek"; };
  $("#alerts-btn").onclick = async () => { if ("Notification" in window && Notification.permission === "default") await Notification.requestPermission(); renderPlaces(); };
  $("#gps-toggle").onchange = (e) => {
    state.gps = e.target.checked; store.set("gps", state.gps);
    if (state.gps) startGps(); else { if (watchId != null) navigator.geolocation.clearWatch(watchId); state.me = null; }
    render();
  };
  for (const id of ["hotel-address", "meeting-point", "embassy"]) {
    const el = $(`#${id}`); el.value = store.get(id, ""); el.oninput = () => store.set(id, el.value);
  }
  $("#show-taxi").onclick = () => { $("#taxi-text").textContent = $("#hotel-address").value || "Add the hotel address first."; $("#taxi").hidden = false; };
  $("#taxi-close").onclick = () => ($("#taxi").hidden = true);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") load(); });
}

/* ---------- start ---------- */
window.addEventListener("DOMContentLoaded", () => {
  bind(); renderLayerChips();
  const top = document.querySelector(".top");
  const setTop = () => document.documentElement.style.setProperty("--top-h", `${Math.ceil(top.getBoundingClientRect().height)}px`);
  if ("ResizeObserver" in window) new ResizeObserver(setTop).observe(top); else setTop();
  if (window.L) initMap(); else $("#map").innerHTML = '<p class="empty" style="padding:120px 16px">The map could not load. The lists below still work.</p>';
  if (state.gps) startGps();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  load(); setInterval(() => { if (document.visibilityState === "visible") load(); }, REFRESH_MS);
  setInterval(renderPlaque, 30000);
});
