/* Paris Pulse v2 — personal situational-awareness app.
   Reads data.json (built every ~10 min by collect.py). Red / amber / clear for the user's
   places and timed stops is worked out entirely on this phone; nothing personal is sent anywhere
   (except a place search, which goes to Photon only when the user types). */
"use strict";

const TZ = "Europe/Paris";
const REFRESH_MS = 2 * 60 * 1000;
const STALE_MIN = 30, DEAD_MIN = 90;
const NEAR_TERM_H = 2;
const R = { red: 500, amber: 2000, laterAmber: 1000, blocked: 500, stopAmber: 1500 };   // metres
const LEVELS = ["green", "amber", "red"];
const PARIS = { lat: 48.8566, lon: 2.3522 };
const PARIS_RADIUS = 8000;
const STYLE = { dark: "https://tiles.openfreemap.org/styles/dark", light: "https://tiles.openfreemap.org/styles/positron" };
const PHOTON = "https://photon.komoot.io/api/";

const LINE_COLORS = {
  "Métro 1": "#FFCD00", "Métro 2": "#003CA6", "Métro 3": "#837902", "Métro 3B": "#6EC4E8",
  "Métro 4": "#CF009E", "Métro 5": "#FF7E2E", "Métro 6": "#6ECA97", "Métro 7": "#FA9ABA",
  "Métro 7B": "#6ECA97", "Métro 8": "#E19BDF", "Métro 9": "#B6BD00", "Métro 10": "#C9910D",
  "Métro 11": "#704B1C", "Métro 12": "#007852", "Métro 13": "#6EC4E8", "Métro 14": "#62259D",
  "RER A": "#E3051C", "RER B": "#5291CE", "RER C": "#FFCE00", "RER D": "#00814F", "RER E": "#C04191",
};
const DARK_TEXT = new Set(["Métro 1", "Métro 3B", "Métro 5", "Métro 6", "Métro 7", "Métro 7B", "Métro 8", "Métro 9", "Métro 13", "RER C"]);
const CAT = {
  demo: { label: "Demonstration", plural: "Demonstrations", color: "#e5484d", icon: '<path d="M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>' },
  strike: { label: "Strike", plural: "Strikes", color: "#8e6cf0", icon: '<path d="M12 3v10M8.5 6.5 12 3l3.5 3.5"/><rect x="4" y="13" width="16" height="7" rx="2"/>' },
  disruption: { label: "Métro / RER problem", plural: "Métro / RER", color: "#f0a020", icon: '<rect x="6" y="3" width="12" height="14" rx="3"/><path d="M6 11h12M9 21l1.5-4M15 21l-1.5-4"/><circle cx="9" cy="14" r=".6"/><circle cx="15" cy="14" r=".6"/>' },
  works: { label: "Planned works", plural: "Planned works", color: "#5b7ee8", icon: '<path d="M4 20h16M7 20l4-14h2l4 14M9 14h6"/>' },
  road: { label: "Roadworks", plural: "Roads", color: "#8b93a1", icon: '<path d="M6 20 10 4M18 20 14 4M12 6v2M12 11v2M12 16v2"/>' },
};

/* ---------- storage (may be unavailable; the app must still work) ---------- */
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

const state = {
  data: null, error: null,
  places: store.get("places", []),
  stops: store.get("stops", []),
  gps: store.get("gps", false), me: null,
  layers: { demo: true, strike: true, disruption: true, works: false, road: false, ...store.get("layers2", {}) },
  feedFilter: "all", feedQuery: "", wide: false,
  day: null, lastLevels: store.get("lastLevels", {}), original: {},
  picking: false, chosen: null,
};

