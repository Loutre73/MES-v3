// Console d'exploitation MES v3 : architecture vivante, services et commandes, simulation, journal.
import { html, render, useState, useEffect, useCallback, useRef } from "./vendor/preact-htm.module.js";

// ----------------------------------------------------------------------------- outils
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch("api/" + path, init);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (r.status === 401 && path !== "session") window.dispatchEvent(new Event("ops-logout"));
  if (!r.ok) throw new Error((data && data.detail) || r.statusText);
  return data;
}

function usePoll(path, ms = 5000, deps = []) {
  const [s, set] = useState({ data: null, error: null });
  const reload = useCallback(() => {
    if (!path) return Promise.resolve();
    return api(path).then(d => set({ data: d, error: null }), e => set(x => ({ ...x, error: e.message })));
  }, [path]);
  useEffect(() => {
    reload();
    if (!ms) return;
    const t = setInterval(() => { if (!document.hidden) reload(); }, ms);
    return () => clearInterval(t);
  }, [path, ...deps]);
  return { ...s, reload };
}

const nf = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("fr-FR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const bytes = b => {
  if (b === null || b === undefined) return "–";
  const u = ["o", "Ko", "Mo", "Go", "To"]; let i = 0; let v = b;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return nf(v, v < 10 && i > 1 ? 1 : 0) + " " + u[i];
};
const ago = ts => (ts ? secs(Date.now() / 1000 - ts) : "–");
function secs(s) {
  s = Math.max(0, s);
  if (s < 90) return Math.round(s) + " s";
  if (s < 5400) return Math.round(s / 60) + " min";
  if (s < 172800) { const h = Math.floor(s / 3600); return h + " h " + String(Math.round((s % 3600) / 60)).padStart(2, "0"); }
  return Math.round(s / 86400) + " j";
}
// Intervalle PostgreSQL lisible : "09:00:00" -> "9 h", "1 year" -> "1 an"
function interval(v) {
  if (!v) return "–";
  const m = /^(\d+):(\d+):(\d+)$/.exec(v);
  if (m) { const h = +m[1], mi = +m[2]; return (h ? h + " h" : "") + (mi ? " " + mi + " min" : "") || v; }
  return v.replace("years", "ans").replace("year", "an").replace("mons", "mois").replace("mon", "mois").replace("days", "j").replace("day", "j");
}
const hm = iso => (iso ? new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : "–");
const dhm = iso => (iso ? new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–");
const G = "#06D16F", O = "#FF9500", R = "#FF3B30", N = "#8A96A6";

function svcState(s) {
  if (!s) return { cls: "neutral", dot: N, label: "Absent", st: "off" };
  if (s.en_marche && s.sante === "unhealthy") return { cls: "bad", dot: R, label: "En défaut", st: "bad" };
  if (s.en_marche && s.sante === "starting") return { cls: "warn", dot: O, label: "Démarrage", st: "warn" };
  if (s.en_marche) return { cls: "ok", dot: G, label: "En service", st: "ok" };
  if (s.couche === "task" && s.code_sortie === 0) return { cls: "neutral", dot: N, label: "Terminé", st: "off" };
  return { cls: "bad", dot: R, label: s.code_sortie ? "Arrêté (code " + s.code_sortie + ")" : "Arrêté", st: "bad" };
}

function spark(values, w = 110, h = 22) {
  const v = (values || []).filter(x => x !== null);
  if (v.length < 2) return "";
  const max = Math.max(5, ...v);
  return v.map((x, i) => (i * w / (v.length - 1)).toFixed(1) + "," + (h - 2 - x / max * (h - 4)).toFixed(1)).join(" ");
}

// ----------------------------------------------------------------------------- composants communs
function Switch({ on, label, onChange, disabled }) {
  return html`<button type="button" class="sw" role="switch" aria-checked=${on ? "true" : "false"} aria-label=${label}
    disabled=${disabled} onClick=${() => onChange(!on)}><i></i></button>`;
}

function Pill({ cls, dot, children }) {
  return html`<span class=${"pill " + cls}>${dot && html`<span class="dot" style=${{ background: dot }}></span>`}${children}</span>`;
}

// Confirmation et notifications partagées
let confirmFn = null, toastFn = null;
const confirmAsk = (text, action, label = "Confirmer") => confirmFn && confirmFn({ text, action, label });
const toast = (text, kind = "ok") => toastFn && toastFn({ text, kind });

async function run(fn, okText) {
  try { const r = await fn(); if (okText) toast(okText); return r; } catch (e) { toast(e.message, "bad"); throw e; }
}

function Confirm() {
  const [c, set] = useState(null);
  confirmFn = set;
  if (!c) return null;
  return html`<div role="alertdialog" class="banner bad">
    <span>${c.text}</span>
    <button type="button" class="btn" onClick=${() => set(null)}>Annuler</button>
    <button type="button" class="btn stop" onClick=${() => { set(null); c.action(); }}>${c.label}</button>
  </div>`;
}

function Toast() {
  const [t, set] = useState(null);
  toastFn = set;
  useEffect(() => { if (!t) return; const h = setTimeout(() => set(null), t.kind === "bad" ? 8000 : 3500); return () => clearTimeout(h); }, [t]);
  return t ? html`<div class=${"toast " + t.kind} role="status">${t.text}</div>` : null;
}

function LogsDialog({ service, onClose }) {
  const ref = useRef(null);
  const { data, error, reload } = usePoll(service ? `services/${service}/journaux?lignes=300` : null, 5000, [service]);
  const box = useRef(null);
  useEffect(() => { if (service && ref.current && !ref.current.open) ref.current.showModal(); }, [service]);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [data]);
  if (!service) return null;
  return html`<dialog ref=${ref} onClose=${onClose} aria-label=${"Journaux de " + service}>
    <div class="dlg">
      <div class="tt"><h2>Journaux · ${service}</h2>
        <div class="row"><button type="button" class="btn sm" onClick=${reload}>Actualiser</button>
        <button type="button" class="btn sm" onClick=${() => ref.current.close()}>Fermer</button></div></div>
      ${error && html`<div class="banner bad">${error}</div>`}
      <div class="logs" ref=${box}>${data ? data.lignes.join("\n") : "Chargement…"}</div>
    </div></dialog>`;
}

