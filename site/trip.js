/* Paris Pulse — Trip tab: the family itinerary, tick-offs and the pre-trip checklist.
   The itinerary is personal (where the family will be, hour by hour), so it is never part of the
   public site. It is imported on each phone from a file or from a private link and kept in this
   phone's storage, like places and stops. Loaded after app.js and uses its helpers. */
"use strict";

const trip = {
  data: store.get("trip", null),
  link: store.get("trip-link", "") || "",
  done: store.get("trip-done", {}) || {},
  changes: store.get("trip-changes", null),
  tab: "plan", day: null, busy: false, error: "",
};

/* ---------- validation ---------- */
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/, DATE = /^\d{4}-\d{2}-\d{2}$/;
/** Returns a clean copy of an itinerary, or throws with a message a person can act on. */
function checkTrip(raw) {
  if (!raw || typeof raw !== "object") throw new Error("That file isn't an itinerary.");
  if (raw.schema_version !== 1) throw new Error("This itinerary was made for a different version of the app.");
  if (!Array.isArray(raw.days) || !raw.days.length) throw new Error("The itinerary has no days.");
  const ids = new Set();
  const uniq = (id, what) => { if (!id || ids.has(id)) throw new Error(`Each ${what} needs its own id (problem with “${id || "blank"}”).`); ids.add(id); return String(id); };
  const num = (v, lo, hi) => (typeof v === "number" && v >= lo && v <= hi ? v : null);
  const days = raw.days.map((d) => {
    if (!DATE.test(d.date || "")) throw new Error(`Day ${d.day ?? "?"} has no valid date.`);
    return {
      id: uniq(d.id, "day"), day: d.day, date: d.date, title: String(d.title || ""), amended: !!d.amended,
      constraints: Array.isArray(d.constraints) ? d.constraints.map(String) : [],
      stops: (d.stops || []).map((s) => {
        if (!TIME.test(s.time || "")) throw new Error(`“${s.title || s.id}” has no valid time.`);
        const lat = num(s.lat, -90, 90), lon = num(s.lon, -180, 180);
        return { id: uniq(s.id, "stop"), time: s.time, end: TIME.test(s.end || "") ? s.end : "", title: String(s.title || "Stop"),
          type: String(s.type || ""), cost: String(s.cost || ""), ticket: String(s.ticket || ""), notes: String(s.notes || ""),
          maps_query: String(s.maps_query || ""), lat: lat != null && lon != null ? lat : null, lon: lat != null && lon != null ? lon : null };
      }).sort((a, b) => a.time.localeCompare(b.time)),
    };
  }).sort((a, b) => a.date.localeCompare(b.date));
  const prep = (raw.prep || []).map((p) => ({ id: uniq(p.id, "task"), category: String(p.category || "Other"), task: String(p.task || ""), due: DATE.test(p.due || "") ? p.due : "" }));
  const t = raw.trip || {};
  return { schema_version: 1, updated_at: String(raw.updated_at || ""), revision: String(raw.revision || ""),
    trip: { title: String(t.title || "Trip"), start_date: days[0].date, end_date: days[days.length - 1].date,
      hotel: t.hotel && t.hotel.name ? { name: String(t.hotel.name), maps_query: String(t.hotel.maps_query || t.hotel.name) } : null },
    days, prep };
}

/* ---------- what changed between two versions ---------- */
function diffTrip(before, after) {
  if (!before) return null;
  const old = new Map(before.days.flatMap((d) => d.stops.map((s) => [s.id, s]))), out = [];
  for (const d of after.days) {
    const seen = d.stops.filter((s) => old.has(s.id));
    const added = d.stops.length - seen.length;
    const changed = seen.filter((s) => { const o = old.get(s.id); return o.time !== s.time || o.end !== s.end || o.title !== s.title || o.notes !== s.notes; });
    const prevHere = before.days.find((x) => x.id === d.id);
    const removed = prevHere ? prevHere.stops.filter((s) => !d.stops.some((n) => n.id === s.id)).length : 0;
    const bits = [];
    if (changed.length) bits.push(changed.length === 1 ? `${changed[0].title} now ${changed[0].time}` : `${changed.length} stops changed`);
    if (added) bits.push(`${added} added`);
    if (removed) bits.push(`${removed} removed`);
    if (bits.length) out.push(`Day ${d.day}: ${bits.join(", ")}`);
  }
  return out.length ? { at: new Date().toISOString(), lines: out } : null;
}