/* ---------- helpers ---------- */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (opts) => new Intl.DateTimeFormat("en-GB", { timeZone: TZ, ...opts });
const fTime = fmt({ hour: "2-digit", minute: "2-digit" });
const fDay = fmt({ weekday: "short", day: "numeric", month: "short" });
const fFull = fmt({ weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const fKey = new Intl.DateTimeFormat("en-CA", { timeZone: TZ });
const dayKey = (d) => fKey.format(d);
const clock = (iso) => (iso ? fTime.format(new Date(iso)) : "");
const when = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return dayKey(d) === dayKey(new Date()) ? fTime.format(d) : fFull.format(d);
};
const minutesSince = (iso) => (Date.now() - new Date(iso).getTime()) / 60000;
function ago(iso) {
  const m = Math.max(0, Math.round(minutesSince(iso)));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h} h ago` : `${Math.floor(h / 24)} d ago`;
}
function distance(a, b) {
  const r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLon = (b.lon - a.lon) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}
const fmtDist = (m) => (m < 950 ? `${Math.round(m / 50) * 50} m` : `${(m / 1000).toFixed(1)} km`);

/** Paris wall-clock date + time ("2026-12-04", "14:00") -> Date. */
function parisDate(day, time) {
  const [y, mo, d] = day.split("-").map(Number), [h, mi] = time.split(":").map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    .formatToParts(new Date(guess)).map((p) => [p.type, p.value]));
  const asParis = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute);
  return new Date(guess - (asParis - guess));
}

const titleOf = (ev) => (state.original[ev.id] ? ev.title : ev.title_en || ev.title);
const GENERIC = /(event|works?|construction|travaux|évènement|événement)\s*-\s*stop\s*\(s\)\s*not served|arrêts? non desservis?/i;
/** Short headline for lists: names the place instead of repeating "Event - Stop (s) not served". */
function headline(ev) {
  const t = titleOf(ev);
  if (!GENERIC.test(t) && !/^#?InfoTrafic/i.test(t)) return t;
  const where = (ev.stops || []).slice(0, 2).map((s) => s.n).join(" · ");
  const kind = { demo: "Demonstration", strike: "Strike", disruption: "Disruption", works: "Works" }[ev.cat] || "Notice";
  return where ? `${kind} · ${where}` : t;
}
const textOf = (ev) => (state.original[ev.id] ? ev.text : ev.text_en || ev.text);
const isNearTerm = (ev) => ev.status === "active" || (ev.start && new Date(ev.start) - Date.now() <= NEAR_TERM_H * 3600e3);
const isToday = (ev) => ev.status === "active" || (ev.start && dayKey(new Date(ev.start)) === dayKey(new Date()));
function timing(ev) {
  if (ev.status === "active") return ev.end ? `Now until ${when(ev.end)}` : "Now";
  if (ev.status === "upcoming") return `From ${when(ev.start)}`;
  return "";
}
function badge(line) {
  const color = LINE_COLORS[line], isBus = line.startsWith("Bus"), isRer = /^(RER|Transilien)/.test(line);
  const label = isBus ? line : line.replace(/^(Métro|RER|Transilien|Tram|Shuttle) /, "");
  const style = color ? ` style="background:${color};color:${DARK_TEXT.has(line) ? "#15161a" : "#fff"}"` : "";
  return `<span class="badge${isRer ? " rer" : ""}${isBus ? " bus" : ""}"${style} title="${esc(line)}">${esc(label)}</span>`;
}
const icon = (cat) => `<span class="tile ${cat}"><svg viewBox="0 0 24 24" aria-hidden="true">${(CAT[cat] || CAT.road).icon}</svg></span>`;

/* ---------- status levels ---------- */
function nearestStop(ev, point) {
  const pts = ev.stops && ev.stops.length ? ev.stops : ev.lat != null ? [{ n: "", lat: ev.lat, lon: ev.lon }] : [];
  let best = null;
  for (const s of pts) { const d = distance(point, s); if (!best || d < best.d) best = { d, stop: s }; }
  return best;
}
function inParis(ev) {
  const near = nearestStop(ev, PARIS);
  if (!near) return (ev.modes || []).some((m) => m !== "Bus");
  return near.d <= PARIS_RADIUS;
}
function describe(ev, near, at) {
  const what = ev.cat === "demo" ? "Demonstration" : "Line blocked";
  const where = near.stop.n ? ` · ${near.stop.n}` : "";
  const time = at ? "" : ev.status === "active" ? " · now" : ` · from ${when(ev.start)}`;
  return `${what} ${fmtDist(near.d)} away${where}${time}`;
}
/** Status for a place right now. */
function assess(point, events) {
  let best = { level: "green", why: "Nothing flagged nearby", cause: null }, rank = -1;
  for (const ev of events) {
    if (ev.status === "ended") continue;
    const near = nearestStop(ev, point); if (!near) continue;
    let l = null, r = 0;
    if (ev.cat === "demo") {
      if (isNearTerm(ev) && near.d <= R.red) { l = "red"; r = 30 - near.d / 1e3; }
      else if (isNearTerm(ev) && near.d <= R.amber) { l = "amber"; r = 20 - near.d / 1e3; }
      else if (isToday(ev) && near.d <= R.laterAmber) { l = "amber"; r = 15 - near.d / 1e3; }
    } else if (ev.cat === "disruption" && ev.sev === "Blocking" && ev.status === "active" && near.d <= R.blocked) { l = "amber"; r = 10 - near.d / 1e3; }
    if (l && r > rank) { rank = r; best = { level: l, why: describe(ev, near), cause: ev }; }
  }
  return best;
}
/** Status for a stop at a given time: checks one hour before to two hours after. */
function assessAt(point, events, at) {
  const from = at.getTime() - 3600e3, to = at.getTime() + 2 * 3600e3;
  let best = { level: "green", why: "Clear at that time", cause: null }, rank = -1;
  for (const ev of events) {
    if (ev.status === "ended") continue;
    const s = ev.start ? new Date(ev.start).getTime() : -Infinity, e = ev.end ? new Date(ev.end).getTime() : Infinity;
    if (e < from || s > to) continue;
    const near = nearestStop(ev, point); if (!near) continue;
    let l = null, r = 0;
    if (ev.cat === "demo" && near.d <= R.red) { l = "red"; r = 30 - near.d / 1e3; }
    else if (ev.cat === "demo" && near.d <= R.stopAmber) { l = "amber"; r = 20 - near.d / 1e3; }
    else if (ev.cat === "disruption" && ev.sev === "Blocking" && near.d <= R.blocked) { l = "amber"; r = 10 - near.d / 1e3; }
    if (l && r > rank) { rank = r; best = { level: l, why: describe(ev, near, true), cause: ev }; }
  }
  return best;
}
const worst = (ls) => ls.reduce((a, b) => (LEVELS.indexOf(b) > LEVELS.indexOf(a) ? b : a), "green");

