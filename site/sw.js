/* Service worker: keeps the app shell and the last data.json available offline. */
const SHELL = "pulse-shell-v3";
const DATA = "pulse-data";
const SHELL_FILES = ["./", "index.html", "style.css", "app.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png",
  "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css", "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== DATA).map((k) => caches.delete(k))))
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
  if (url.hostname.endsWith("openfreemap.org") || url.hostname.endsWith("photon.komoot.io")) return;   // map tiles and search: straight to the network
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

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((list) => (list[0] ? list[0].focus() : self.clients.openWindow("./"))));
});