// ----------------------------------------------------------------------------- en-tête, navigation
const PAGES = [
  ["architecture", "Architecture"],
  ["services", "Services et commandes"],
  ["simulation", "Simulation"],
  ["journal", "Journal"],
];

function Side({ page, etat, user, onLogout }) {
  const svc = name => (etat?.services || []).find(s => s.service === name);
  const appUp = ["api", "web"].every(n => svc(n)?.en_marche);
  const dots = { architecture: G, services: appUp ? G : R, simulation: svc("simulator")?.en_marche ? "#82A1CD" : N, journal: "#A9BDD6" };
  const mesUrl = `${location.protocol}//${location.hostname}${location.port && location.port !== (etat?.port_console || "8090") ? ":" + location.port : ""}/`;
  return html`<nav class="side" aria-label="Console d'exploitation">
    <div class="brand"><div class="logo"><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#000B44" stroke-width="2.2" stroke-linecap="round"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M6 18l2-2M16 8l2-2"/><circle cx="12" cy="12" r="3"/></svg></div>MES · Exploitation</div>
    <p class="host">${location.hostname} · version ${etat?.version || "…"}</p>
    ${PAGES.map(([id, label]) => html`<a class=${"nv" + (page === id ? " on" : "")} href=${"#/" + id} aria-current=${page === id ? "page" : undefined}>
      <span class="dot" style=${{ background: dots[id] }}></span>${label}</a>`)}
    <a class="nv" href=${mesUrl + "systeme"} target="_blank" rel="noopener"><span class="dot" style=${{ background: "#A9BDD6" }}></span>Supervision Grafana ↗</a>
    <div class="foot">
      <span>Connecté : ${user.display_name || user.username} (admin)</span>
      <a href=${mesUrl} target="_blank" rel="noopener">Ouvrir l'application MES ↗</a>
      <button type="button" onClick=${onLogout}>Déconnexion</button>
    </div>
  </nav>`;
}

function Header({ over, title, accent, etat, hist, theme, setTheme, reload }) {
  const svc = name => (etat?.services || []).find(s => s.service === name);
  const appUp = ["api", "web"].every(n => svc(n)?.en_marche);
  const viaWeb = location.pathname.startsWith("/exploitation");
  const port = etat?.port_console || "8090";
  const stop = () => confirmAsk(
    "Arrêter l'application coupe l'accès au MES (écrans, API, Grafana) pour tous les utilisateurs. La base et le collecteur restent en service : aucune donnée n'est perdue."
      + (viaWeb ? ` La console restera joignable sur http://${location.hostname}:${port}/` : ""),
    () => run(() => api("application/stop", { method: "POST" }), "Application arrêtée").then(reload), "Arrêter l'application");
  const g = hist?.global;
  return html`<header class="head">
    <div><p class="over">${over}</p><h1>${title} ${accent && html`<span>${accent}</span>`}</h1></div>
    <div class="acts">
      <${Pill} cls="onband" dot=${appUp ? G : R}>${appUp ? "Application en service" : "Application arrêtée"}<//>
      ${appUp
        ? html`<button type="button" class="btn band" onClick=${stop}>Arrêter</button>`
        : html`<button type="button" class="btn go" onClick=${() => run(() => api("application/start", { method: "POST" }), "Application démarrée").then(reload)}>Démarrer</button>`}
      <${Pill} cls="onband" dot=${g ? O : G}>Historisation ${g ? "suspendue" : "active" + (hist ? " · " + nf(hist.points_s) + " pts/s" : "")}<//>
      <div class="seg" role="group" aria-label="Thème">
        <button type="button" aria-pressed=${theme === "light" ? "true" : "false"} onClick=${() => setTheme("light")}>Clair</button>
        <button type="button" aria-pressed=${theme === "dark" ? "true" : "false"} onClick=${() => setTheme("dark")}>Sombre</button>
      </div>
    </div>
  </header>`;
}

// ----------------------------------------------------------------------------- architecture vivante
// Positions en unités du schéma (1000 × 600) : x, y du coin haut gauche ; cartes de 150 × 84.
const W = 150, H = 84;
const NODES = {
  source: [30, 60], collector: [235, 60], db: [440, 60], api: [645, 60], web: [850, 60],
  prometheus: [235, 280], grafana: [440, 280], ops: [645, 280], users: [850, 280],
  exporters: [235, 480], sim_alt: [30, 280],
};
const mid = (n, side) => {
  const [x, y] = NODES[n];
  return { r: [x + W, y + H / 2], l: [x, y + H / 2], t: [x + W / 2, y], b: [x + W / 2, y + H] }[side];
};

function Edge({ from, to, label, dashed, active }) {
  const [x1, y1] = from, [x2, y2] = to;
  const d = x1 === x2 || y1 === y2 ? `M${x1},${y1} L${x2},${y2}` : `M${x1},${y1} L${x1},${(y1 + y2) / 2} L${x2},${(y1 + y2) / 2} L${x2},${y2}`;
  return html`<path d=${d} fill="none" stroke=${active === false ? "var(--muted)" : "var(--edge)"} stroke-width="2"
    stroke-dasharray=${dashed ? "6 6" : undefined} marker-end="url(#head)" vector-effect="non-scaling-stroke"/>`;
}

