/* Service worker: keeps the app shell and the last data.json available offline. */
const SHELL = "pulse-shell-v10";
const DATA = "pulse-data";
const MAP = "pulse-map";       // map tiles, fonts and icons (filled by "Save the map for offline" and by browsing)
const MAP_MAX = 3000;          // entries; oldest are dropped beyond this
const SHELL_FILES = ["./", "index.html", "style.css", "app.js", "trip.js", "setup.js", "offline.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png",
  "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css", "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== DATA && k !== MAP).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname.endsWith("/data.json")) {
    // Network first; fall back to the last copy so the app still shows something offline.
    e.respondWith(fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(DATA).then((c) => c.put("data.json", copy));
      return res;
    }).catch(() => caches.open(DATA).then((c) => c.match("data.json")).then((r) => r || Response.error())));
    return;
  }
  if (url.hostname.endsWith("photon.komoot.io")) return;   // place search: straight to the network
  if (url.hostname.endsWith("openfreemap.org")) {
    // Tiles, fonts and icons never change at a given address: use the saved copy, else fetch and save it.
    // Styles and tile indexes can change: fetch first, fall back to the saved copy offline.
    const fixed = !url.pathname.startsWith("/styles/") && /\.(pbf|png|json)$/.test(url.pathname);
    e.respondWith(caches.open(MAP).then(async (c) => {
      const saved = await c.match(e.request.url);
      if (saved && fixed) return saved;
      try {
        const res = await fetch(e.request);
        if (res.ok) { c.put(e.request.url, res.clone()).then(() => trimMap(c)); }
        return res;
      } catch (err) { if (saved) return saved; throw err; }
    }));
    return;
  }
  if (url.origin === location.origin) {
    // The app's own files: network first so updates appear immediately; cache when offline.
    e.respondWith(fetch(e.request).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("index.html"))));
    return;
  }
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
    if (res.ok && (url.origin === location.origin || url.hostname === "unpkg.com" || url.hostname.endsWith("gstatic.com") || url.hostname.endsWith("googleapis.com"))) {
      const copy = res.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy));
    }
    return res;
  })));
});

let trimming = false;
async function trimMap(c) {
  if (trimming) return; trimming = true;
  try { const keys = await c.keys(); for (const k of keys.slice(0, Math.max(0, keys.length - MAP_MAX))) await c.delete(k); } finally { trimming = false; }
}

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((list) => (list[0] ? list[0].focus() : self.clients.openWindow("./"))));
});
