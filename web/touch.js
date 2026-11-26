// Mode tactile : interrupteur dans le coin bas gauche et clavier à l'écran pour toute l'application.
// En mode tactile, toucher un champ de saisie ouvre le clavier adapté : pavé numérique pour un nombre,
// pavé HHMM pour une heure, AZERTY pour un texte. Le champ reste visible au-dessus du clavier, surligné.
// Le choix est mémorisé sur le poste ; il est activé d'office sur un écran tactile. La taille des éléments ne change pas.
const KEY = "mes-tactile";
const FIELDS = "input:not([type]), input[type=text], input[type=search], input[type=password], input[type=email], input[type=number], input[type=time], textarea";
let touch = false, kb = null;   // kb : { el, kind: "num" | "time" | "text", val, label, caps, digits }

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const sw = document.createElement("button");
sw.type = "button";
sw.className = "touchsw";
sw.setAttribute("role", "switch");
sw.innerHTML = `<span class="sw" aria-hidden="true"></span>Mode tactile`;
const pad = document.createElement("div");
pad.id = "kbd";
pad.hidden = true;
pad.setAttribute("role", "dialog");
pad.setAttribute("aria-label", "Clavier");
document.body.append(sw, pad);

function setTouch(on) {
  touch = on;
  document.documentElement.classList.toggle("touch", on);
  sw.setAttribute("aria-checked", String(on));
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* stockage indisponible */ }
  if (!on) close();
}

function kindOf(el) { return el.type === "time" ? "time" : el.type === "number" ? "num" : "text"; }
function labelOf(el) {
  const l = el.getAttribute("aria-label") || (el.closest("label") && el.closest("label").textContent.trim()) || el.placeholder;
  return l || "Saisie";
}

function open(el) {
  const kind = kindOf(el);
  kb = { el, kind, val: kind === "time" ? "" : String(el.value || ""), label: labelOf(el), caps: false, digits: false };
  document.querySelectorAll(".kbd-target").forEach(x => x.classList.remove("kbd-target"));
  el.classList.add("kbd-target");
  document.body.classList.add("kbd-open");
  draw();
  setTimeout(() => el.scrollIntoView({ block: "center", behavior: "smooth" }), 30);
}

function close() {
  if (kb) kb.el.classList.remove("kbd-target");
  kb = null;
  document.body.classList.remove("kbd-open");
  pad.hidden = true;
  pad.innerHTML = "";
}

function shown() {
  if (kb.kind === "time") { const d = kb.val.padEnd(4, "–"); return `${d.slice(0, 2)}:${d.slice(2, 4)}`; }
  return kb.el.type === "password" ? "•".repeat(kb.val.length) : kb.val;
}

function draw() {
  pad.hidden = false;
  const key = (v, l, cls = "") => `<button type="button" class="key ${cls}" data-k="${esc(v)}">${l ?? esc(v)}</button>`;
  let keys;
  if (kb.kind !== "text") {
    keys = `<div class="kpad">${[..."789456123"].map(c => key(c)).join("")}${kb.kind === "num" ? key(",", ",") : key("back", "⌫")}${key("0")}${kb.kind === "num" ? key("back", "⌫") : key("ok", "OK", "ok")}</div>`;
    if (kb.kind === "num") keys += `<div class="kpad">${key("ok", "OK", "ok wide3")}</div>`;
  } else if (kb.digits) {
    keys = `<div class="krow">${[..."1234567890"].map(c => key(c)).join("")}</div>
      <div class="krow">${[..."/:.,()+-°%@"].map(c => key(c)).join("")}${key("back", "⌫", "wide")}</div>
      <div class="krow">${key("abc", "ABC", "wide")}${key(" ", "Espace", "space")}${key("ok", "OK", "wide ok")}</div>`;
  } else {
    const rows = ["azertyuiop", "qsdfghjklm", "wxcvbn'é"].map(r => [...r].map(c => key(kb.caps ? c.toUpperCase() : c)).join(""));
    keys = `<div class="krow">${rows[0]}</div><div class="krow">${rows[1]}</div>
      <div class="krow">${key("caps", "⇧", "wide" + (kb.caps ? " on" : ""))}${rows[2]}${key("back", "⌫", "wide")}</div>
      <div class="krow">${key("123", "123", "wide")}${key(" ", "Espace", "space")}${key("ok", "OK", "wide ok")}</div>`;
  }
  const v = shown();
  pad.innerHTML = `<div class="kbd-in"><div class="kbd-head"><span class="kl">${esc(kb.label)}</span>
    <span class="kv num">${v ? esc(v) : "<i>vide</i>"}</span>
    ${kb.kind === "time" ? `<span class="kh">HH MM</span>` : ""}
    <button type="button" class="key wide" data-k="cancel">Annuler</button></div>${keys}</div>`;
}

function flash() {
  const h = pad.querySelector(".kv");
  if (h) { h.classList.add("bad"); setTimeout(() => h.classList.remove("bad"), 500); }
}

function commit() {
  const el = kb.el;
  let v = kb.val;
  if (kb.kind === "time") {
    if (v.length < 3) return flash();
    v = v.padStart(4, "0");
    if (+v.slice(0, 2) > 23 || +v.slice(2) > 59) return flash();
    v = `${v.slice(0, 2)}:${v.slice(2)}`;
  }
  if (kb.kind === "num") v = v.replace(",", ".");
  close();
  if (!el.isConnected) return;
  // Preact suit la valeur par l'événement « input » (et « change » pour les champs qui l'écoutent)
  const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set;
  set.call(el, v);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

pad.addEventListener("pointerdown", e => e.preventDefault());   // garde le champ en place
pad.addEventListener("click", e => {
  const b = e.target.closest("[data-k]");
  if (!b || !kb) return;
  if (!kb.el.isConnected) return close();
  const k = b.dataset.k;
  if (k === "cancel") return close();
  if (k === "ok") return commit();
  if (k === "back") kb.val = kb.val.slice(0, -1);
  else if (k === "caps") kb.caps = !kb.caps;
  else if (k === "123") kb.digits = true;
  else if (k === "abc") kb.digits = false;
  else if (kb.kind === "time") { if (kb.val.length < 4) kb.val += k; }
  else if (kb.kind === "num") {
    if (k === ",") { if (!kb.val.includes(",")) kb.val = (kb.val || "0") + ","; }
    else if (kb.val.length < 9) kb.val = (kb.val === "0" ? "" : kb.val) + k;
  }
  else { kb.val += k; if (kb.caps) kb.caps = false; }
  draw();
});

document.addEventListener("pointerdown", e => {
  if (!touch || e.target.closest("#kbd")) return;
  const el = e.target.closest(FIELDS);
  if (el && !el.disabled && !el.readOnly) { e.preventDefault(); el.blur(); open(el); }
}, true);
// un champ atteint autrement (tabulation, autofocus) n'ouvre pas le clavier du système
document.addEventListener("focusin", e => { if (touch && e.target.matches && e.target.matches(FIELDS)) { e.target.setAttribute("inputmode", "none"); } });
document.addEventListener("keydown", e => { if (kb && e.key === "Escape") close(); });
sw.addEventListener("click", () => setTouch(!touch));

let start = false;
try {
  const v = localStorage.getItem(KEY);
  start = v === "1" || (v === null && matchMedia("(pointer: coarse)").matches);
} catch (e) { /* stockage indisponible */ }
setTouch(start);