/* ---------- data ---------- */
const events = () => (state.data ? state.data.events : []);
const parisEvents = () => events().filter(inParis);
function watchPoints() {
  const pts = state.places.map((p) => ({ ...p, kind: "place" }));
  if (state.gps && state.me) pts.unshift({ id: "me", name: "You", ...state.me, kind: "me" });
  return pts;
}
function upcomingStops(hours = 3) {
  const now = Date.now();
  return state.stops.map((s) => ({ ...s, at: parisDate(s.date, s.time) }))
    .filter((s) => s.at.getTime() >= now - 3600e3 && s.at.getTime() <= now + hours * 3600e3);
}
async function load() {
  try {
    const res = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json(); state.error = null;
  } catch {
    state.error = "offline";
    if (!state.data) { try { const c = await caches.match("data.json", { ignoreSearch: true }); if (c) state.data = await c.json(); } catch { /* none */ } }
  }
  render();
}

/* ---------- status card ---------- */
function renderStatus() {
  const el = $("#status"), t = $("#status-title"), s = $("#status-sub");
  if (!state.data) { el.dataset.level = "loading"; t.textContent = state.error ? "No data yet" : "Loading"; s.textContent = state.error ? "Check your connection" : "Checking official feeds…"; return; }
  const age = minutesSince(state.data.generated_at), fresh = ago(state.data.generated_at);
  if (age > DEAD_MIN) { el.dataset.level = "stale"; t.textContent = "Data is out of date"; s.textContent = `Last update ${fresh}. Use the official apps in Help.`; el._cause = null; return; }
  const evs = events(), pe = parisEvents();
  const demos = pe.filter((e) => e.cat === "demo" && isNearTerm(e)).length;
  const results = watchPoints().map((p) => ({ name: p.name, ...assess(p, evs) }))
    .concat(upcomingStops().map((st) => ({ name: `${st.name} at ${st.time}`, ...assessAt(st, evs, st.at) })));
  let level, title, sub, cause = null;
  if (results.length) {
    level = worst(results.map((r) => r.level));
    const top = results.find((r) => r.level === level) || results[0];
    cause = top.cause;
    title = level === "red" ? `Demonstration near ${top.name}` : level === "amber" ? `Watch out near ${top.name}` : "Clear near your places";
    sub = level === "green" ? `${demos} demonstration notice${demos === 1 ? "" : "s"} elsewhere in Paris` : top.why;
  } else {
    level = demos ? "amber" : "green";
    title = demos ? `${demos} demonstration notice${demos === 1 ? "" : "s"} in Paris` : "No demonstration notices";
    sub = "Add your hotel in Today for alerts near you";
  }
  sub += ` · ${age > STALE_MIN ? "data " : "updated "}${fresh}${state.error ? " · offline" : ""}`;
  el.dataset.level = level; t.textContent = title; s.textContent = sub; el._cause = cause;
  const badgeEl = $("#feed-badge"); badgeEl.hidden = !demos; badgeEl.textContent = demos;
}

/* ---------- map ---------- */
let map = null, markers = [];
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
function initMap() {
  if (!window.maplibregl) { $("#map").innerHTML = '<p class="empty" style="padding:140px 20px">The map could not load. The list still works.</p>'; return; }
  try {
    map = new maplibregl.Map({ container: "map", style: darkQuery.matches ? STYLE.dark : STYLE.light, center: [PARIS.lon, PARIS.lat], zoom: 12.2,
      attributionControl: { compact: true }, pitchWithRotate: false, dragRotate: false });
  } catch { $("#map").innerHTML = '<p class="empty" style="padding:140px 20px">This phone can\'t show the map. The list still works.</p>'; return; }
  map.touchZoomRotate.disableRotation();
  map.on("style.load", addLayers);
  map.on("click", (e) => {
    if (state.picking) return finishPick({ lat: e.lngLat.lat, lon: e.lngLat.lng });
    const hit = map.queryRenderedFeatures(e.point, { layers: ["dots", "roads"].filter((l) => map.getLayer(l)) })[0];
    if (hit) { const ev = events().find((x) => x.id === hit.properties.id); if (ev) showDetail(ev); }
  });
  for (const l of ["dots", "roads"]) {
    map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
  }
  darkQuery.addEventListener("change", (e) => map.setStyle(e.matches ? STYLE.dark : STYLE.light));
}
function geo() {
  const pts = [], lines = [];
  for (const ev of events()) {
    if (!state.layers[ev.cat]) continue;
    if (ev.cat === "road") { if (!ev.lt && ev.pts && ev.pts.length > 1) lines.push({ type: "Feature", properties: { id: ev.id }, geometry: { type: "LineString", coordinates: ev.pts.map(([a, b]) => [b, a]) } }); continue; }
    const soon = ev.cat === "demo" && isNearTerm(ev);
    for (const s of ev.stops || []) pts.push({ type: "Feature", properties: { id: ev.id, cat: ev.cat, soon }, geometry: { type: "Point", coordinates: [s.lon, s.lat] } });
  }
  return { pts: { type: "FeatureCollection", features: pts }, lines: { type: "FeatureCollection", features: lines } };
}
function addLayers() {
  const g = geo();
  map.addSource("events", { type: "geojson", data: g.pts });
  map.addSource("roadlines", { type: "geojson", data: g.lines });
  map.addLayer({ id: "roads", type: "line", source: "roadlines", paint: { "line-color": CAT.road.color, "line-width": 4, "line-opacity": 0.75 } });
  map.addLayer({ id: "glow", type: "circle", source: "events", filter: ["==", ["get", "cat"], "demo"], paint: {
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 10, 13, 26, 16, 70],
    "circle-color": ["case", ["get", "soon"], CAT.demo.color, "#ff8a3d"], "circle-opacity": ["case", ["get", "soon"], 0.32, 0.18], "circle-blur": 0.7 } });
  map.addLayer({ id: "dots", type: "circle", source: "events", paint: {
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3, 14, 6, 17, 9],
    "circle-color": ["match", ["get", "cat"], "demo", CAT.demo.color, "strike", CAT.strike.color, "disruption", CAT.disruption.color, CAT.works.color],
    "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5 } });
}
function renderMap() {
  if (!map) return;
  const g = geo();
  if (map.getSource("events")) { map.getSource("events").setData(g.pts); map.getSource("roadlines").setData(g.lines); }
  markers.forEach((m) => m.remove()); markers = [];
  const evs = events();
  for (const p of watchPoints()) {
    const el = document.createElement("div");
    if (p.kind === "me") el.className = "me";
    else { const lv = assess(p, evs).level; el.className = `pin ${lv}`; el.innerHTML = `<i>${esc(p.name.slice(0, 1).toUpperCase())}</i>${esc(p.name)}`; }
    markers.push(new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map));
  }
}
function renderLayerChips() {
  const counts = {}; for (const e of parisEvents()) counts[e.cat] = (counts[e.cat] || 0) + 1;
  $("#layers").innerHTML = ["demo", "strike", "disruption", "works", "road"].map((k) =>
    `<button class="chip" data-layer="${k}" aria-pressed="${!!state.layers[k]}"><span class="swatch" style="background:${CAT[k].color}"></span>${CAT[k].plural}${counts[k] ? ` <span class="n">${counts[k]}</span>` : ""}</button>`).join("");
}
function fitTo(points) {
  if (!map || !points.length) return;
  if (points.length === 1) return map.flyTo({ center: [points[0].lon, points[0].lat], zoom: 15 });
  const b = new maplibregl.LngLatBounds(); points.forEach((p) => b.extend([p.lon, p.lat]));
  map.fitBounds(b, { padding: { top: 170, bottom: 300, left: 40, right: 40 }, maxZoom: 16 });
}