function saveTrip(raw, source) {
  const clean = checkTrip(raw);
  const changes = diffTrip(trip.data, clean);
  const same = trip.data && JSON.stringify(trip.data.days) === JSON.stringify(clean.days) && JSON.stringify(trip.data.prep) === JSON.stringify(clean.prep);
  trip.data = clean; store.set("trip", clean);
  // Ticks for stops and tasks that no longer exist are dropped; everything else keeps its tick.
  const ids = new Set(clean.days.flatMap((d) => d.stops.map((s) => s.id)).concat(clean.prep.map((p) => p.id)));
  trip.done = Object.fromEntries(Object.entries(trip.done).filter(([k]) => ids.has(k))); store.set("trip-done", trip.done);
  if (changes && !same) { trip.changes = changes; store.set("trip-changes", changes); if (source === "link") notifyTrip(changes); }
  trip.error = "";
  return clean;
}

async function notifyTrip(changes) {
  if (!store.get("alerts", false) || !("Notification" in window) || Notification.permission !== "granted") return;
  const reg = await navigator.serviceWorker?.getRegistration();
  const opts = { body: changes.lines.join("\n"), tag: "pulse-trip", renotify: true, icon: "icons/icon-192.png" };
  try { reg ? reg.showNotification("Itinerary updated", opts) : new Notification("Itinerary updated", opts); } catch { /* ignore */ }
}

/** Fetch the itinerary from the private link, if one is set. Quietly keeps the saved copy when offline. */
async function refreshTrip(loud = false) {
  if (!trip.link || trip.busy) return;
  trip.busy = true; if (loud) renderTrip();
  try {
    const url = trip.link + (trip.link.includes("?") ? "&" : "?") + "t=" + Date.now();
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`The link answered ${res.status}. Check it's still shared.`);
    saveTrip(await res.json(), "link");
  } catch (e) {
    if (loud || !trip.data) trip.error = e instanceof SyntaxError ? "That link doesn't return an itinerary file." : navigator.onLine === false ? "You're offline. Showing the saved copy." : e.message;
  }
  trip.busy = false; render();
}

/* ---------- integration with Today: itinerary stops are safety-checked like your own stops ---------- */
function tripStops() {
  if (!trip.data) return [];
  return trip.data.days.flatMap((d) => d.stops.filter((s) => s.lat != null)
    .map((s) => ({ id: `trip-${s.id}`, name: s.title, lat: s.lat, lon: s.lon, date: d.date, time: s.time, kind: "trip" })));
}

