/* Paris Pulse — "lost" card.
   A full-screen card in French to show a police officer, station staff or a shopkeeper if a family
   member gets separated: who they are, who to call (tap a number to call it), the hotel and the
   meeting point, with the same lines in English underneath. Everything is kept on this phone; the
   family numbers can travel with the family setup link, the name can't (each phone is someone's).
   Loaded after app.js and trip.js. */
"use strict";

const PHONE_RE = /\+?\d[\d\s().-]{6,}\d/;

/** "Rassyid (father) +60 12 345 6789" -> { label: "Rassyid (father)", number: "+60 12 345 6789", tel: "+60123456789" } */
function parseContact(line) {
  const m = line.match(PHONE_RE);
  if (!m) return { label: line.trim(), number: "", tel: "" };
  const label = (line.slice(0, m.index) + line.slice(m.index + m[0].length)).replace(/[\s:,–-]+$/, "").replace(/^[\s:,–-]+/, "").trim();
  return { label, number: m[0].trim(), tel: m[0].replace(/[^\d+]/g, "") };
}

function lostCardHtml() {
  const name = store.get("lost-name", "").trim();
  const contacts = store.get("lost-contacts", "").split("\n").map((l) => l.trim()).filter(Boolean).map(parseContact);
  const hotel = store.get("hotel-address", "").trim() || (typeof tripHotelText === "function" && tripHotelText()) || "";
  const meet = store.get("meeting-point", "").trim();
  const section = (fr, en, body) => `<section><h3>${fr} <span>${en}</span></h3>${body}</section>`;
  return `
    <p class="lost-fr">J'ai perdu ma famille.${name ? ` Je m'appelle <b>${esc(name)}</b>.` : ""}</p>
    <p class="lost-fr">Je viens de Malaisie et je ne parle pas français.</p>
    <p class="lost-fr">Pouvez-vous appeler ma famille, s'il vous plaît&nbsp;?</p>
    <p class="lost-en">I've lost my family.${name ? ` My name is ${esc(name)}.` : ""} I'm from Malaysia and don't speak French. Please call my family.</p>
    ${contacts.length ? section("Ma famille", "My family", contacts.map((c) => c.tel
      ? `<a class="lost-call" href="tel:${esc(c.tel)}"><span>${esc(c.label || "Famille")}</span><b>${esc(c.number)}</b></a>`
      : `<p class="lost-line">${esc(c.label)}</p>`).join("")) : '<p class="lost-missing">Add family phone numbers in Help first.</p>'}
    ${hotel ? section("Mon hôtel", "My hotel", `<p class="lost-line">${esc(hotel)}</p>`) : ""}
    ${meet ? section("Point de rendez-vous", "Meeting point", `<p class="lost-line">${esc(meet)}</p>`) : ""}
    <p class="lost-emergency">Urgence&nbsp;: <a href="tel:112">112</a> · Police&nbsp;: <a href="tel:17">17</a></p>`;
}

function showLost() {
  $("#lost-body").innerHTML = lostCardHtml();
  $("#lost").hidden = false;
}

window.addEventListener("DOMContentLoaded", () => {
  for (const id of ["lost-name", "lost-contacts"]) {
    const el = $(`#${id}`); if (!el) continue;
    el.value = store.get(id, "");
    el.addEventListener("input", () => { store.set(id, el.value); if (typeof renderSetupCard === "function") renderSetupCard(); });
  }
  $("#show-lost").onclick = showLost;
  $("#lost-close").onclick = () => ($("#lost").hidden = true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#lost").hidden = true; });
});
