/* Paris Pulse — pickpocket hotspots.
   A fixed list taken from official travel advice, not live data. It never changes a colour:
   places and stops inside a hotspot get a "keep bags in front" note, and the map can show the areas.
   Sources:
   - US State Department advice (as republished by CountryReports): Eiffel Tower lifts, museum
     escalators such as the Louvre's, the area around Sacré-Cœur, and the RER B from CDG.
   - UK FCDO travel advice: the Métro, RER lines and mainline stations.
   Loaded after app.js. */
"use strict";

const PICK_SOURCES = {
  us: { name: "US travel advice", url: "https://www.countryreports.org/travel/france/crimes.htm" },
  uk: { name: "UK FCDO travel advice", url: "https://www.gov.uk/foreign-travel-advice/france/safety-and-security" },
};
const HOTSPOTS = [
  { id: "eiffel", name: "Eiffel Tower", lat: 48.8584, lon: 2.2945, r: 350, why: "Crowded lifts and queues", src: "us" },
  { id: "louvre", name: "Louvre", lat: 48.8610, lon: 2.3358, r: 350, why: "Crowded escalators and entrances", src: "us" },
  { id: "sacrecoeur", name: "Sacré-Cœur and Montmartre", lat: 48.8867, lon: 2.3431, r: 400, why: "Pickpockets and snatch thefts around the basilica", src: "us" },
  { id: "chatelet", name: "Châtelet–Les Halles", lat: 48.8620, lon: 2.3470, r: 300, why: "Busiest Métro and RER hub", src: "uk" },
  { id: "gdnord", name: "Gare du Nord", lat: 48.8809, lon: 2.3553, r: 250, why: "Mainline station, end of the RER B from CDG", src: "uk" },
  { id: "gdest", name: "Gare de l'Est", lat: 48.8768, lon: 2.3592, r: 200, why: "Mainline station", src: "uk" },
  { id: "gdlyon", name: "Gare de Lyon", lat: 48.8443, lon: 2.3744, r: 250, why: "Mainline station", src: "uk" },
  { id: "stlazare", name: "Gare Saint-Lazare", lat: 48.8763, lon: 2.3254, r: 250, why: "Mainline station", src: "uk" },
  { id: "montparnasse", name: "Gare Montparnasse", lat: 48.8412, lon: 2.3209, r: 250, why: "Mainline station", src: "uk" },
  { id: "austerlitz", name: "Gare d'Austerlitz", lat: 48.8420, lon: 2.3655, r: 200, why: "Mainline station", src: "uk" },
];
const PICK_COLOR = "#7048e8";

/** The hotspot a point is in, as a short note; "" if none. */
function hotspotNote(point) {
  const h = HOTSPOTS.find((x) => distance(point, x) <= x.r + 100);
  return h ? `Pickpocket hotspot: ${h.name}. Keep bags zipped and in front.` : "";
}

function hotspotGeo() {
  const features = HOTSPOTS.map((h) => {
    const ring = [];
    for (let i = 0; i <= 40; i++) {
      const a = (i / 40) * 2 * Math.PI;
      ring.push([h.lon + (h.r / (111320 * Math.cos((h.lat * Math.PI) / 180))) * Math.cos(a), h.lat + (h.r / 110540) * Math.sin(a)]);
    }
    return { type: "Feature", properties: { id: h.id }, geometry: { type: "Polygon", coordinates: [ring] } };
  });
  return { type: "FeatureCollection", features };
}

/** Called from addLayers() whenever the map style loads. */
function addHotspotLayers() {
  map.addSource("hotspots", { type: "geojson", data: hotspotGeo() });
  const vis = state.layers.pick ? "visible" : "none";
  map.addLayer({ id: "hotspots-fill", type: "fill", source: "hotspots", layout: { visibility: vis }, paint: { "fill-color": PICK_COLOR, "fill-opacity": 0.12 } });
  map.addLayer({ id: "hotspots-line", type: "line", source: "hotspots", layout: { visibility: vis },
    paint: { "line-color": PICK_COLOR, "line-width": 1.5, "line-opacity": 0.6, "line-dasharray": [2, 2] } });
}

function showHotspots() {
  if (!map || !map.getLayer("hotspots-fill")) return;
  const vis = state.layers.pick ? "visible" : "none";
  map.setLayoutProperty("hotspots-fill", "visibility", vis); map.setLayoutProperty("hotspots-line", "visibility", vis);
}

function hotspotPopup(id, lngLat) {
  const h = HOTSPOTS.find((x) => x.id === id); if (!h) return;
  const s = PICK_SOURCES[h.src];
  new maplibregl.Popup({ offset: 6, maxWidth: "260px" }).setLngLat(lngLat).setHTML(
    `<div class="tpop"><p class="tpop-time">Pickpocket hotspot</p><p class="tpop-title">${esc(h.name)}</p>
     <p class="tpop-note">${esc(h.why)}. Keep bags zipped and in front, phones out of back pockets.</p>
     <p class="fine" style="margin-top:6px">Source: <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a></p></div>`).addTo(map);
  map.easeTo({ center: lngLat, offset: [0, 110], duration: 350 });   // clear the status card and chips
}
