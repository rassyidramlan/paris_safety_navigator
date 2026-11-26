/* Paris Pulse — alerts when the app is closed (Web Push).
   How it fits together (no server of our own):
   - The organiser creates a key pair once (Help > Closed-app alerts). The private half goes into a
     GitHub Actions secret; the public half rides in the family setup link to every phone.
   - Each phone turns alerts on under Trip > Places. The browser gives it a push address, which the
     phone shows as a code (with its owner's name and always-watched places). The owner sends the
     code to the organiser, who adds it as a line in push-devices.txt in the family's secret gist.
   - Every 10 minutes the collector (push.py) checks the hotel, places and itinerary stops and sends
     red alerts to every registered phone. The service worker shows them even with the app closed.
   Loaded after app.js, trip.js and setup.js. */
"use strict";

const PUSH_PREFIX = "PP1.";
const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

function b64urlBytes(bytes) {
  let bin = ""; new Uint8Array(bytes).forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function bytesFromB64url(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

/* ---------- organiser: create the key pair ---------- */
async function createPushKeys() {
  const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const jwk = await crypto.subtle.exportKey("jwk", kp.privateKey);
  const pub = new Uint8Array(65); pub[0] = 4; pub.set(bytesFromB64url(jwk.x), 1); pub.set(bytesFromB64url(jwk.y), 33);
  const admin = { pk: b64urlBytes(pub), sk: jwk.d, at: new Date().toISOString() };
  store.set("push-admin", admin); store.set("push-key", admin.pk);
  return admin;
}

function renderPushAdmin() {
  const box = $("#push-admin-body"); if (!box) return;
  const a = store.get("push-admin", null);
  box.innerHTML = a
    ? `<p class="fine" style="margin:0 0 6px">Keys created ${esc(when(a.at))}. Copy the private key into GitHub as the secret <b>PUSH_VAPID_PRIVATE_KEY</b>.</p>
       <textarea readonly rows="2" id="push-sk">${esc(a.sk)}</textarea>
       <div class="row-2"><button class="btn" id="push-sk-copy">Copy private key</button><button class="btn" id="push-new-keys">Create new keys</button></div>
       <p class="fine">Then share the family setup link again (Help → Family setup) so every phone gets the matching public key.</p>`
    : `<button class="btn btn-wide" id="push-create">Create alert keys</button>`;
}

/* ---------- each phone: turn on, show the code ---------- */
function deviceCode(sub) {
  const name = (store.get("lost-name", "") || "").trim() || "A phone";
  const places = state.places.map((p) => [p.name, +p.lat.toFixed(5), +p.lon.toFixed(5)]);
  return PUSH_PREFIX + b64url(JSON.stringify({ n: name.slice(0, 30), s: sub.toJSON(), p: places }));
}

async function currentSubscription() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.pushManager.getSubscription() : null;
}

async function turnOnPush() {
  const msg = $("#push-status"), key = store.get("push-key", "");
  try {
    if (Notification.permission === "default") await Notification.requestPermission();
    if (Notification.permission !== "granted") { msg.textContent = "Notifications are blocked for this app in the phone's settings. Allow them, then try again."; return; }
    const reg = await navigator.serviceWorker.ready;
    const old = await reg.pushManager.getSubscription();
    if (old && store.get("push-key-used", "") !== key) await old.unsubscribe();   // the organiser made new keys
    const sub = (old && store.get("push-key-used", "") === key) ? old
      : await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytesFromB64url(key) });
    store.set("push-key-used", key); store.set("push-code", deviceCode(sub)); store.set("alerts", true);
  } catch (e) {
    msg.textContent = `Couldn't turn on alerts here (${e && e.name ? e.name : "error"}). Check your connection and try again.`;
    return;
  }
  renderPushCard();
}

async function renderPushCard() {
  const msg = $("#push-status"), on = $("#push-on"), box = $("#push-code-box"); if (!msg) return;
  on.hidden = true; box.hidden = true;
  if (!pushSupported()) {
    msg.textContent = isIOS() && !isStandalone()
      ? "On iPhone: tap Share → Add to Home Screen, open Paris Pulse from the Home Screen, then come back here."
      : "This browser can't receive alerts while it's closed. Try Chrome.";
    return;
  }
  if (isIOS() && !isStandalone()) { msg.textContent = "On iPhone: tap Share → Add to Home Screen, open Paris Pulse from the Home Screen, then come back here."; return; }
  const key = store.get("push-key", "");
  if (!key) { msg.textContent = "Waiting for the organiser's family setup link, which carries the alert key."; return; }
  const sub = await currentSubscription();
  if (sub && store.get("push-key-used", "") === key && Notification.permission === "granted") {
    const code = deviceCode(sub); store.set("push-code", code);
    msg.textContent = "On for this phone. Send this code to the organiser once. You'll get a \"Closed-app alerts are on\" message within about 10 minutes of it being added.";
    $("#push-code").value = code; box.hidden = false;
  } else {
    msg.textContent = "Red alerts arrive even when Paris Pulse is closed: a demonstration near your hotel, places or the next stops, or a red weather warning.";
    on.hidden = false;
  }
}

async function sharePushCode() {
  const code = $("#push-code").value, note = $("#push-code-note");
  const text = `Paris Pulse alert code (${(store.get("lost-name", "") || "my phone").trim()}):\n${code}`;
  try { if (navigator.share) { await navigator.share({ title: "Paris Pulse alert code", text }); return; } } catch (e) { if (e && e.name === "AbortError") return; }
  try { await navigator.clipboard.writeText(code); note.textContent = "Copied. Send it to the organiser."; } catch { $("#push-code").select(); }
}

/* ---------- opening the app from an alert: #ev=<id> shows that event ---------- */
function openEventFromHash(tries = 0) {
  const m = location.hash.match(/^#ev=(.+)$/); if (!m) return;
  const id = decodeURIComponent(m[1]);
  if (!state.data) { if (tries < 40) setTimeout(() => openEventFromHash(tries + 1), 250); return; }
  const ev = events().find((x) => x.id === id);
  try { history.replaceState(null, "", location.pathname + location.search); } catch { /* ignore */ }
  switchView("map"); if (ev) showDetail(ev);
}

window.addEventListener("DOMContentLoaded", () => {
  $("#push-on").onclick = turnOnPush;
  $("#push-share").onclick = sharePushCode;
  $("#push-copy").onclick = async () => { try { await navigator.clipboard.writeText($("#push-code").value); $("#push-code-note").textContent = "Copied."; } catch { $("#push-code").select(); } };
  $("#push-admin-body").addEventListener("click", async (e) => {
    if (e.target.id === "push-create" || (e.target.id === "push-new-keys" && confirm("New keys mean every phone has to turn alerts on again. Continue?"))) { await createPushKeys(); renderPushAdmin(); renderPushCard(); if (typeof renderSetupCard === "function") renderSetupCard(); }
    if (e.target.id === "push-sk-copy") { try { await navigator.clipboard.writeText($("#push-sk").value); e.target.textContent = "Copied"; } catch { $("#push-sk").select(); } }
  });
  renderPushAdmin(); renderPushCard();
  openEventFromHash();
  window.addEventListener("hashchange", () => openEventFromHash());
  navigator.serviceWorker?.addEventListener("message", (e) => { if (e.data && e.data.ev) { location.hash = `ev=${encodeURIComponent(e.data.ev)}`; } });
});
