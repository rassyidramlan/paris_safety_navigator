/* Paris Pulse — current temperature and daily forecast, from Open-Meteo (free, no key).
   The phone asks for central Paris plus any itinerary stop more than 15 km away (La Vallée,
   Versailles, CDG), in one request. Results are kept on the phone, so the last forecast still shows
   offline, and refreshed every 30 minutes while the app is open.
   Loaded after app.js and trip.js. */
"use strict";

const OM_URL = "https://api.open-meteo.com/v1/forecast";
const PARIS_CENTRE = { lat: 48.8566, lon: 2.3522, name: "Paris" };
const FAR_KM = 15, REFRESH_MIN = 30;
const wxState = { data: store.get("forecast", null), busy: false };

/* WMO weather codes, as Open-Meteo reports them. */
function wxText(code) {
  const c = Number(code);
  if (c === 0) return "Clear";
  if (c === 1) return "Mostly clear";
  if (c === 2) return "Partly cloudy";
  if (c === 3) return "Cloudy";
  if (c === 45 || c === 48) return "Fog";
  if (c >= 51 && c <= 55) return "Drizzle";
  if (c === 56 || c === 57 || c === 66 || c === 67) return "Freezing rain";
  if (c >= 61 && c <= 65) return c === 65 ? "Heavy rain" : "Rain";
  if (c >= 71 && c <= 77) return "Snow";
  if (c >= 80 && c <= 82) return "Showers";
  if (c === 85 || c === 86) return "Snow showers";
  if (c >= 95) return "Thunderstorm";
  return "";
}
const WX_ICON = {
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  cloud: '<path d="M7 18a4 4 0 0 1-.6-8A5.5 5.5 0 0 1 17 9a3.5 3.5 0 0 1 .5 7z"/>',
  rain: '<path d="M7 15a4 4 0 0 1-.6-8A5.5 5.5 0 0 1 17 6a3.5 3.5 0 0 1 .5 7"/><path d="M8 18l-1 2M12 18l-1 2M16 18l-1 2"/>',
  snow: '<path d="M7 15a4 4 0 0 1-.6-8A5.5 5.5 0 0 1 17 6a3.5 3.5 0 0 1 .5 7"/><path d="M8 19h.01M12 19h.01M16 19h.01M10 21h.01M14 21h.01"/>',
  storm: '<path d="M7 15a4 4 0 0 1-.6-8A5.5 5.5 0 0 1 17 6a3.5 3.5 0 0 1 .5 7"/><path d="M12 13l-2 4h4l-2 4"/>',
  fog: '<path d="M4 10h16M4 14h16M6 18h12M7 6h10"/>',
};
function wxIcon(code) {
  const c = Number(code);
  const k = c <= 1 ? "sun" : c <= 3 ? "cloud" : c <= 48 ? "fog" : (c >= 71 && c <= 77) || c === 85 || c === 86 ? "snow" : c >= 95 ? "storm" : "rain";
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${WX_ICON[k]}</svg>`;
}
const deg = (t) => `${Math.round(t)}°`;

/** Central Paris, plus itinerary stops far from it (rounded, so nearby stops share one forecast). */
function forecastPoints() {
  const pts = [PARIS_CENTRE];
  const seen = new Set(["48.86,2.35"]);
  if (typeof trip !== "undefined" && trip.data) {
    for (const d of trip.data.days) for (const s of d.stops) {
      if (s.lat == null || distance(s, PARIS_CENTRE) < FAR_KM * 1000) continue;
      const key = `${s.lat.toFixed(2)},${s.lon.toFixed(2)}`;
      if (seen.has(key) || pts.length >= 6) continue;
      seen.add(key); pts.push({ lat: +s.lat.toFixed(3), lon: +s.lon.toFixed(3), name: s.title });
    }
  }
  return pts;
}

async function refreshForecast(force = false) {
  if (wxState.busy) return;
  const pts = forecastPoints();
  const key = pts.map((p) => `${p.lat},${p.lon}`).join(";");
  const fresh = wxState.data && wxState.data.key === key && Date.now() - new Date(wxState.data.at).getTime() < REFRESH_MIN * 60e3;
  if (fresh && !force) return;
  wxState.busy = true;
  try {
    const q = new URLSearchParams({
      latitude: pts.map((p) => p.lat).join(","), longitude: pts.map((p) => p.lon).join(","),
      current: "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
      daily: "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,snowfall_sum",
      timezone: "Europe/Paris", forecast_days: "16",
    });
    const res = await fetch(`${OM_URL}?${q}`);
    if (!res.ok) throw new Error(res.status);
    const raw = await res.json();
    const list = Array.isArray(raw) ? raw : [raw];
    wxState.data = {
      at: new Date().toISOString(), key,
      places: list.map((r, i) => ({
        name: pts[i].name, lat: pts[i].lat, lon: pts[i].lon, current: r.current || null,
        daily: (r.daily && r.daily.time || []).map((t, j) => ({
          date: t, code: r.daily.weather_code[j], max: r.daily.temperature_2m_max[j], min: r.daily.temperature_2m_min[j],
          rain: r.daily.precipitation_probability_max ? r.daily.precipitation_probability_max[j] : null,
          snow: r.daily.snowfall_sum ? r.daily.snowfall_sum[j] : null,
        })),
      })),
    };
    store.set("forecast", wxState.data);
  } catch { /* offline or service down: keep the last forecast */ }
  wxState.busy = false;
  renderNowWeather();
  if (typeof renderTrip === "function") renderTrip();
}

/* ---------- rendering ---------- */
function renderNowWeather() {
  const el = $("#status-wx"); if (!el) return;
  const p = wxState.data && wxState.data.places[0], c = p && p.current;
  // Older than 3 hours isn't "now" any more.
  if (!c || Date.now() - new Date(wxState.data.at).getTime() > 3 * 3600e3) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `${wxIcon(c.weather_code)}<b>${esc(deg(c.temperature_2m))}</b><small>${esc(wxText(c.weather_code))}</small>`;
  el.title = `Paris now: ${deg(c.temperature_2m)}, feels like ${deg(c.apparent_temperature)}`;
}

function dayLine(f) {
  const snowy = f.snow > 0.2;
  const bits = [`${deg(f.min)} / ${deg(f.max)}`, snowy ? `Snow, about ${Math.max(1, Math.round(f.snow))} cm` : wxText(f.code)];
  if (!snowy && f.rain != null && f.rain >= 30) bits.push(`rain ${Math.round(f.rain / 10) * 10}%`);
  return bits.filter(Boolean).join(" · ");
}

/** One line per place for a trip day: Paris, plus any far stop on that day. */
function dayForecastHtml(day) {
  if (!wxState.data) return "";
  const lines = [];
  const paris = wxState.data.places[0], pf = paris && paris.daily.find((x) => x.date === day.date);
  const far = wxState.data.places.slice(1).filter((p) => day.stops.some((s) => s.lat != null && Math.abs(s.lat - p.lat) < 0.02 && Math.abs(s.lon - p.lon) < 0.02));
  for (const p of far) {
    const f = p.daily.find((x) => x.date === day.date);
    if (f) lines.push(`<span>${wxIcon(f.code)}<b>${esc(p.name)}</b> ${esc(dayLine(f))}</span>`);
  }
  if (pf) lines.unshift(`<span>${wxIcon(pf.code)}<b>Paris</b> ${esc(dayLine(pf))}</span>`);
  return lines.length ? `<p class="tday-wx">${lines.join("")}</p>` : "";
}

window.addEventListener("DOMContentLoaded", () => {
  renderNowWeather();
  refreshForecast();
  setInterval(() => { if (document.visibilityState === "visible") refreshForecast(); }, 5 * 60e3);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshForecast(); });
});