/* ---------- rendering ---------- */
const fShort = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const shortDate = (k) => fShort.format(parisDate(k, "12:00"));
function mapsUrl(s) {
  const q = s.lat != null ? `${s.lat},${s.lon}` : s.maps_query;
  return q ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}` : "";
}
const TYPE_ICON = {
  meal: '<path d="M7 3v8a2 2 0 0 0 2 2v8M11 3v8M7 7h4M17 3c-1.7 0-3 2.2-3 5s1.3 4 3 4v9"/>',
  transport: '<rect x="4" y="5" width="16" height="11" rx="3"/><path d="M4 11h16M7 19l1-3M17 19l-1-3"/>',
  shopping: '<path d="M6 8h12l-1 12H7L6 8z"/><path d="M9 8a3 3 0 0 1 6 0"/>',
  flight: '<path d="M3 13l18-6-6 14-3-6-9-2z"/>',
  hotel: '<path d="M3 20V6M3 14h18v6M21 14v-3a3 3 0 0 0-3-3h-7v6"/><circle cx="7" cy="10" r="2"/>',
  visit: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
};

function pickDay() {
  const days = trip.data.days, today = dayKey(new Date());
  if (trip.day && days.some((d) => d.id === trip.day)) return;
  const d = days.find((x) => x.date === today) || (today < days[0].date ? days[0] : today > days[days.length - 1].date ? days[days.length - 1] : days[0]);
  trip.day = d.id;
}

function stopRow(s, day, evs) {
  const done = !!trip.done[s.id];
  const safety = s.lat != null && state.data ? assessAt(s, evs, parisDate(day.date, s.time)) : null;
  const meta = [s.end ? `until ${s.end}` : "", s.cost === "included" ? "Included" : s.cost === "own" ? "Own cost" : "", s.ticket].filter(Boolean);
  const url = mapsUrl(s);
  return `<div class="tstop${done ? " done" : ""}">
    <label class="tick"><input type="checkbox" data-tick="${esc(s.id)}"${done ? " checked" : ""} aria-label="Done: ${esc(s.title)}"><span></span></label>
    <div class="tstop-main">
      <div class="tstop-head"><span class="tstop-time">${esc(s.time)}</span><span class="tstop-title">${esc(s.title)}</span></div>
      ${meta.length ? `<div class="tstop-meta">${esc(meta.join(" · "))}</div>` : ""}
      ${s.notes ? `<div class="tstop-note">${esc(s.notes)}</div>` : ""}
      ${safety && safety.level !== "green" ? `<div class="place-why ${safety.level}"><span class="dot ${safety.level}"></span> ${esc(safety.why)}</div>` : ""}
    </div>
    ${url ? `<a class="icon-btn" href="${esc(url)}" target="_blank" rel="noopener" aria-label="Open ${esc(s.title)} in Google Maps"><svg viewBox="0 0 24 24">${TYPE_ICON.visit}</svg></a>` : `<span class="tstop-type" aria-hidden="true"><svg viewBox="0 0 24 24">${TYPE_ICON[s.type] || TYPE_ICON.visit}</svg></span>`}
  </div>`;
}

function renderPlan() {
  pickDay();
  const days = trip.data.days, day = days.find((d) => d.id === trip.day), evs = events();
  const count = (d) => d.stops.filter((s) => trip.done[s.id]).length;
  const today = dayKey(new Date());
  let html = `<div class="chips" id="trip-days">${days.map((d) => `<button class="chip" data-tday="${esc(d.id)}" aria-selected="${d.id === day.id}">${d.date === today ? "Today" : `Day ${esc(d.day)}`}<span class="n">${esc(shortDate(d.date))}</span></button>`).join("")}</div>`;
  const n = count(day), pct = day.stops.length ? Math.round((n / day.stops.length) * 100) : 0;
  html += `<div class="card tday">
    <div class="tday-head"><div><h2>${esc(day.title)}</h2><p class="page-sub">${esc(fDay.format(parisDate(day.date, "12:00")))}${day.amended ? ' · <span class="tag">Amended</span>' : ""}</p></div></div>
    <div class="bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div>
    <p class="fine">${n} of ${day.stops.length} done</p>
    ${day.constraints.length ? `<div class="banner amber"><b>Keep in mind</b><span>${esc(day.constraints.join(" · "))}</span></div>` : ""}
    <div class="list">${day.stops.map((s) => stopRow(s, day, evs)).join("") || '<p class="empty">Nothing planned.</p>'}</div>
  </div>`;
  return html;
}

function renderPrep() {
  const items = trip.data.prep, today = dayKey(new Date());
  if (!items.length) return '<p class="empty">This itinerary has no checklist.</p>';
  const left = items.filter((p) => !trip.done[p.id]).length;
  const cats = [...new Set(items.map((p) => p.category))];
  return `<p class="page-sub" style="margin:4px 2px 0">${left ? `${left} of ${items.length} still to do` : "All done"}</p>` + cats.map((c) => `<h2>${esc(c)}</h2><div class="card list">${items.filter((p) => p.category === c).map((p) => {
    const done = !!trip.done[p.id], late = !done && p.due && p.due < today;
    return `<div class="tstop${done ? " done" : ""}"><label class="tick"><input type="checkbox" data-tick="${esc(p.id)}"${done ? " checked" : ""} aria-label="Done: ${esc(p.task)}"><span></span></label>
      <div class="tstop-main"><div class="tstop-title">${esc(p.task)}</div>${p.due ? `<div class="tstop-meta${late ? " late" : ""}">${late ? "Overdue · was due" : "By"} ${esc(shortDate(p.due))}</div>` : ""}</div></div>`;
  }).join("")}</div>`).join("");
}

function renderTrip() {
  const page = $("#trip-body"); if (!page) return;
  const d = trip.data;
  $("#trip-sub").textContent = d ? `${shortDate(d.trip.start_date)} – ${shortDate(d.trip.end_date)}${d.revision ? ` · ${d.revision}` : ""}` : "Your itinerary, on this phone only";
  let html = "";
  if (trip.error) html += `<p class="error">${esc(trip.error)}</p>`;
  if (trip.changes && trip.changes.lines) html +=`<div class="banner blue"><b>Itinerary updated</b>${trip.changes.lines.map((l) => `<span>${esc(l)}</span>`).join("")}<button class="link-btn" id="trip-changes-ok">Got it</button></div>`;
  if (!d) {
    html += `<div class="card"><h2 style="margin-top:4px">Add your itinerary</h2><p class="page-sub">Open the itinerary file, or paste a private link to it so changes reach this phone automatically.</p>${importControls()}</div>`;
  } else {
    html += `<div class="seg trip-seg" role="radiogroup" aria-label="Show">
      <label><input type="radio" name="trip-tab" value="plan"${trip.tab === "plan" ? " checked" : ""}><span>Itinerary</span></label>
      <label><input type="radio" name="trip-tab" value="prep"${trip.tab === "prep" ? " checked" : ""}><span>Prep ${d.prep.filter((p) => trip.done[p.id]).length}/${d.prep.length}</span></label></div>`;
    html += trip.tab === "prep" ? renderPrep() : renderPlan();
    html += `<h2>Itinerary source</h2><div class="card">
      <p class="page-sub" style="margin:0 0 4px">${trip.link ? "Updates from your private link each time the app opens." : "Imported from a file. Add a link to get changes automatically."}${d.updated_at ? ` Version from ${esc(when(d.updated_at))}.` : ""}</p>
      ${importControls()}
      <button class="btn btn-wide" id="trip-clear">Remove itinerary from this phone</button></div>
      <p class="fine">Stops with a location are also checked for demonstrations in Today. Ticks are saved only on this phone.</p>`;
  }
  const left = page.querySelector("#trip-days")?.scrollLeft || trip.scroll || 0;
  page.innerHTML = html;
  const strip = page.querySelector("#trip-days"), sel = strip?.querySelector('[aria-selected="true"]');
  if (strip && sel) {
    // Keep the chosen day in view without jumping the page.
    const want = sel.offsetLeft - strip.offsetLeft - 16;
    strip.scrollLeft = want < left || want + sel.offsetWidth > left + strip.clientWidth ? want : left;
    trip.scroll = strip.scrollLeft;
  }
}

function importControls() {
  return `<input class="search" id="trip-link" type="url" inputmode="url" placeholder="https://gist.githubusercontent.com/…/raw/itinerary.json" value="${esc(trip.link)}" aria-label="Private link to the itinerary" style="margin-top:10px">
    <div class="row-2"><button class="btn btn-primary" id="trip-link-save">${trip.busy ? "Loading…" : trip.link ? "Refresh now" : "Use link"}</button>
    <label class="btn" for="trip-file">Open file</label></div>
    <input type="file" id="trip-file" accept="application/json,.json" hidden>`;
}

/* ---------- events ---------- */
function bindTrip() {
  const page = $("#trip-body");
  page.addEventListener("change", async (e) => {
    const t = e.target;
    if (t.dataset.tick) { trip.done[t.dataset.tick] = t.checked; if (!t.checked) delete trip.done[t.dataset.tick]; store.set("trip-done", trip.done); return renderTrip(); }
    if (t.name === "trip-tab") { trip.tab = t.value; return renderTrip(); }
    if (t.id === "trip-file" && t.files[0]) {
      try { saveTrip(JSON.parse(await t.files[0].text()), "file"); }
      catch (err) { trip.error = err instanceof SyntaxError ? "That file isn't an itinerary (it isn't valid JSON)." : err.message; }
      render();
    }
  });
  page.addEventListener("click", (e) => {
    const d = e.target.closest("[data-tday]"); if (d) { trip.day = d.dataset.tday; return renderTrip(); }
    if (e.target.id === "trip-changes-ok") { trip.changes = null; store.set("trip-changes", null); return renderTrip(); }
    if (e.target.id === "trip-link-save") {
      const v = $("#trip-link").value.trim();
      if (v && !/^https:\/\//i.test(v)) { trip.error = "The link must start with https://"; return renderTrip(); }
      trip.link = v; store.set("trip-link", v); trip.error = "";
      return v ? refreshTrip(true) : renderTrip();
    }
    if (e.target.id === "trip-clear") {
      trip.data = null; trip.done = {}; trip.changes = null; trip.link = ""; trip.day = null;
      for (const k of ["trip", "trip-done", "trip-changes", "trip-link"]) { try { localStorage.removeItem(k); } catch { /* private mode */ } }
      return render();
    }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  bindTrip(); renderTrip(); refreshTrip();
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshTrip(); });
});
