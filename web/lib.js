// Outils partagés de l'interface : accès API, formats, composants communs, thème clair/sombre.
import { html, useState, useEffect, useCallback } from "./vendor/preact-htm.module.js";

// ----------------------------------------------------------------------------- outils
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch("/api" + path, init);
  if (r.status === 401 && path !== "/session") { window.dispatchEvent(new Event("mes-logout")); throw new Error("Session expirée"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || r.statusText);
  return data;
}

function useApi(path, deps = [], refreshMs = 0) {
  const [state, set] = useState({ data: null, error: null, loading: true });
  const reload = useCallback(() => {
    if (!path) return;
    api(path).then(d => set({ data: d, error: null, loading: false }), e => set(s => ({ ...s, error: e.message, loading: false })));
  }, [path]);
  useEffect(() => {
    reload();
    if (!refreshMs) return;
    const t = setInterval(reload, refreshMs);
    return () => clearInterval(t);
  }, [path, ...deps]);
  return { ...state, reload };
}

const fmtDT = new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" });
const fmtT = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" });
const dt = v => (v ? fmtDT.format(new Date(v)) : "");
const hm = v => (v ? fmtT.format(new Date(v)) : "");
const num = (v, d = 1) => (v === null || v === undefined || v === "" ? "–" : Number(v).toLocaleString("fr-FR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = v => (v === null || v === undefined ? "–" : num(v * 100, 1) + " %");
function dur(s) {
  if (s === null || s === undefined) return "–";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${String(m).padStart(2, "0")}` : m ? `${m} min ${String(s % 60).padStart(2, "0")}` : `${s} s`;
}
const toLocalInput = d => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 16); };
const fromLocalInput = v => new Date(v).toISOString();

const ROLE_LABEL = { operateur: "Opérateur", chef_equipe: "Chef d'équipe", maintenance: "Maintenance", admin: "Administrateur" };
const can = (user, ...roles) => user.role === "admin" || roles.includes(user.role);

function Msg({ error, ok }) {
  if (error) return html`<div class="err">${error}</div>`;
  if (ok) return html`<div class="ok">${ok}</div>`;
  return null;
}

function Dialog({ title, onClose, children }) {
  return html`<div class="dialog-back" onClick=${e => e.target === e.currentTarget && onClose()}>
    <div class="dialog" role="dialog" aria-label=${title}><h3>${title}</h3>${children}</div></div>`;
}

// action asynchrone avec gestion d'erreur et de message
function useAction() {
  const [msg, setMsg] = useState({});
  const [busy, setBusy] = useState(false);
  const run = async (fn, okText) => {
    setBusy(true); setMsg({});
    try { const r = await fn(); if (okText) setMsg({ ok: typeof okText === "function" ? okText(r) : okText }); return r; }
    catch (e) { setMsg({ error: e.message }); return undefined; }
    finally { setBusy(false); }
  };
  return { msg, busy, run, setMsg };
}

// ----------------------------------------------------------------------------- courbe simple en SVG
function Chart({ series, height = 220, yLabel = "", bands = [] }) {
  const W = 800, H = height, P = { l: 48, r: 12, t: 10, b: 24 };
  const pts = series.flatMap(s => s.points);
  if (!pts.length) return html`<p class="muted">Pas de données sur la période.</p>`;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]).concat(bands.map(b => b.y));
  let x0 = Math.min(...xs), x1 = Math.max(...xs); if (x1 === x0) x1 = x0 + 1;
  let y0 = Math.min(...ys), y1 = Math.max(...ys); if (y1 === y0) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const X = x => P.l + ((x - x0) / (x1 - x0)) * (W - P.l - P.r);
  const Y = y => P.t + (1 - (y - y0) / (y1 - y0)) * (H - P.t - P.b);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => y0 + f * (y1 - y0));
  const xt = [0, 0.5, 1].map(f => x0 + f * (x1 - x0));
  return html`<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style=${{ height: H + "px" }}>
    ${ticks.map(t => html`<line class="axis" x1=${P.l} x2=${W - P.r} y1=${Y(t)} y2=${Y(t)} /><text x=${P.l - 6} y=${Y(t) + 4} text-anchor="end">${num(t, Math.abs(y1 - y0) < 10 ? 1 : 0)}</text>`)}
    ${xt.map((t, i) => html`<text x=${X(t)} y=${H - 6} text-anchor=${["start", "middle", "end"][i]}>${dt(t)}</text>`)}
    ${bands.map(b => html`<line x1=${P.l} x2=${W - P.r} y1=${Y(b.y)} y2=${Y(b.y)} stroke=${b.color} stroke-dasharray="4 4" />`)}
    ${series.map(s => html`<polyline fill="none" stroke=${s.color || "var(--primary)"} stroke-width="2" vector-effect="non-scaling-stroke"
        points=${s.points.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ")} />
      ${s.points.length < 60 && s.points.map(p => html`<circle cx=${X(p[0])} cy=${Y(p[1])} r="3.5" fill=${s.color || "var(--primary)"} />`)}`)}
    ${yLabel && html`<text x=${P.l} y=${P.t + 10}>${yLabel}</text>`}
  </svg>`;
}

// ----------------------------------------------------------------------------- thème clair / sombre
const THEME_KEY = "mes-theme";
function initialTheme() {
  try { const t = localStorage.getItem(THEME_KEY); if (t === "clair" || t === "sombre") return t; } catch (e) { /* stockage indisponible */ }
  return matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "sombre" : "clair";
}
let theme = initialTheme();
const themeListeners = new Set();
function applyTheme() { document.documentElement.dataset.theme = theme === "sombre" ? "dark" : "light"; }
applyTheme();
export function setTheme(t) {
  theme = t; applyTheme();
  try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* stockage indisponible */ }
  themeListeners.forEach(f => f(t));
}
export function useTheme() {
  const [t, set] = useState(theme);
  useEffect(() => { themeListeners.add(set); return () => themeListeners.delete(set); }, []);
  return t;
}

const SUN = "M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z";
const MOON = "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z";
export function ThemeToggle() {
  const t = useTheme();
  return html`<div class="seg" role="group" aria-label="Thème">
    <button type="button" class=${t === "clair" ? "on" : ""} aria-pressed=${t === "clair"} onClick=${() => setTheme("clair")}><${Icon} d=${SUN} size="14" />Clair</button>
    <button type="button" class=${t === "sombre" ? "on" : ""} aria-pressed=${t === "sombre"} onClick=${() => setTheme("sombre")}><${Icon} d=${MOON} size="14" />Sombre</button>
  </div>`;
}

// ----------------------------------------------------------------------------- composants de la charte
export function Icon({ d, size = 16 }) {
  return html`<svg aria-hidden="true" width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d=${d} /></svg>`;
}
export const ICON = {
  expand: "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7",
  close: "M18 6 6 18M6 6l12 12",
  back: "M15 18l-6-6 6-6",
  next: "M9 18l6-6-6-6",
  cut: "M6 3v18M18 3v18M6 12h12",
};

// Bandeau marine en tête de vue : sur-titre, titre (un mot en vert), contenu à droite
export function Band({ over, title, accent, children, back }) {
  return html`<header class="band">
    <div class="band-title">
      ${back}
      <div><p class="over">${over}</p><h1>${title} ${accent && html`<span class="accent">${accent}</span>`}</h1></div>
    </div>
    <div class="band-tools">${children}<${ThemeToggle} /></div>
  </header>`;
}

// Tuile agrandissable : agrandie, elle recouvre toute la grille (la vue garde la hauteur de l'écran)
export function Tile({ title, id, open, onToggle, col, row, children, detail, extra, cls = "" }) {
  const isOpen = open === id;
  return html`<article class=${`tile ${cls} ${isOpen ? "open" : ""}`} style=${isOpen ? {} : { gridColumn: col, gridRow: row }}>
    <div class="th"><h2>${title}</h2><span class="spacer"></span>${extra}
      ${onToggle && html`<button type="button" class="xb" onClick=${() => onToggle(isOpen ? null : id)} aria-label=${isOpen ? "Réduire " + title : "Agrandir " + title} title=${isOpen ? "Réduire" : "Agrandir"}>
        <${Icon} d=${isOpen ? ICON.close : ICON.expand} /></button>`}</div>
    ${isOpen && detail ? detail : children}
  </article>`;
}

export function Pill({ kind = "idle", children }) {
  return html`<span class=${"pill " + kind}><span class="dot"></span>${children}</span>`;
}

// état d'une ligne à partir de la dernière valeur de marche
export function lineState(l) {
  if (l.en_marche === null || l.en_marche === undefined) return { kind: "idle", label: "Inconnu" };
  if (l.en_marche < 0.5) return { kind: "bad", label: "À l'arrêt" };
  const nominal = l.cadence_nominale_th;
  if (nominal && l.debit !== null && l.debit !== undefined && l.debit < 0.7 * nominal) return { kind: "warn", label: "Ralenti" };
  return { kind: "good", label: "En marche" };
}
// couleur d'un TRS (seuils usuels)
export const oeeKind = v => (v === null || v === undefined ? "idle" : v >= 0.75 ? "good" : v >= 0.6 ? "warn" : "bad");

export const fmtDay = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });
export const fmtDayShort = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short" });
export function nowLabel() {
  const d = new Date();
  const s = fmtDay.format(d);
  return s.charAt(0).toUpperCase() + s.slice(1) + " · " + hm(d);
}

// ----------------------------------------------------------------------------- frise d'un poste
// Fond vert (marche), ralentis en orange, arrêts en rouge (hachurés s'ils sont à qualifier, gris s'ils sont planifiés).
const T = v => new Date(v).getTime();
export function stopClass(s) { return s.planned ? "planned" : s.cause_id ? "" : "todo"; }
export function Frise({ start, end, stops = [], slows = [], small = false, sel = null, onPick = null, kind = "production" }) {
  const a = T(start), b = T(end), now = Date.now(), span = Math.max(1, b - a);
  const x = t => Math.min(100, Math.max(0, ((T(t) - a) / span) * 100));
  const nowX = x(now);
  const runW = Math.min(100, nowX);
  const planned = kind !== "production";
  return html`<div class=${"frise" + (small ? " small" : "")} role=${onPick ? "group" : "img"} aria-label="Frise du poste">
    <div class=${planned ? "seg-stop planned" : "seg-run"} style=${{ left: 0, width: runW + "%" }}></div>
    ${slows.map(r => html`<div class="seg-slow" style=${{ left: x(r.starts_at) + "%", width: Math.max(0.3, x(r.ends_at) - x(r.starts_at)) + "%" }}></div>`)}
    ${stops.map(s => {
      const l = x(s.starts_at), w = Math.max(x(s.ends_at || now) - l, 0.4);
      const label = `Arrêt ${hm(s.starts_at)}, ${dur(s.duree_s)}${s.cause ? " · " + s.cause : s.planned ? " · planifié" : " · à qualifier"}`;
      return onPick
        ? html`<button type="button" class=${`seg-stop ${stopClass(s)} ${sel === s.id ? "sel" : ""}`} title=${label} aria-label=${label}
            style=${{ left: l + "%", width: `max(${w}%, 14px)` }} onClick=${() => onPick(s)}></button>`
        : html`<div class=${`seg-stop ${stopClass(s)}`} title=${label} style=${{ left: l + "%", width: w + "%" }}></div>`;
    })}
    ${nowX > 0 && nowX < 100 && html`<div class="future" style=${{ left: nowX + "%" }}></div><div class="now" style=${{ left: nowX + "%" }}></div>`}
  </div>`;
}
export function Ticks({ start, end, every = 1 }) {
  const a = T(start), b = T(end), out = [];
  for (let t = a; t <= b + 1; t += every * 3600e3) out.push(t);
  return html`<div class="ticks">${out.map(t => html`<span style=${{ left: ((t - a) / (b - a)) * 100 + "%" }}>${hm(t)}</span>`)}</div>`;
}

// barres verticales simples (séries horaires) ; libellés en HTML pour rester lisibles quelle que soit la taille
export function Bars({ data, height = 120, color = "var(--primary)", target = null, unit = "", labelEvery = 3, slots = 0 }) {
  if (!data.length) return html`<p class="empty">Pas de données.</p>`;
  const n = Math.max(data.length, slots);
  const W = 600, H = height, P = { t: 6, b: 2 };
  const max = Math.max(1, ...data.map(d => d.v || 0), target || 0, ...data.map(d => d.target || 0)) * 1.1;
  const bw = W / n;
  const Y = v => P.t + (1 - v / max) * (H - P.t - P.b);
  return html`<div style="display:flex;flex-direction:column;height:100%;min-height:0">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;flex:1;min-height:0;display:block" role="img" aria-label=${"Histogramme " + unit}>
      ${data.map((d, i) => html`<g>
        <rect x=${i * bw + bw * 0.15} y=${Y(d.v || 0)} width=${bw * 0.7} height=${Math.max(0, H - P.b - Y(d.v || 0))}
          style=${{ fill: d.color || color }}><title>${d.label} : ${num(d.v, 0)} ${unit}</title></rect>
        ${d.target ? html`<line x1=${i * bw} x2=${(i + 1) * bw} y1=${Y(d.target)} y2=${Y(d.target)} style=${{ stroke: "var(--text)" }} stroke-dasharray="4 3" stroke-width="1.5" vector-effect="non-scaling-stroke" />` : ""}
      </g>`)}
      ${target ? html`<line x1="0" x2=${W} y1=${Y(target)} y2=${Y(target)} style=${{ stroke: "var(--text)" }} stroke-dasharray="4 3" stroke-width="1.5" vector-effect="non-scaling-stroke" />` : ""}
    </svg>
    <div style=${{ display: "grid", gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))`, fontSize: "11px", color: "var(--text2)", marginTop: "3px" }}>
      ${data.map((d, i) => html`<span style="text-align:center;white-space:nowrap;overflow:visible" class="num">${i % labelEvery === 0 ? d.label : ""}</span>`)}
    </div>
  </div>`;
}

// anneau de pourcentage (TRS)
export function Ring({ value, size = 96, label = "" }) {
  const r = 40, c = 2 * Math.PI * r, v = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value));
  const col = { good: "var(--green)", warn: "var(--orange)", bad: "var(--red)", idle: "var(--muted)" }[oeeKind(value)];
  return html`<svg width=${size} height=${size} viewBox="0 0 100 100" role="img" aria-label=${label + " " + pct(value)}>
    <circle cx="50" cy="50" r=${r} fill="none" style=${{ stroke: "var(--fill)" }} stroke-width="11" />
    <circle cx="50" cy="50" r=${r} fill="none" style=${{ stroke: col }} stroke-width="11" stroke-linecap="round"
      stroke-dasharray=${`${c * v} ${c}`} transform="rotate(-90 50 50)" />
    <text x="50" y="56" text-anchor="middle" font-size="20" font-weight="800" style=${{ fill: "var(--text)" }}>${value === null || value === undefined ? "–" : Math.round(v * 100) + "%"}</text>
  </svg>`;
}

// message bref en bas d'écran
export function useToast() {
  const [t, set] = useState(null);
  useEffect(() => { if (!t) return; const h = setTimeout(() => set(null), 3500); return () => clearTimeout(h); }, [t]);
  const show = (text, error = false) => set({ text, error, at: Date.now() });
  const node = t && html`<div class=${"toast" + (t.error ? " err" : "")} role="status">${t.text}</div>`;
  return [node, show];
}

export { api, useApi, useAction, Msg, Dialog, Chart, dt, hm, num, pct, dur, toLocalInput, fromLocalInput, ROLE_LABEL, can };
