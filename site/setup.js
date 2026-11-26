/* Paris Pulse — family setup link.
   One phone shares a link that sets up another: the private itinerary link, always-watched places
   (the hotel), the hotel address for the taxi card, the meeting point and the embassy details.
   Everything rides in the part of the link after "#", which browsers never send to the server,
   so it doesn't pass through GitHub. Treat the link as private: share it in the family chat only.
   Loaded after app.js and trip.js. */
"use strict";

const SETUP_KEY = "setup=";
const TEXT_FIELDS = { hotel: "hotel-address", meet: "meeting-point", embassy: "embassy", lost: "lost-contacts" };

function b64url(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = ""; bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function unb64url(code) {
  const bin = atob(code.replace(/-/g, "+").replace(/_/g, "/"));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

/** What this phone would hand to another one. */
function setupPayload() {
  const out = { v: 1 };
  if (trip.link) out.trip = trip.link;
  const places = state.places.map((p) => ({ n: p.name, a: p.lat, o: p.lon }));
  if (places.length) out.places = places;
  for (const [k, id] of Object.entries(TEXT_FIELDS)) { const v = store.get(id, ""); if (v) out[k] = v; }
  return out;
}

/** Checks a received payload; returns a clean copy or null. Nothing in it is trusted as markup. */
function checkSetup(raw) {
  if (!raw || raw.v !== 1) return null;
  const ok = (n, lo, hi) => typeof n === "number" && n >= lo && n <= hi;
  const out = {};
  if (typeof raw.trip === "string" && /^https:\/\/\S+$/i.test(raw.trip) && raw.trip.length < 400) out.trip = raw.trip;
  if (Array.isArray(raw.places)) {
    out.places = raw.places.slice(0, 10).filter((p) => p && typeof p.n === "string" && ok(p.a, -90, 90) && ok(p.o, -180, 180))
      .map((p) => ({ name: p.n.slice(0, 40), lat: +p.a.toFixed(5), lon: +p.o.toFixed(5) }));
    if (!out.places.length) delete out.places;
  }
  for (const k of Object.keys(TEXT_FIELDS)) if (typeof raw[k] === "string" && raw[k].trim()) out[k] = raw[k].slice(0, 500);
  return Object.keys(out).length ? out : null;
}

function readSetupFromUrl() {
  const h = location.hash.slice(1);
  if (!h.startsWith(SETUP_KEY)) return null;
  try { return checkSetup(JSON.parse(unb64url(h.slice(SETUP_KEY.length)))); } catch { return null; }
}
function clearSetupFromUrl() { try { history.replaceState(null, "", location.pathname + location.search); } catch { /* ignore */ } }

function describeSetup(s) {
  const items = [];
  if (s.trip) items.push("Your family itinerary, with automatic updates");
  if (s.places) items.push(`Always watch: ${s.places.map((p) => p.name).join(", ")}`);
  if (s.hotel) items.push("Hotel address for the taxi card");
  if (s.meet) items.push("Family meeting point");
  if (s.embassy) items.push("Embassy details");
  if (s.lost) items.push("Family phone numbers for the lost card");
  items.push("Alerts on this phone (you'll be asked to allow them)");
  return items;
}

function showSetupOffer(s) {
  $("#setup-list").innerHTML = describeSetup(s).map((t) => `<li>${esc(t)}</li>`).join("");
  $("#setup").hidden = false;
  $("#setup-apply").onclick = async () => {
    if (s.places) {
      for (const p of s.places) {
        if (!state.places.some((x) => distance(x, p) < 100)) state.places.push({ id: `${Date.now()}-${state.places.length}`, ...p });
      }
      store.set("places", state.places);
    }
    for (const [k, id] of Object.entries(TEXT_FIELDS)) if (s[k]) { store.set(id, s[k]); const el = $(`#${id}`); if (el) el.value = s[k]; }
    if (s.trip) { trip.link = s.trip; store.set("trip-link", s.trip); }
    // The tap on this button lets the browser ask for notification permission.
    if ("Notification" in window) {
      if (Notification.permission === "default") { try { await Notification.requestPermission(); } catch { /* ignore */ } }
      store.set("alerts", Notification.permission === "granted");
    }
    $("#setup").hidden = true; clearSetupFromUrl();
    if (s.trip) refreshTrip(true);
    if (s.trip) { trip.tab = "plan"; switchView("trip"); } else openPlaces();
    render();
  };
  $("#setup-skip").onclick = () => { $("#setup").hidden = true; clearSetupFromUrl(); };
}

async function shareSetup() {
  const p = setupPayload(), note = $("#setup-share-note");
  if (Object.keys(p).length === 1) { note.textContent = "Nothing to share yet. Add your itinerary link and hotel first."; return; }
  const url = `${location.origin}${location.pathname}#${SETUP_KEY}${b64url(JSON.stringify(p))}`;
  const text = "Open this on your phone to set up Paris Pulse for our trip. Keep it in the family chat only.";
  try {
    if (navigator.share) { await navigator.share({ title: "Paris Pulse setup", text, url }); note.textContent = "Shared."; return; }
  } catch (e) { if (e && e.name === "AbortError") return; }
  try { await navigator.clipboard.writeText(url); note.textContent = "Link copied. Paste it in the family chat only."; }
  catch { note.textContent = url; }
}

function renderSetupCard() {
  const p = setupPayload(), bits = [];
  if (p.trip) bits.push("itinerary link"); if (p.places) bits.push(`${p.places.length} place${p.places.length > 1 ? "s" : ""}`);
  if (p.hotel) bits.push("hotel address"); if (p.meet) bits.push("meeting point"); if (p.embassy) bits.push("embassy"); if (p.lost) bits.push("family numbers");
  $("#setup-share-what").textContent = bits.length ? `Shares: ${bits.join(", ")}.` : "Add your itinerary link and hotel first.";
}

window.addEventListener("DOMContentLoaded", () => {
  $("#setup-share").onclick = shareSetup;
  renderSetupCard();
  for (const id of Object.values(TEXT_FIELDS)) $(`#${id}`)?.addEventListener("input", renderSetupCard);
  const s = readSetupFromUrl();
  if (s) showSetupOffer(s); else if (location.hash.startsWith(`#${SETUP_KEY}`)) clearSetupFromUrl();
  window.addEventListener("hashchange", () => { const x = readSetupFromUrl(); if (x) showSetupOffer(x); });
});