/* ---------- bottom sheet ---------- */
const SNAPS = { peek: 0.70, half: 0.42, full: 0 };
function setSnap(name) {
  const sheet = $("#sheet"); sheet.dataset.snap = name; sheet.style.transform = "";
  const visible = (1 - SNAPS[name]) * 0.92;   // share of the view the sheet covers
  $("#locate").style.bottom = name === "full" ? "-80px" : `calc(${(visible * 100).toFixed(1)}% + 14px)`;
}
function bindSheet() {
  const sheet = $("#sheet"), grab = $("#sheet-grab"), body = $("#sheet-body");
  let startY = 0, startT = 0, dragging = false;
  const h = () => sheet.getBoundingClientRect().height;
  const current = () => SNAPS[sheet.dataset.snap] * h();
  const down = (e) => { dragging = true; startY = e.clientY; startT = current(); sheet.classList.add("dragging"); sheet.setPointerCapture?.(e.pointerId); };
  grab.addEventListener("pointerdown", down);
  body.addEventListener("pointerdown", (e) => { if (sheet.dataset.snap !== "full" || body.scrollTop <= 0) { startY = e.clientY; startT = current(); dragging = "maybe"; } });
  window.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dy = e.clientY - startY;
    if (dragging === "maybe") { if (Math.abs(dy) < 8 || (sheet.dataset.snap === "full" && dy < 0)) return; dragging = true; sheet.classList.add("dragging"); }
    const t = Math.min(Math.max(startT + dy, 0), SNAPS.peek * h());
    sheet.style.transform = `translateY(${t}px)`;
  });
  window.addEventListener("pointerup", (e) => {
    if (!dragging) return;
    const wasDrag = dragging === true; dragging = false; sheet.classList.remove("dragging");
    if (!wasDrag) return;
    if (Math.abs(e.clientY - startY) < 6) {   // a tap on the handle: step through the heights
      const next = { peek: "half", half: "full", full: "peek" }[sheet.dataset.snap];
      return setSnap(next);
    }
    const t = startT + (e.clientY - startY), frac = t / h();
    const name = Object.entries(SNAPS).sort((a, b) => Math.abs(a[1] - frac) - Math.abs(b[1] - frac))[0][0];
    setSnap(name);
  });
  grab.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSnap({ peek: "half", half: "full", full: "peek" }[sheet.dataset.snap]); } });
  setSnap("peek");
}

/* ---------- lists ---------- */
const order = { demo: 0, strike: 1, disruption: 2, works: 3, road: 4 };
const byRank = (a, b) => (order[a.cat] - order[b.cat]) || ((a.status === "active" ? 0 : 1) - (b.status === "active" ? 0 : 1))
  || ((a.start ? +new Date(a.start) : 0) - (b.start ? +new Date(b.start) : 0));