function Architecture({ etat, hist, reload, openLogs }) {
  const [sel, setSel] = useState("collector");
  const svcs = etat?.services || [];
  const svc = n => svcs.find(s => s.service === n);
  const c = etat?.collecte, col = etat?.collecteur;
  const simOn = !!svc("simulator");
  const exporters = svcs.filter(s => s.couche === "mon" && s.service !== "prometheus");
  const expUp = exporters.filter(s => s.en_marche).length;
  const stOf = n => svcState(svc(n)).st;
  const collectorSt = !svc("collector")?.en_marche ? "bad" : (col && !col.opcua) || hist?.global ? "warn" : "ok";
  const nodes = [
    ["source", simOn ? "Simulateur S7-1500" : "Automate S7-1500", simOn ? "simulator · OPC UA :4840" : etat?.opcua || "OPC UA", simOn ? stOf("simulator") : (col?.opcua ? "ok" : "bad"), simOn ? "simulator" : null],
    ["collector", "Collecteur", col ? `${nf(col.tags)} tags · OPC UA ${col.opcua ? "connecté" : "déconnecté"}` : "métriques indisponibles", collectorSt, "collector"],
    ["db", "TimescaleDB", hist?.global ? "historisation suspendue" : `${nf(c?.points_s)} pts/s écrits`, hist?.global ? "warn" : stOf("db"), "db"],
    ["api", "API MES", "écrans, événements, KPI", stOf("api"), "api"],
    ["web", "Interface web", "nginx · port 80", stOf("web"), "web"],
    ["prometheus", "Prometheus", svc("prometheus") ? "métriques techniques" : "profil monitoring absent", svc("prometheus") ? stOf("prometheus") : "off", svc("prometheus") ? "prometheus" : null],
    ["grafana", "Grafana", "tableaux de bord", stOf("grafana"), "grafana"],
    ["ops", "Console d'exploitation", "vous êtes ici", stOf("ops"), "ops"],
    ["users", "Utilisateurs", "navigateur", "ok", null],
    ["exporters", "Exporteurs", exporters.length ? `${expUp} / ${exporters.length} en service` : "profil monitoring absent", exporters.length ? (expUp === exporters.length ? "ok" : "warn") : "off", null],
  ];
  if (simOn) nodes.push(["sim_alt", "Automate réel", "non raccordé (mode démonstration)", "off", null]);
  const pos = n => ({ left: NODES[n][0] / 10 + "%", top: NODES[n][1] / 6 + "%" });
  const lbl = (x, y, t) => html`<span class="elabel" style=${{ left: x / 10 + "%", top: y / 6 + "%" }}>${t}</span>`;
  const selNode = nodes.find(n => n[0] === sel) || nodes[1];
  return html`<div class="cols">
    <section class="tile" aria-label="Schéma de l'architecture" style=${{ flex: "999 1 620px" }}>
      <div class="tt"><h2>Architecture du MES · temps réel</h2>
        <span class="sub">trait plein : données métier · pointillés : métriques et administration</span></div>
      <div class="archi">
        <svg class="edges" viewBox="0 0 1000 600" preserveAspectRatio="none" aria-hidden="true">
          <defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--edge)"/></marker></defs>
          <${Edge} from=${mid("source", "r")} to=${mid("collector", "l")}/>
          <${Edge} from=${mid("collector", "r")} to=${mid("db", "l")} active=${!hist?.global}/>
          <${Edge} from=${mid("db", "r")} to=${mid("api", "l")}/>
          <${Edge} from=${mid("api", "r")} to=${mid("web", "l")}/>
          <${Edge} from=${mid("users", "t")} to=${mid("web", "b")}/>
          <${Edge} from=${mid("db", "b")} to=${mid("grafana", "t")}/>
          <${Edge} from=${mid("collector", "b")} to=${mid("prometheus", "t")} dashed/>
          <${Edge} from=${mid("prometheus", "r")} to=${mid("grafana", "l")} dashed/>
          <${Edge} from=${mid("exporters", "t")} to=${mid("prometheus", "b")} dashed/>
          <${Edge} from=${mid("ops", "t")} to=${mid("api", "b")} dashed/>
          ${simOn && html`<${Edge} from=${mid("sim_alt", "t")} to=${mid("source", "b")} dashed active=${false}/>`}
        </svg>
        ${lbl(207, 82, "OPC UA")}
        ${lbl(412, 82, hist?.global ? "suspendu" : nf(c?.points_s) + " pts/s")}
        ${lbl(617, 82, "SQL")}
        ${lbl(822, 82, "HTTP")}
        ${lbl(925, 210, ":80")}
        ${lbl(515, 210, "lecture")}
        ${lbl(310, 210, "/metrics")}
        ${lbl(410, 310, "PromQL")}
        ${lbl(310, 420, "toutes les 10 s")}
        ${lbl(720, 210, "Docker")}
        ${nodes.map(([id, title, sub, st]) => html`<button type="button" class=${"node st-" + st + (sel === id ? " sel" : "")} style=${pos(id)}
            onClick=${() => setSel(id)} aria-pressed=${sel === id ? "true" : "false"}>
          <b>${title}</b><span class="sub">${sub}</span>
          <span class="small" style=${{ display: "flex", alignItems: "center", gap: "6px", color: "var(--text2)" }}>
            <span class="dot" style=${{ background: { ok: G, warn: O, bad: R, off: N }[st] }}></span>${{ ok: "en service", warn: "à surveiller", bad: "arrêté / en défaut", off: "inactif" }[st]}</span>
        </button>`)}
      </div>
      <div class="row sub small">
        <span><span class="dot" style=${{ background: G }}></span> en service</span>
        <span><span class="dot" style=${{ background: O }}></span> à surveiller</span>
        <span><span class="dot" style=${{ background: R }}></span> arrêté ou en défaut</span>
        <span>· tâche ponctuelle migrate : ${svcState(svc("migrate")).label.toLowerCase()} ${svc("migrate")?.depuis ? "il y a " + ago(svc("migrate").depuis) : ""}</span>
      </div>
    </section>
    <${NodePanel} node=${selNode} svc=${selNode[4] ? svc(selNode[4]) : null} etat=${etat} hist=${hist} reload=${reload} openLogs=${openLogs} exporters=${exporters}/>
  </div>`;
}

function ServiceActions({ s, reload, openLogs, nowrap }) {
  if (!s) return null;
  const st = svcState(s);
  const act = a => run(() => api(`services/${s.service}/${a}`, { method: "POST" }),
    { start: "démarré", stop: "arrêté", restart: "redémarré" }[a] && `${s.service} ${{ start: "démarré", stop: "arrêté", restart: "redémarré" }[a]}`).then(reload);
  return html`<div class="row" style=${nowrap ? { flexWrap: "nowrap" } : {}}>
    ${s.action_possible && html`<button type="button" class="btn sm" onClick=${() => act("restart")}>Redémarrer</button>`}
    ${s.arret_possible && (s.en_marche
      ? html`<button type="button" class="btn sm" onClick=${() => confirmAsk(`Arrêter ${s.service} (${s.role}) ?`, () => act("stop"), "Arrêter")}>Arrêter</button>`
      : html`<button type="button" class="btn sm go" onClick=${() => act("start")}>Démarrer</button>`)}
    <button type="button" class="btn sm" onClick=${() => openLogs(s.service)}>Journaux</button>
    ${st.st === "off" && null}
  </div>`;
}