function row(ev) {
  const tag = ev.status === "active" ? '<span class="when now">Now</span>' : isToday(ev) ? `<span class="when today">${clock(ev.start)}</span>` : `<span class="when later">${ev.start ? fDay.format(new Date(ev.start)) : ""}</span>`;
  const lines = (ev.lines || []).slice(0, 6).map(badge).join("");
  const where = (ev.stops || []).slice(0, 3).map((s) => s.n).join(", ");
  return `<button class="ev" data-id="${esc(ev.id)}">${icon(ev.cat)}
    <span class="ev-main"><span class="ev-title">${esc(headline(ev))}</span>
      <span class="ev-meta">${esc(timing(ev))}${where ? ` · ${esc(where)}` : ""}</span>
      ${lines ? `<span class="ev-lines">${lines}</span>` : ""}</span>${tag}</button>`;
}
function placeRow(p, a, time = "") {
  return `<div class="place" data-place="${esc(p.id)}">${time ? `<span class="place-time">${esc(time)}</span>` : `<span class="dot ${a.level}"></span>`}
    <div><div class="place-name">${time ? `<span class="dot ${a.level}"></span> ` : ""}${esc(p.name)}</div><div class="place-why ${a.level}">${esc(a.why)}</div></div>
    ${p.kind === "me" ? "" : `<button class="icon-btn" data-remove="${esc(p.id)}" aria-label="Remove ${esc(p.name)}"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button>`}</div>`;
}
const skeleton = () => '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';

function renderSheet() {
  if (!state.data) { $("#sheet-body").innerHTML = skeleton(); return; }
  const pe = parisEvents(), evs = events();
  const now = pe.filter((e) => (e.cat === "demo" && isNearTerm(e)) || ((e.cat === "strike" || e.cat === "disruption") && e.status === "active")).sort(byRank);
  const later = pe.filter((e) => e.cat === "demo" && !isNearTerm(e)).sort(byRank);
  const pts = watchPoints();
  let html = `<p class="sheet-title">${pts.length ? "Near your places" : "Happening in Paris"}</p>`;
  if (pts.length) html += `<div class="list">${pts.map((p) => placeRow(p, assess(p, evs))).join("")}</div><p class="section-label">Happening now</p>`;
  html += now.length ? `<div class="list">${now.map(row).join("")}</div>` : '<p class="empty">No demonstration, strike or Métro/RER notices right now.</p>';
  if (later.length) html += `<p class="section-label">Coming up</p><div class="list">${later.slice(0, 10).map(row).join("")}</div>`;
  $("#sheet-body").innerHTML = html;
}

function renderFeed() {
  if (!state.data) { $("#feed-list").innerHTML = skeleton(); return; }
  const base = events().filter((e) => (state.wide || inParis(e)) && !(e.cat === "road" && e.lt));
  const q = state.feedQuery.trim().toLowerCase();
  const matches = (e) => !q || [titleOf(e), textOf(e), ...(e.lines || []), ...(e.stops || []).map((s) => s.n)].join(" ").toLowerCase().includes(q);
  const cats = ["all", "demo", "strike", "disruption", "works", "road"];
  const count = (k) => base.filter((e) => (k === "all" ? e.cat !== "road" : e.cat === k) && matches(e)).length;
  $("#feed-filters").innerHTML = cats.map((k) => `<button class="chip" role="tab" data-filter="${k}" aria-selected="${state.feedFilter === k}">${k === "all" ? "All" : CAT[k].plural} <span class="n">${count(k)}</span></button>`).join("")
    + `<button class="chip" data-wide="1" aria-pressed="${state.wide}">Suburbs</button>`;
  const list = base.filter((e) => (state.feedFilter === "all" ? e.cat !== "road" : e.cat === state.feedFilter) && matches(e)).sort(byRank);
  const groups = [["Now", list.filter((e) => e.status === "active")], ["Later today", list.filter((e) => e.status !== "active" && isToday(e))], ["This week", list.filter((e) => e.status !== "active" && !isToday(e))]];
  $("#feed-list").innerHTML = list.length ? groups.filter(([, l]) => l.length).map(([h, l]) => `<div class="group-head">${h}</div><div class="list">${l.map(row).join("")}</div>`).join("")
    : `<p class="empty">${q ? "Nothing matches that search." : "Nothing in this category right now."}</p>`;
  $("#feed-sub").textContent = `${state.wide ? "Île-de-France" : "Central Paris"} · updated ${ago(state.data.generated_at)}`;
}

function renderToday() {
  const today = new Date(), days = [];
  for (let i = 0; i < 7; i++) days.push(dayKey(new Date(today.getTime() + i * 864e5)));
  if (!state.day || !days.includes(state.day)) state.day = days[0];
  $("#days").innerHTML = days.map((d, i) => `<button class="chip" data-day="${d}" aria-selected="${state.day === d}">${i === 0 ? "Today" : fDay.format(parisDate(d, "12:00"))}</button>`).join("");
  $("#today-title").textContent = state.day === days[0] ? "Today" : fDay.format(parisDate(state.day, "12:00"));
  const evs = events(), pe = parisEvents();
  const dayStart = parisDate(state.day, "00:00").getTime(), dayEnd = dayStart + 864e5;
  const onDay = (e) => e.status !== "ended" && (e.start ? +new Date(e.start) : 0) < dayEnd && (e.end ? +new Date(e.end) : Infinity) > dayStart;
  const demos = pe.filter((e) => e.cat === "demo" && onDay(e)).length;
  const strikes = pe.filter((e) => e.cat === "strike" && onDay(e));
  $("#today-sub").textContent = state.data ? `${demos} demonstration notice${demos === 1 ? "" : "s"} · ${strikes.length} strike${strikes.length === 1 ? "" : "s"} in Paris` : "";
  const strikeLines = [...new Set(strikes.flatMap((e) => e.lines || []))].slice(0, 8);
  $("#today-banner").innerHTML = strikes.length ? `<div class="banner violet"><b>Strike notices${strikeLines.length ? `: ${esc(strikeLines.join(", "))}` : ""}</b><span>Expect reduced service. Check before travelling.</span></div>` : "";
  const pts = watchPoints();
  $("#places-list").innerHTML = pts.length ? pts.map((p) => placeRow(p, assess(p, evs))).join("") : '<p class="empty">Add your hotel first. It is checked all day, every day.</p>';
  const stops = state.stops.filter((s) => s.date === state.day).sort((a, b) => a.time.localeCompare(b.time));
  $("#stops-list").innerHTML = stops.length ? stops.map((s) => placeRow(s, assessAt(s, evs, parisDate(s.date, s.time)), s.time)).join("")
    : '<p class="empty">Add where you plan to be and when. Each stop is checked for that time.</p>';
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  $("#alerts-toggle").checked = perm === "granted" && store.get("alerts", false);
  $("#alerts-note").textContent = perm === "denied" ? "Blocked in browser settings" : perm === "unsupported" ? "Not supported in this browser" : "Notify me when a place or stop turns amber or red";
  $("#gps-toggle").checked = !!state.gps;
}

function renderHelp() {
  const d = state.data;
  $("#sources").innerHTML = d ? d.sources.map((s) => `<div class="src-row"><span>${esc(s.name)}</span><span class="${s.ok ? "" : "bad"}">${s.ok ? `${s.count} items` : "Unavailable"}</span></div>`).join("")
    + `<div class="src-row"><span>Last update</span><span>${esc(ago(d.generated_at))}</span></div>` : '<p class="empty">No data yet.</p>';
}

function render() { renderStatus(); renderLayerChips(); renderMap(); renderSheet(); renderFeed(); renderToday(); renderHelp(); checkAlerts(); }