function NodePanel({ node, svc, etat, hist, reload, openLogs, exporters }) {
  const [id, title] = node;
  const col = etat?.collecteur, c = etat?.collecte;
  const st = svcState(svc);
  const { data: stock } = usePoll(id === "db" ? "stockage" : null, 30000, [id]);
  const { data: simu } = usePoll(id === "source" && svc ? "simulation" : null, 5000, [id]);
  const kv = (k, v) => html`<div class="kv"><span>${k}</span><b class="num">${v}</b></div>`;
  return html`<aside class="tile panel" aria-label=${"Détails : " + title}>
    <div class="tt"><h2>${title}</h2>${svc && html`<${Pill} cls=${st.cls} dot=${st.dot}>${st.label}<//>`}</div>
    ${svc && html`<div class="mono sub">${svc.image} · ${svc.container}</div>`}
    <div>
      ${svc && kv(svc.en_marche ? "En service depuis" : "Arrêté depuis", ago(svc.depuis))}
      ${svc && svc.en_marche && kv("CPU · mémoire", `${nf(svc.cpu, 1)} % · ${bytes(svc.memoire)}`)}
      ${svc && svc.redemarrages > 0 && kv("Redémarrages automatiques", svc.redemarrages)}
      ${id === "collector" && col && html`
        ${kv("Session OPC UA", col.opcua ? "connectée" : "déconnectée")}
        ${kv("Point de connexion", etat.opcua || "–")}
        ${kv("Tags surveillés · abonnements", `${nf(col.tags)} · ${nf(col.abonnements)}`)}
        ${kv("Écrits par seconde (1 min)", nf(c?.points_s))}
        ${kv("Retard de collecte", c?.retard_s !== null && c?.retard_s !== undefined ? nf(c.retard_s, 1) + " s" : "–")}
        ${kv("File d'écriture · lots en tampon disque", `${nf(col.file)} · ${nf(col.tampon)}`)}
        ${kv("Valeurs non écrites (suspension)", nf(col.suspendus))}`}
      ${id === "db" && stock && html`
        ${kv("Historian brut", bytes(stock.historian_brut))}
        ${kv("Base complète", bytes(stock.base))}
        ${kv("Disque Docker libre", `${bytes(stock.disque.libre)} sur ${bytes(stock.disque.total)}`)}
        ${stock.purge && kv("Prochaine purge (conservation " + interval(stock.purge.conservation) + ")", `${hm(stock.purge.prochaine)} · ≈ ${bytes(stock.purge.octets)}`)}`}
      ${id === "source" && simu && html`
        ${kv("Tags simulés", nf(simu.dbs.reduce((a, d) => a + d.tags, 0)))}
        ${kv("Valeurs modifiées / s", nf(simu.dbs.reduce((a, d) => a + d.valeurs_s, 0)))}
        ${kv("Événements en cours", simu.evenements.length)}
        ${kv("Période · accélération", `${nf(simu.regles.global.update_interval_s, 1)} s · ×${nf(simu.regles.global.time_acceleration)}`)}`}
      ${id === "exporters" && exporters.map(s => kv(s.service, svcState(s).label))}
      ${id === "users" && html`<p class="sub">Accès à l'application MES par le port 80 (nginx), qui sert aussi Grafana sous /grafana/ et cette console sous /exploitation/.</p>`}
      ${id === "sim_alt" && html`<p class="sub">Pour raccorder l'automate réel : renseigner OPCUA_ENDPOINT dans site.env, retirer le profil sim de COMPOSE_PROFILES, puis ./mesctl update.</p>`}
      ${id === "ops" && html`<p class="sub">Pilote les conteneurs du projet par l'API Docker. Ne peut pas s'arrêter elle-même ; joignable aussi sur le port ${etat?.port_console}.</p>`}
    </div>
    ${id === "collector" && html`<div class="row" style=${{ justifyContent: "space-between" }}>
      <span><b>Historisation</b><br/><span class="sub small">${hist?.global ? "suspendue" + (hist.global.jusqu_a ? " jusqu'à " + hm(hist.global.jusqu_a) : "") : "écriture dans l'historian active"}</span></span>
      <${Switch} on=${!hist?.global} label="Historisation" onChange=${on => setHist("global", on, null, reload)}/></div>`}
    ${id === "source" && svc && html`<a class="btn sm" href="#/simulation" style=${{ textDecoration: "none", alignSelf: "flex-start" }}>Règles et événements de simulation</a>`}
    <${ServiceActions} s=${svc} reload=${reload} openLogs=${openLogs}/>
    ${svc && html`<${LastLogs} service=${svc.service}/>`}
  </aside>`;
}

function LastLogs({ service }) {
  const { data } = usePoll(`services/${service}/journaux?lignes=12`, 10000, [service]);
  return html`<div class="logs" style=${{ maxHeight: "200px" }}>${data ? data.lignes.slice(-12).join("\n") : "…"}</div>`;
}

function setHist(portee, actif, minutes, reload) {
  return run(() => api("historisation", { method: "PUT", body: { portee, actif, minutes } }),
    actif ? "Historisation reprise" : "Historisation suspendue").then(reload);
}

// ----------------------------------------------------------------------------- services et commandes
function Services({ etat, hist, reload, openLogs }) {
  const { data: stock } = usePoll("stockage", 30000);
  const { data: journal, reload: reloadJ } = usePoll("journal?limite=15", 15000);
  const svcs = etat?.services || [];
  const app = ["api", "web", "grafana"].map(n => svcs.find(s => s.service === n)).filter(Boolean);
  const appUp = app.length && app.every(s => s.en_marche);
  const appAct = (a, txt) => run(() => api("application/" + a, { method: "POST" }), txt).then(() => { reload(); reloadJ(); });
  const g = hist?.global;
  const disk = stock && (() => {
    const t = stock.disque.total, used = t - stock.disque.libre;
    const parts = [["Historian brut", stock.historian_brut, "#2F5D8A"], ["Reste de la base", Math.max(0, stock.base - stock.historian_brut), "#82A1CD"],
      ["Autres (images, Prometheus…)", Math.max(0, used - stock.base), "var(--muted)"], ["Libre", stock.disque.libre, "#06D16F"]];
    return { pct: used / t, parts: parts.map(([label, v, c]) => ({ label, v, c, w: (v / t * 100).toFixed(1) + "%" })) };
  })();
  const running = svcs.filter(s => s.en_marche).length;
  return html`
    <div class="grid3">
      <section class="tile" aria-label="Application">
        <div class="tt"><h2>Application MES</h2><${Pill} cls=${appUp ? "ok" : "bad"} dot=${appUp ? G : R}>${appUp ? "En service" : "Arrêtée"}<//></div>
        <p class="sub" style=${{ margin: 0 }}>Démarre ou arrête l'application (API, interface web, Grafana). La base, le collecteur et cette console restent en service : aucune donnée n'est perdue.</p>
        <div class="row">
          <button type="button" class="btn go" disabled=${appUp} onClick=${() => appAct("start", "Application démarrée")}>Démarrer</button>
          <button type="button" class="btn" onClick=${() => appAct("restart", "Application redémarrée")}>Redémarrer</button>
          <button type="button" class="btn stop" disabled=${!appUp} onClick=${() => confirmAsk("Arrêter l'application coupe l'accès au MES pour tous les utilisateurs. La collecte continue.", () => appAct("stop", "Application arrêtée"), "Arrêter l'application")}>Arrêter</button>
        </div>
      </section>
      <section class="tile" aria-label="Historisation">
        <div class="tt"><h2>Historisation</h2><${Switch} on=${!g} label="Historisation" onChange=${on => setHist("global", on, null, reload)}/></div>
        <div class="row" style=${{ alignItems: "baseline" }}><span class="big num" style=${{ color: g ? "var(--orange-fg)" : "inherit" }}>${g ? "Suspendue" : nf(hist?.points_s) + " pts/s"}</span>
          <span class="sub">${g ? `depuis ${hm(g.depuis)} par ${g.par}${g.jusqu_a ? ", jusqu'à " + hm(g.jusqu_a) : ", jusqu'à réactivation"}. Le collecteur reste connecté ; arrêts, alarmes et KPI ne sont plus alimentés.` : `écrits dans l'historian · ${nf(hist?.tags)} tags`}</span></div>
        <div class="row" role="group" aria-label="Durée de la suspension"><span class="sub">Suspendre :</span>
          ${[["15 min", 15], ["1 h", 60], ["jusqu'à réactivation", null]].map(([l, m]) => html`<button type="button" class="chip" onClick=${() => setHist("global", false, m, reload)}>${l}</button>`)}
        </div>
        ${hist?.scopes?.some(s => s.suspension) && html`<div class="sub small">Périmètres suspendus : ${hist.scopes.filter(s => s.suspension).map(s => s.nom).join(", ")} (voir Simulation)</div>`}
      </section>
      <section class="tile" aria-label="Stockage">
        <div class="tt"><h2>Stockage</h2>${disk && html`<${Pill} cls=${disk.pct > 0.9 ? "bad" : disk.pct > 0.75 ? "warn" : "ok"}>Disque Docker ${nf(disk.pct * 100)} %<//>`}</div>
        ${disk ? html`
          <div class="bar" role="img" aria-label="Répartition du disque">${disk.parts.map(p => html`<div title=${p.label} style=${{ width: p.w, background: p.c }}></div>`)}</div>
          <div style=${{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 12px" }} class="small">
            ${disk.parts.map(p => html`<span style=${{ display: "flex", alignItems: "center", gap: "6px" }}><span class="dot" style=${{ background: p.c }}></span>${p.label}<b class="num" style=${{ marginLeft: "auto" }}>${bytes(p.v)}</b></span>`)}
          </div>
          ${stock.purge && html`<div class="sub small">Conservation des données brutes : <b>${interval(stock.purge.conservation)}</b> · prochaine purge ${hm(stock.purge.prochaine)} (≈ ${bytes(stock.purge.octets)} libérés)</div>`}`
        : html`<span class="sub">Chargement…</span>`}
      </section>
    </div>
    <section class="tile" aria-label="Services">
      <div class="tt"><h2>Services</h2><span class="sub">${running} / ${svcs.length} en service · projet ${etat?.projet || "mes"}</span></div>
      ${etat?.docker_erreur && html`<div class="banner bad">${etat.docker_erreur}</div>`}
      <div class="scroll"><table>
        <thead><tr><th>Service</th><th>Rôle</th><th>État</th><th>CPU (10 min)</th><th>Mémoire</th><th>Depuis</th><th>Version</th><th><span class="sub">Actions</span></th></tr></thead>
        <tbody>${svcs.map(s => { const st = svcState(s); return html`<tr>
          <td><b>${s.service}</b></td>
          <td class="sub">${s.role}</td>
          <td><${Pill} cls=${st.cls} dot=${st.dot}>${st.label}<//></td>
          <td style=${{ whiteSpace: "nowrap" }}><svg aria-hidden="true" width="110" height="22" viewBox="0 0 110 22" style=${{ verticalAlign: "middle" }}><polyline points=${spark(s.historique_cpu)} fill="none" stroke=${st.dot} stroke-width="2"/></svg>
            <b class="num"> ${s.en_marche ? nf(s.cpu, 1) + " %" : "–"}</b></td>
          <td class="num">${s.en_marche ? bytes(s.memoire) : "–"}</td>
          <td class="sub">${ago(s.depuis)}</td>
          <td class="mono">${s.version}</td>
          <td style=${{ whiteSpace: "nowrap" }}><${ServiceActions} nowrap s=${s} reload=${() => { reload(); reloadJ(); }} openLogs=${openLogs}/></td>
        </tr>`; })}</tbody>
      </table></div>
    </section>
    <section class="tile" aria-label="Journal des opérations">
      <div class="tt"><h2>Journal des opérations</h2><a href="#/journal" class="sub">tout le journal</a></div>
      <${JournalRows} rows=${(journal || []).slice(0, 8)}/>
    </section>`;
}

const ACTIONS = {
  service_start: "service démarré", service_stop: "service arrêté", service_restart: "service redémarré",
  application_start: "application démarrée", application_stop: "application arrêtée", application_restart: "application redémarrée",
  historisation_suspendue: "historisation suspendue", historisation_reprise: "historisation reprise",
  simulation_regles: "règles de simulation modifiées", simulation_evenement: "événement de simulation injecté",
  simulation_evenement_arrete: "événement de simulation arrêté",
};

function JournalRows({ rows }) {
  if (!rows.length) return html`<span class="sub">Aucune opération enregistrée.</span>`;
  return html`<div style=${{ display: "grid", gap: "4px" }}>${rows.map(j => html`<div class="row" style=${{ flexWrap: "nowrap", fontSize: "13px" }}>
    <span class="num sub" style=${{ width: "92px", flex: "none" }}>${dhm(j.at)}</span><b style=${{ width: "110px", flex: "none" }}>${j.qui || "système"}</b>
    <span>${ACTIONS[j.action] || j.action.replaceAll("_", " ")}${j.id ? " · " + j.id : ""}${j.details && j.details.minutes ? " · " + j.details.minutes + " min" : ""}</span></div>`)}</div>`;
}

function Journal() {
  const { data } = usePoll("journal?limite=200", 15000);
  return html`<section class="tile" aria-label="Journal"><div class="tt"><h2>Journal des opérations</h2><span class="sub">console d'exploitation et application MES</span></div>
    <${JournalRows} rows=${data || []}/></section>`;
}

// ----------------------------------------------------------------------------- simulation
function Simulation({ hist, reload: reloadAll }) {
  const { data: sim, error, reload } = usePoll("simulation", 5000);
  const [draft, setDraft] = useState(null);
  useEffect(() => { if (sim && !draft) setDraft(JSON.parse(JSON.stringify(sim.regles))); }, [sim]);
  const dirty = sim && draft && JSON.stringify(sim.regles) !== JSON.stringify(draft);
  const apply = () => run(() => api("simulation/regles", { method: "PUT", body: draft }), "Règles appliquées, sans redémarrage").then(d => { setDraft(d); reload(); });
  const reset = () => run(() => api("simulation/regles", { method: "PUT", body: { defaut: true } }), "Règles par défaut").then(d => { setDraft(d); reload(); });
  const field = (spec, value, set) => html`<label class="f">${spec.libelle} (${spec.unite})
    <input class="inp num" type="number" min=${spec.min} max=${spec.max} step="any" value=${value} onInput=${e => set(parseFloat(e.target.value))}/></label>`;
  return html`<div class="cols">
    <div style=${{ flex: "1 1 420px", display: "grid", gap: "12px", minWidth: 0 }}>
      <section class="tile" aria-label="Règles de simulation">
        <div class="tt"><h2>Règles de simulation</h2>${sim ? html`<${Pill} cls="ok" dot=${G}>Source : simulateur<//>` : html`<${Pill} cls="neutral">Simulateur absent<//>`}</div>
        ${error && html`<div class="banner warn">${error}</div>`}
        ${sim && draft && html`
          <div style=${{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: "10px" }}>
            ${sim.specs.global.map(sp => field(sp, draft.global[sp.cle], v => setDraft({ ...draft, global: { ...draft.global, [sp.cle]: v } })))}
          </div>
          ${sim.dbs.map(d => html`<div style=${{ display: "grid", gap: "6px" }}>
            <div class="row" style=${{ justifyContent: "space-between" }}><b class="mono" style=${{ fontSize: "13px" }}>${d.nom}</b>
              <span class="sub small">${d.struct} · ${nf(d.instances)} instances · ${nf(d.tags)} tags · ${nf(d.valeurs_s)} val/s</span></div>
            <div style=${{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: "10px" }}>
              ${(sim.specs.struct[d.struct] || []).map(sp => field(sp, draft.db[d.nom]?.[sp.cle],
                v => setDraft({ ...draft, db: { ...draft.db, [d.nom]: { ...draft.db[d.nom], [sp.cle]: v } } })))}
            </div></div>`)}
          <div class="row"><button type="button" class="btn go" disabled=${!dirty} onClick=${apply}>Appliquer</button>
            <button type="button" class="btn" disabled=${!dirty} onClick=${() => setDraft(JSON.parse(JSON.stringify(sim.regles)))}>Annuler</button>
            <button type="button" class="btn" onClick=${reset}>Valeurs par défaut</button>
            <span class="sub small">${dirty ? "modifié, non appliqué" : "appliqué à chaud, sans redémarrage"}</span></div>`}
      </section>
      ${sim && html`<${Events} sim=${sim} reload=${reload}/>`}
    </div>
    <div style=${{ flex: "1 1 460px", display: "grid", gap: "12px", minWidth: 0 }}>
      <${TagExplorer}/>
      <${Scopes} hist=${hist} reload=${reloadAll}/>
    </div>
  </div>`;
}

function Events({ sim, reload }) {
  const [kind, setKind] = useState("arret_ligne");
  const def = sim.types_evenements[kind];
  const [params, setParams] = useState({});
  useEffect(() => {
    const p = { ...def.params };
    if ("cible" in p) p.cible = (kind.includes("moteur") ? sim.cibles.moteur : sim.cibles.ligne)[0] || "";
    setParams(p);
  }, [kind]);
  const launch = () => run(() => api("simulation/evenements", { method: "POST", body: { type: kind, params } }), def.libelle + " lancé").then(reload);
  const stop = id => run(() => api("simulation/evenements/" + id, { method: "DELETE" }), "Événement arrêté").then(reload);
  const LBL = { cible: "Cible", minutes: "Durée (min)", code: "Code d'arrêt", nombre: "Nombre", secondes: "Durée de chaque arrêt (s)", pct: "Amplitude (%)", facteur: "Facteur" };
  return html`<section class="tile" aria-label="Injecter un événement">
    <div class="tt"><h2>Injecter un événement</h2><span class="sub">pour tester arrêts, alarmes, dérives et KPI</span></div>
    <div class="row" role="group" aria-label="Type d'événement">
      ${Object.entries(sim.types_evenements).map(([k, v]) => html`<button type="button" class="chip" aria-pressed=${k === kind ? "true" : "false"}
        style=${k === kind ? { background: "var(--blue-t)", borderColor: "var(--blue-fg)" } : {}} onClick=${() => setKind(k)}>${v.libelle}</button>`)}
    </div>
    <div style=${{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "10px", alignItems: "end" }}>
      ${Object.keys(params).map(k => k === "cible"
        ? html`<label class="f">${LBL.cible}<select class="inp" value=${params.cible} onChange=${e => setParams({ ...params, cible: e.target.value })}>
            ${(kind.includes("moteur") ? sim.cibles.moteur : sim.cibles.ligne).map(n => html`<option value=${n}>${n}</option>`)}</select></label>`
        : html`<label class="f">${LBL[k] || k}<input class="inp num" type="number" step="any" value=${params[k]} onInput=${e => setParams({ ...params, [k]: parseFloat(e.target.value) })}/></label>`)}
      <button type="button" class="btn go" onClick=${launch}>Lancer</button>
    </div>
    ${kind === "coupure" && html`<span class="sub small">Toutes les valeurs passent en qualité mauvaise (BadCommunicationError) pendant la durée, puis sont republiées.</span>`}
    ${kind === "derive_moteur" && html`<span class="sub small">Le courant monte progressivement jusqu'à l'amplitude, puis reste à ce niveau jusqu'à l'arrêt de l'événement.</span>`}
    <div style=${{ display: "grid", gap: "6px" }}>
      ${sim.evenements.length ? sim.evenements.map(e => html`<div class="ev on row" style=${{ justifyContent: "space-between" }}>
        <span><b>${e.libelle}</b> <span class="sub small">${e.params.cible || ""} · depuis ${ago(e.debut)}${e.fin ? " · fin dans " + secs(e.fin - Date.now() / 1000) : " · jusqu'à arrêt"}</span></span>
        <button type="button" class="btn sm" onClick=${() => stop(e.id)}>Arrêter</button></div>`)
      : html`<span class="sub">Aucun événement en cours.</span>`}
    </div>
  </section>`;
}

function TagExplorer() {
  const [q, setQ] = useState("M101");
  const [sel, setSel] = useState(null);
  const { data: tags } = usePoll("tags?limite=30&q=" + encodeURIComponent(q), 5000, [q]);
  useEffect(() => { if (tags && tags.length && !tags.some(t => t.id === sel)) setSel(tags[0].id); }, [tags]);
  const { data: tr } = usePoll(sel ? `tags/${sel}/tendance?minutes=10` : null, 5000, [sel]);
  const cur = (tags || []).find(t => t.id === sel);
  let line = "", lo = 0, hi = 0;
  if (tr && tr.points.length) {
    const t1 = Date.now(), t0 = t1 - 10 * 60000;
    const pts = tr.points.filter(p => p[1] !== null).map(p => [new Date(p[0]).getTime(), p[1]]);
    if (tr.avant !== null && tr.avant !== undefined) pts.unshift([t0, tr.avant]);
    if (pts.length) {
      pts.push([t1, pts[pts.length - 1][1]]);
      lo = Math.min(...pts.map(p => p[1])); hi = Math.max(...pts.map(p => p[1]));
      const span = hi - lo || 1;
      // marches d'escalier : la valeur tient jusqu'au point suivant (historisation sur changement)
      line = pts.map((p, i) => {
        const x = ((p[0] - t0) / (t1 - t0) * 400).toFixed(1), y = (150 - (p[1] - lo) / span * 130).toFixed(1);
        const prevY = i ? (150 - (pts[i - 1][1] - lo) / span * 130).toFixed(1) : y;
        return (i ? `${x},${prevY} ` : "") + `${x},${y}`;
      }).join(" ");
    }
  }
  const qual = qv => (qv === 0 ? G : qv === 1 ? O : qv === 2 ? R : N);
  return html`<section class="tile" aria-label="Données en direct">
    <div class="tt"><h2>Données en direct</h2><span class="sub">valeurs historisées · actualisées toutes les 5 s</span></div>
    <label class="f">Rechercher un tag<input class="inp" value=${q} onInput=${e => setQ(e.target.value)} placeholder="ex. L3 ou DB_Lignes"/></label>
    <div class="cols" style=${{ alignItems: "stretch" }}>
      <div class="taglist" style=${{ flex: "1 1 220px", maxHeight: "300px", overflow: "auto", minWidth: 0 }}>
        ${(tags || []).map(t => html`<button type="button" class=${t.id === sel ? "on" : ""} onClick=${() => setSel(t.id)}>
          <span class="mono" style=${{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>${t.nom}</span>
          <b class="num small">${nf(t.valeur, 2)}</b><span class="dot" style=${{ background: qual(t.qualite) }}></span></button>`)}
        ${tags && !tags.length && html`<span class="sub">Aucun tag.</span>`}
      </div>
      <div style=${{ flex: "1 1 240px", display: "grid", gap: "6px", minWidth: 0 }}>
        <div class="row" style=${{ justifyContent: "space-between" }}><span class="mono" style=${{ fontWeight: 700 }}>${cur?.nom || ""}</span>
          <span class="num big">${cur ? nf(cur.valeur, 2) : ""} <span class="sub">${cur?.unite || ""}</span></span></div>
        <svg viewBox="0 0 400 160" preserveAspectRatio="none" role="img" aria-label=${"Tendance des 10 dernières minutes de " + (cur?.nom || "")}
          style=${{ width: "100%", height: "160px", background: "var(--fill)", borderRadius: "8px" }}>
          <polyline points=${line} fill="none" stroke="#06D16F" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
        <div class="row sub small" style=${{ justifyContent: "space-between" }}><span>il y a 10 min</span><span>min ${nf(lo, 2)} · max ${nf(hi, 2)}</span><span>maintenant</span></div>
        ${cur && html`<span class="sub small">Dernière écriture ${cur.heure ? "à " + new Date(cur.heure).toLocaleTimeString("fr-FR") : "–"} · qualité ${["bonne", "incertaine", "mauvaise"][cur.qualite] || "–"}</span>`}
      </div>
    </div>
  </section>`;
}

function Scopes({ hist, reload }) {
  if (!hist) return null;
  return html`<section class="tile" aria-label="Historisation par périmètre">
    <div class="tt"><h2>Historisation par périmètre</h2><span class="sub">un DB de l'automate par ligne</span></div>
    ${hist.global && html`<div class="banner warn"><span>Historisation suspendue globalement : aucun périmètre n'est écrit.</span></div>`}
    ${hist.scopes.map(s => html`<div class="row" style=${{ justifyContent: "space-between", padding: "8px 10px", border: "1px solid var(--line)", borderRadius: "10px", flexWrap: "nowrap" }}>
      <span style=${{ minWidth: 0 }}><b class="mono" style=${{ fontSize: "13px" }}>${s.nom}</b><br/>
        <span class="small" style=${{ color: s.suspension ? "var(--orange-fg)" : "var(--text2)" }}>${s.suspension
          ? `suspendu par ${s.suspension.par}${s.suspension.jusqu_a ? " jusqu'à " + hm(s.suspension.jusqu_a) : " jusqu'à réactivation"}`
          : `${nf(s.tags)} tags · ${nf(s.points_s, 1)} pts/s`}</span></span>
      <span class="row" style=${{ flexWrap: "nowrap" }}>
        ${!s.suspension && html`<button type="button" class="chip" onClick=${() => setHist(s.nom, false, 60, reload)}>1 h</button>`}
        <${Switch} on=${!s.suspension} label=${"Historiser " + s.nom} onChange=${on => setHist(s.nom, on, null, reload)}/></span>
    </div>`)}
    <span class="sub small">Pendant une suspension, le collecteur reste abonné : à la reprise, la dernière valeur de chaque tag est réécrite.</span>
  </section>`;
}

// ----------------------------------------------------------------------------- connexion, application
function Login({ onLogin, error: initial }) {
  const [u, setU] = useState(""), [p, setP] = useState(""), [err, setErr] = useState(initial);
  const submit = e => { e.preventDefault(); api("session", { method: "POST", body: { username: u, password: p } }).then(onLogin, x => setErr(x.message)); };
  return html`<form class="tile login" onSubmit=${submit}>
    <div class="brand" style=${{ padding: 0 }}><div class="logo"></div>MES · Exploitation</div>
    <p class="sub" style=${{ margin: 0 }}>Console réservée aux administrateurs. Compte local de l'application MES.</p>
    <label class="f">Identifiant<input class="inp" autocomplete="username" value=${u} onInput=${e => setU(e.target.value)} required/></label>
    <label class="f">Mot de passe<input class="inp" type="password" autocomplete="current-password" value=${p} onInput=${e => setP(e.target.value)} required/></label>
    ${err && html`<div class="banner bad" role="alert">${err}</div>`}
    <button class="btn go" type="submit">Se connecter</button>
  </form>`;
}

const PAGE_HEAD = {
  architecture: ["Exploitation · vue d'ensemble", "Architecture", "vivante"],
  services: ["Exploitation · commandes", "Services et", "commandes"],
  simulation: ["Exploitation · données", "Simulation", ""],
  journal: ["Exploitation · traçabilité", "Journal", ""],
};

function App() {
  const [user, setUser] = useState(undefined);
  const [loginErr, setLoginErr] = useState(null);
  const [page, setPage] = useState((location.hash.replace("#/", "") || "architecture"));
  const [theme, setThemeS] = useState(() => localStorage.getItem("mes-ops-theme") || "dark");
  const [logs, setLogs] = useState(null);
  const setTheme = t => { localStorage.setItem("mes-ops-theme", t); setThemeS(t); };
  useEffect(() => {
    api("session").then(setUser, e => { setLoginErr(e.message.includes("administrateurs") ? e.message : null); setUser(null); });
    const onHash = () => setPage(location.hash.replace("#/", "") || "architecture");
    const onOut = () => setUser(null);
    window.addEventListener("hashchange", onHash);
    window.addEventListener("ops-logout", onOut);
    return () => { window.removeEventListener("hashchange", onHash); window.removeEventListener("ops-logout", onOut); };
  }, []);
  const etat = usePoll(user ? "etat" : null, 5000, [user]);
  const hist = usePoll(user ? "historisation" : null, 5000, [user]);
  const reload = () => { etat.reload(); hist.reload(); };
  if (user === undefined) return html`<div class=${"root th-" + theme}></div>`;
  if (!user) return html`<div class=${"root th-" + theme}><${Login} error=${loginErr} onLogin=${u => { setUser(u); }}/></div>`;
  const p = PAGE_HEAD[page] ? page : "architecture";
  const [over, title, accent] = PAGE_HEAD[p];
  const props = { etat: etat.data, hist: hist.data, reload, openLogs: setLogs };
  return html`<div class=${"root th-" + theme}>
    <${Side} page=${p} etat=${etat.data} user=${user} onLogout=${() => api("session", { method: "DELETE" }).then(() => setUser(null))}/>
    <main>
      <${Header} over=${over} title=${title} accent=${accent} etat=${etat.data} hist=${hist.data} theme=${theme} setTheme=${setTheme} reload=${reload}/>
      <${Confirm}/>
      ${etat.error && html`<div class="banner bad"><span>${etat.error}</span></div>`}
      ${p === "architecture" && html`<${Architecture} ...${props}/>`}
      ${p === "services" && html`<${Services} ...${props}/>`}
      ${p === "simulation" && html`<${Simulation} hist=${hist.data} reload=${reload}/>`}
      ${p === "journal" && html`<${Journal}/>`}
    </main>
    <${LogsDialog} service=${logs} onClose=${() => setLogs(null)}/>
    <${Toast}/>
  </div>`;
}

render(html`<${App}/>`, document.getElementById("app"));