/* ---------- detail ---------- */
function showDetail(ev) {
  const translated = !!(ev.title_en || ev.text_en), orig = !!state.original[ev.id];
  const near = watchPoints().map((p) => { const n = nearestStop(ev, p); return n ? `${esc(p.name)} ${fmtDist(n.d)}` : ""; }).filter(Boolean).join(" · ");
  const c = nearestStop(ev, PARIS) && (ev.stops && ev.stops.length ? ev.stops[Math.floor(ev.stops.length / 2)] : { lat: ev.lat, lon: ev.lon });
  const lines = (ev.lines || []).map(badge).join(" ");
  $("#detail-body").innerHTML = `
    <div class="pills"><span class="pill ${ev.cat}">${esc((CAT[ev.cat] || CAT.road).label)}</span><span class="pill">${esc(ev.src)}</span>${ev.sev ? `<span class="pill">${esc(ev.sev)}</span>` : ""}</div>
    <h2 class="detail-title" id="detail-title">${esc(headline(ev))}</h2>
    ${headline(ev) !== titleOf(ev) ? `<p class="detail-meta" style="margin-top:-2px">${esc(titleOf(ev))}</p>` : ""}
    <p class="detail-meta"><b>${esc(timing(ev))}</b>${ev.end && ev.status !== "active" ? ` · until ${esc(when(ev.end))}` : ""}${near ? ` · ${near}` : ""}</p>
    ${lines ? `<p class="ev-lines" style="margin:0 0 12px">${lines}</p>` : ""}
    ${textOf(ev) ? `<div class="msg">${translated ? `<p class="msg-note"><span>${orig ? "Original French" : "Translated from French"}</span><button id="toggle-orig">${orig ? "Show English" : "Show original"}</button></p>` : ""}${esc(textOf(ev))}</div>` : ""}
    ${ev.why ? `<p class="kv">Flagged because the operator wrote <b>“${esc(ev.why)}”</b></p>` : ""}
    ${ev.stops && ev.stops.length ? `<p class="kv">Stops: <b>${esc(ev.stops.map((s) => s.n).join(", "))}</b></p>` : ""}
    <p class="kv">${ev.updated ? `Notice updated ${esc(when(ev.updated))} · ` : ""}All times Paris time</p>
    <div class="row-2"><button class="btn" id="d-map">Show on map</button>${c && c.lat != null ? `<a class="btn" target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${c.lat},${c.lon}">Google Maps</a>` : ""}</div>
    ${ev.url ? `<a class="btn btn-wide" target="_blank" rel="noopener" href="${esc(ev.url)}">Official source</a>` : ""}`;
  $("#detail").hidden = false;
  const t = $("#toggle-orig"); if (t) t.onclick = () => { state.original[ev.id] = !state.original[ev.id]; showDetail(ev); };
  $("#d-map").onclick = () => {
    $("#detail").hidden = true; switchView("map"); setSnap("peek");
    fitTo((ev.stops && ev.stops.length ? ev.stops : []).concat((ev.pts || []).map(([lat, lon]) => ({ lat, lon }))).concat(ev.lat != null && !(ev.stops || []).length ? [{ lat: ev.lat, lon: ev.lon }] : []));
  };
}

/* ---------- add place / stop ---------- */
function openAdd() {
  state.chosen = null; $("#add-query").value = ""; $("#add-results").innerHTML = ""; $("#add-chosen").hidden = true; $("#add-error").hidden = true;
  $("#add").hidden = false; setTimeout(() => $("#add-query").focus(), 100);
}
function choose(place) {
  state.chosen = place; $("#add-chosen").hidden = false; $("#add-results").innerHTML = "";
  $("#add-name").value = place.name || ""; $("#add-error").hidden = true;
}
let searchTimer = null, searchSeq = 0;
function search(q) {
  clearTimeout(searchTimer);
  if (q.trim().length < 3) { $("#add-results").innerHTML = ""; return; }
  searchTimer = setTimeout(async () => {
    const seq = ++searchSeq;
    try {
      const url = `${PHOTON}?q=${encodeURIComponent(q)}&lat=${PARIS.lat}&lon=${PARIS.lon}&limit=6&lang=en`;
      const res = await fetch(url); if (!res.ok) throw new Error();
      const data = await res.json(); if (seq !== searchSeq) return;
      const items = (data.features || []).map((f) => {
        const p = f.properties || {}, [lon, lat] = f.geometry.coordinates;
        const name = p.name || [p.housenumber, p.street].filter(Boolean).join(" ") || "Place";
        const sub = [p.street && p.name ? p.street : "", p.postcode, p.city].filter(Boolean).join(", ");
        return { name, sub, lat, lon };
      });
      state.results = items;
      $("#add-results").innerHTML = items.length ? items.map((it, i) => `<button class="ev" data-result="${i}"><span class="ev-main"><span class="ev-title">${esc(it.name)}</span><span class="ev-meta">${esc(it.sub)}</span></span></button>`).join("")
        : '<p class="empty">No matches. Try Pick on map.</p>';
    } catch { if (seq === searchSeq) $("#add-results").innerHTML = '<p class="empty">Search isn\'t reachable right now. Use Pick on map.</p>'; }
  }, 400);
}
function saveChosen() {
  const c = state.chosen; if (!c) return;
  const name = $("#add-name").value.trim() || c.name || "Place";
  const kind = document.querySelector('input[name="when"]:checked').value;
  const item = { id: String(Date.now()), name: name.slice(0, 40), lat: +(+c.lat).toFixed(5), lon: +(+c.lon).toFixed(5) };
  if (kind === "stop") { state.stops.push({ ...item, date: state.day || dayKey(new Date()), time: $("#add-time").value || "10:00" }); store.set("stops", state.stops); }
  else { state.places.push(item); store.set("places", state.places); }
  $("#add").hidden = true; render();
}
function finishPick(pt) {
  state.picking = false; $("#pick-hint").hidden = true;
  switchView("today"); $("#add").hidden = false; choose({ name: "", ...pt }); $("#add-name").focus();
}

/* ---------- location ---------- */
let watchId = null;
function startGps() {
  if (!navigator.geolocation) return;
  watchId = navigator.geolocation.watchPosition((p) => { state.me = { lat: p.coords.latitude, lon: p.coords.longitude }; render(); }, () => { state.me = null; }, { maximumAge: 60000, timeout: 20000 });
}

/* ---------- alerts: system notifications while the app is open or recently used ---------- */
async function checkAlerts() {
  if (!state.data || minutesSince(state.data.generated_at) > DEAD_MIN) return;
  const evs = events(), now = {}, worse = [];
  for (const p of watchPoints()) { const a = assess(p, evs); now[p.id] = a.level; if (LEVELS.indexOf(a.level) > LEVELS.indexOf(state.lastLevels[p.id] || "green")) worse.push({ name: p.name, a }); }
  for (const s of upcomingStops()) { const a = assessAt(s, evs, s.at); const k = `stop-${s.id}`; now[k] = a.level; if (LEVELS.indexOf(a.level) > LEVELS.indexOf(state.lastLevels[k] || "green")) worse.push({ name: `${s.name} at ${s.time}`, a }); }
  state.lastLevels = now; store.set("lastLevels", now);
  if (worse.some((w) => w.a.level === "red")) navigator.vibrate?.([250, 100, 250]);
  if (!worse.length || !store.get("alerts", false) || !("Notification" in window) || Notification.permission !== "granted") return;
  const reg = await navigator.serviceWorker?.getRegistration();
  for (const { name, a } of worse) {
    const title = a.level === "red" ? `Demonstration near ${name}` : `Watch out near ${name}`;
    const opts = { body: a.why, tag: `pulse-${name}`, renotify: true, icon: "icons/icon-192.png" };
    try { reg ? reg.showNotification(title, opts) : new Notification(title, opts); } catch { /* ignore */ }
  }
}

/* ---------- navigation and events ---------- */
function switchView(name) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  if (name === "map" && map) setTimeout(() => map.resize(), 30);
}
function bindPullToRefresh() {
  const page = $("#feed-page"), ptr = $("#ptr"); let y0 = null;
  page.addEventListener("touchstart", (e) => { y0 = page.scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
  page.addEventListener("touchmove", (e) => { if (y0 != null) ptr.classList.toggle("show", e.touches[0].clientY - y0 > 70); }, { passive: true });
  page.addEventListener("touchend", () => { if (ptr.classList.contains("show")) { ptr.textContent = "Refreshing…"; load().then(() => { ptr.classList.remove("show"); ptr.textContent = "Release to refresh"; }); } y0 = null; });
}
function bind() {
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => switchView(b.dataset.view)));
  $("#status").onclick = () => { const c = $("#status")._cause; if (c) showDetail(c); else { switchView("map"); setSnap("half"); } };
  $("#layers").onclick = (e) => { const k = e.target.closest("[data-layer]")?.dataset.layer; if (!k) return; state.layers[k] = !state.layers[k]; store.set("layers2", state.layers); renderLayerChips(); renderMap(); };
  $("#locate").onclick = () => {
    if (state.me) return fitTo([state.me]);
    navigator.geolocation?.getCurrentPosition((p) => { state.me = { lat: p.coords.latitude, lon: p.coords.longitude }; fitTo([state.me]); renderMap(); }, () => fitTo(state.places.length ? state.places : [PARIS]));
  };
  $("#feed-filters").onclick = (e) => {
    if (e.target.closest("[data-wide]")) { state.wide = !state.wide; return renderFeed(); }
    const k = e.target.closest("[data-filter]")?.dataset.filter; if (k) { state.feedFilter = k; renderFeed(); }
  };
  $("#feed-search").oninput = (e) => { state.feedQuery = e.target.value; renderFeed(); };
  $("#days").onclick = (e) => { const d = e.target.closest("[data-day]")?.dataset.day; if (d) { state.day = d; renderToday(); } };
  document.addEventListener("click", (e) => {
    const evEl = e.target.closest(".ev[data-id]");
    if (evEl) { const ev = events().find((x) => x.id === evEl.dataset.id); if (ev) showDetail(ev); return; }
    const rm = e.target.closest("[data-remove]");
    if (rm) {
      e.stopPropagation(); const id = rm.dataset.remove;
      state.places = state.places.filter((p) => p.id !== id); state.stops = state.stops.filter((s) => s.id !== id);
      store.set("places", state.places); store.set("stops", state.stops); return render();
    }
    const pl = e.target.closest(".place[data-place]");
    if (pl && pl.closest("#sheet-body")) { const p = watchPoints().find((x) => x.id === pl.dataset.place); if (p) { fitTo([p]); setSnap("peek"); } }
    const r = e.target.closest("[data-result]"); if (r) choose(state.results[+r.dataset.result]);
  });
  $("#detail-close").onclick = () => ($("#detail").hidden = true);
  $("#detail").onclick = (e) => { if (e.target.id === "detail") $("#detail").hidden = true; };
  $("#add-open").onclick = openAdd;
  $("#add-close").onclick = () => ($("#add").hidden = true);
  $("#add").onclick = (e) => { if (e.target.id === "add") $("#add").hidden = true; };
  $("#add-query").oninput = (e) => search(e.target.value);
  $("#add-here").onclick = () => {
    if (!navigator.geolocation) { $("#add-error").textContent = "This browser can't share your location. Use Pick on map."; $("#add-error").hidden = false; return; }
    navigator.geolocation.getCurrentPosition((p) => choose({ name: "", lat: p.coords.latitude, lon: p.coords.longitude }),
      () => { $("#add-error").textContent = "Location permission was refused. Use Pick on map."; $("#add-error").hidden = false; }, { timeout: 15000 });
  };
  $("#add-pick").onclick = () => { $("#add").hidden = true; state.picking = true; switchView("map"); setSnap("peek"); $("#pick-hint").hidden = false; };
  $("#pick-cancel").onclick = (e) => { e.stopPropagation(); state.picking = false; $("#pick-hint").hidden = true; };
  document.querySelectorAll('input[name="when"]').forEach((r) => (r.onchange = () => ($("#add-time").hidden = r.value !== "stop" || !r.checked)));
  $("#add-save").onclick = saveChosen;
  $("#alerts-toggle").onchange = async (e) => {
    if (e.target.checked && "Notification" in window && Notification.permission === "default") await Notification.requestPermission();
    store.set("alerts", e.target.checked && "Notification" in window && Notification.permission === "granted"); renderToday();
  };
  $("#gps-toggle").onchange = (e) => {
    state.gps = e.target.checked; store.set("gps", state.gps);
    if (state.gps) startGps(); else { if (watchId != null) navigator.geolocation.clearWatch(watchId); state.me = null; }
    render();
  };
  for (const id of ["hotel-address", "meeting-point", "embassy"]) { const el = $(`#${id}`); el.value = store.get(id, ""); el.oninput = () => store.set(id, el.value); }
  $("#show-taxi").onclick = () => { $("#taxi-text").textContent = $("#hotel-address").value || "Add the hotel address first."; $("#taxi").hidden = false; };
  $("#taxi-close").onclick = () => ($("#taxi").hidden = true);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") load(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#detail").hidden = true; $("#add").hidden = true; $("#taxi").hidden = true; } });
}

window.addEventListener("DOMContentLoaded", () => {
  bind(); bindSheet(); bindPullToRefresh(); initMap();
  if (state.gps) startGps();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  render(); load();
  setInterval(() => { if (document.visibilityState === "visible") load(); }, REFRESH_MS);
  setInterval(renderStatus, 30000);
});
