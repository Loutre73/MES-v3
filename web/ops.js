// Console d'exploitation du MES v3 (/systeme) : architecture, services, historisation, simulation.
// Servie par l'agent d'exploitation (/api/ops/...) : elle reste disponible quand l'application est arrêtée.
import { html, render, useState, useEffect, useRef } from "./vendor/preact-htm.module.js";
import { Band, Icon, Dialog, num, dur } from "./lib.js";

// ----------------------------------------------------------------------------- accès à l'agent
async function ops(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch("/api/ops" + path, init);
  if (r.status === 401) { location.reload(); throw new Error("Session expirée"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && (data.detail || data.erreur)) || r.statusText);
  return data;
}

function usePoll(path, ms) {
  const [s, set] = useState({ data: null, error: null });
  const tick = useRef(0);
  const load = () => {
    if (!path) return;
    const n = ++tick.current;
    ops(path).then(d => n === tick.current && set({ data: d, error: null }), e => set(x => ({ ...x, error: e.message })));
  };
  useEffect(() => {
    load();
    if (!ms) return;
    const t = setInterval(load, ms);
    return () => clearInterval(t);
  }, [path]);
  return { ...s, reload: load };
}

// ----------------------------------------------------------------------------- formats
const octets = b => {
  if (b === null || b === undefined || b < 0) return "–";
  const u = ["o", "Ko", "Mo", "Go", "To"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return num(b, b < 10 && i > 1 ? 1 : 0) + " " + u[i];
};
const rate = v => (v === null || v === undefined ? "–" : num(v, v < 10 ? 1 : 0));
const since = t => (t ? dur(Date.now() / 1000 - t) : "–");
const hhmm = t => new Date(t).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
const hhmmss = t => new Date(t).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const RETENTION = { "1 year": "1 an", "3 years": "3 ans", "9 hours": "9 h" };
const retention = v => RETENTION[v] || (v || "").replace(" hours", " h").replace(" days", " j").replace(" years", " ans").replace(" year", " an");

// état affiché d'un service : bon, attention, arrêté, terminé
function svcState(s) {
  if (!s) return { kind: "idle", label: "Absent" };
  if (s.service === "migrate" && !s.running) return s.exit_code === 0 ? { kind: "idle", label: "Terminé" } : { kind: "bad", label: "Échec" };
  if (!s.running) return { kind: "bad", label: s.state === "restarting" ? "Redémarre" : "Arrêté" };
  if (s.health === "unhealthy") return { kind: "warn", label: "En échec (santé)" };
  if (s.health === "starting") return { kind: "warn", label: "Démarrage" };
  return { kind: "good", label: "En service" };
}
const KIND_COLOR = { good: "var(--green)", warn: "var(--orange)", bad: "var(--red)", idle: "var(--muted)" };
const Pill = ({ kind, children }) => html`<span class=${"pill " + kind}><span class="dot"></span>${children}</span>`;

function Spark({ data, color = "var(--green)", w = 110, h = 24, max }) {
  if (!data || data.length < 2) return html`<svg width=${w} height=${h} aria-hidden="true"></svg>`;
  const m = max || Math.max(1, ...data);
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${(h - 2 - (v / m) * (h - 4)).toFixed(1)}`).join(" ");
  return html`<svg width=${w} height=${h} viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points=${pts} fill="none" stroke=${color} stroke-width="1.8" /></svg>`;
}

function Switch({ on, onChange, label, disabled }) {
  return html`<button type="button" class=${"sw" + (on ? " on" : "")} role="switch" aria-checked=${on} aria-label=${label}
    title=${label} disabled=${disabled} onClick=${() => onChange(!on)}><i></i></button>`;
}

function Confirm({ title, text, ok = "Confirmer", danger = true, onOk, onClose }) {
  const [busy, setBusy] = useState(false);
  return html`<${Dialog} title=${title} onClose=${onClose}>
    <p>${text}</p>
    <div class="row-end"><button type="button" onClick=${onClose}>Annuler</button>
      <button type="button" class=${danger ? "stop" : "primary"} disabled=${busy}
        onClick=${async () => { setBusy(true); await onOk(); setBusy(false); onClose(); }}>${busy ? "En cours…" : ok}</button></div>
  <//>`;
}

// ----------------------------------------------------------------------------- actions communes
function useActions(reload, toast) {
  const run = async (fn, okText) => {
    try { await fn(); toast({ ok: okText }); } catch (e) { toast({ error: e.message }); }
    reload();
  };
  return {
    service: (svc, action, label) => run(() => ops(`/services/${svc}/${action}`, { method: "POST", body: {} }),
      `${label} : ${{ start: "démarré", stop: "arrêté", restart: "redémarré" }[action]}`),
    app: action => run(() => ops(`/application/${action}`, { method: "POST", body: {} }),
      `Application ${{ start: "démarrée", stop: "arrêtée", restart: "redémarrée" }[action]}`),
    hist: (body, okText) => run(() => ops("/historisation", { method: "PUT", body }), okText),
  };
}

// ----------------------------------------------------------------------------- 1 · Architecture
// Positions en % du schéma (x, y) ; les flux sont tracés entre centres.
const NODES = {
  source: [12, 9], collector: [50, 9], db: [88, 9],
  prometheus: [12, 37], grafana: [50, 37], api: [88, 37],
  exporters: [12, 65], ops: [50, 65], web: [88, 65],
  users: [88, 92],
};
const EDGES = [
  ["source", "collector", f => f.opc_vals_s !== null && f.opc_vals_s !== undefined ? rate(f.opc_vals_s) + " val/s" : "OPC UA"],
  ["collector", "db", f => (f.pts_s !== null && f.pts_s !== undefined ? rate(f.pts_s) + " pts/s" : "COPY")],
  ["api", "db", () => "SQL"],
  ["web", "api", f => (f.req_s !== null && f.req_s !== undefined ? rate(f.req_s) + " req/s" : "HTTP")],
  ["grafana", "db", () => "SQL"],
  ["users", "web", () => "navigateurs"],
  ["web", "ops", () => "/systeme"],
  ["prometheus", "exporters", () => "10 s"],
];

function Architecture({ d, actions, admin, setConfirm, openLogs }) {
  const [sel, setSel] = useState("collector");
  const by = Object.fromEntries(d.services.map(s => [s.service, s]));
  const col = d.collecteur, hist = col && col.historisation;
  const hasSim = !!by.simulator;
  const exporters = ["node-exporter", "docker-exporter", "process-exporter", "postgres-exporter"].map(k => by[k]).filter(Boolean);
  const node = id => {
    if (id === "source") {
      if (hasSim) return { svc: by.simulator, title: "Simulateur S7-1500", sub: d.simulateur ? `${num(d.simulateur.total_tags, 0)} tags` : "contrôle injoignable" };
      return { svc: null, title: "Automate S7-1517", sub: col ? col.endpoint : "", kind: col ? (col.opcua ? "good" : "bad") : "idle", label: col && col.opcua ? "Connecté" : "Déconnecté" };
    }
    if (id === "users") return { svc: null, title: "Utilisateurs", sub: "navigateur, Grafana", kind: "idle", label: "" };
    if (id === "exporters") {
      if (!exporters.length) return null;
      const down = exporters.filter(s => !s.running).length;
      return { svc: null, group: exporters, title: "Exporteurs", sub: `${exporters.length - down} / ${exporters.length} en service`, kind: down ? "warn" : "good", label: down ? "Partiel" : "En service" };
    }
    const s = by[id];
    if (!s) return null;
    const n = { svc: s, title: s.label, sub: s.cpu_pct !== null && s.cpu_pct !== undefined ? `CPU ${num(s.cpu_pct, 1)} % · ${octets(s.mem_bytes)}` : svcState(s).label };
    if (id === "collector" && s.running && hist && (!hist.actif || hist.dbs_suspendus.length)) n.warn = !hist.actif ? "Historisation suspendue" : "Historisation partielle";
    if (id === "collector" && s.running && col && !col.opcua) n.warn = "OPC UA déconnecté";
    if (id === "db" && s.running && d.hote.disk_pct >= 90) n.warn = `Disque ${num(d.hote.disk_pct, 0)} %`;
    return n;
  };
  const view = Object.fromEntries(Object.keys(NODES).map(id => [id, node(id)]).filter(([, n]) => n));
  const kindOf = n => n.warn ? "warn" : n.kind || svcState(n.svc).kind;
  const current = view[sel] ? sel : "collector";
  const flux = d.flux || {};
  return html`<div class="arch">
    <section class="tile arch-map" aria-label="Schéma de l'architecture">
      <div class="th"><h2>Architecture en direct</h2><span class="sub">cliquer un bloc pour le détail et les actions · relevé toutes les 5 s</span></div>
      <div class="map">
        <svg class="edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          ${EDGES.filter(([a, b]) => view[a] && view[b]).map(([a, b]) => {
            const [x1, y1] = NODES[a], [x2, y2] = NODES[b];
            const live = view[a].svc ? view[a].svc.running !== false : true;
            return html`<line x1=${x1} y1=${y1} x2=${x2} y2=${y2} class=${live && view[b].svc?.running !== false ? "flow" : "flow off"} vector-effect="non-scaling-stroke" />`;
          })}
        </svg>
        ${EDGES.filter(([a, b]) => view[a] && view[b]).map(([a, b, lab]) => {
          const [x1, y1] = NODES[a], [x2, y2] = NODES[b];
          return html`<span class="elabel" style=${{ left: (x1 + x2) / 2 + "%", top: (y1 + y2) / 2 + "%" }}>${lab(flux)}</span>`;
        })}
        ${Object.entries(view).map(([id, n]) => {
          const k = kindOf(n);
          return html`<button type="button" class=${"node " + k + (current === id ? " sel" : "")} style=${{ left: NODES[id][0] + "%", top: NODES[id][1] + "%" }}
              onClick=${() => setSel(id)} aria-pressed=${current === id}>
            <b><span class="dot" style=${{ background: KIND_COLOR[k] }}></span>${n.title}</b>
            <span>${n.warn || n.sub}</span></button>`;
        })}
      </div>
      <div class="hoststrip">
        <div><span>CPU hôte</span><b>${num(d.hote.cpu_pct, 0)} %</b><${Spark} data=${d.historique.host_cpu} max=${100} w=${80} h=${18} /></div>
        <div><span>Mémoire</span><b>${octets(d.hote.mem_used)} / ${octets(d.hote.mem_total)}</b></div>
        <div class=${d.hote.disk_pct >= 90 ? "alert" : ""}><span>Disque</span><b>${num(d.hote.disk_pct, 0)} %</b><small>${octets(d.hote.disk_total - d.hote.disk_used)} libres</small></div>
        <div><span>Charge (1 min)</span><b>${num(d.hote.load1, 2)}</b><small>${d.hote.cpus} cœurs</small></div>
        <div><span>Conteneurs</span><b>${d.services.filter(s => s.running).length} / ${d.services.length}</b><small>en service</small></div>
      </div>
    </section>
    <${NodePanel} id=${current} n=${view[current]} d=${d} actions=${actions} admin=${admin} setConfirm=${setConfirm} openLogs=${openLogs} />
  </div>`;
}

function KV({ k, v }) { return html`<div class="kv"><span>${k}</span><b>${v}</b></div>`; }

function NodePanel({ id, n, d, actions, admin, setConfirm, openLogs }) {
  const s = n.svc;
  const [logs, setLogs] = useState(null);
  useEffect(() => {
    setLogs(null);
    if (!s) return;
    let live = true;
    const load = () => ops(`/services/${s.service}/journaux?lignes=40`).then(r => live && setLogs(r.lignes), () => live && setLogs([]));
    load();
    const t = setInterval(load, 10000);
    return () => { live = false; clearInterval(t); };
  }, [s && s.service]);
  const col = d.collecteur, hist = col && col.historisation;
  const st = s ? svcState(s) : null;
  return html`<aside class="tile panel" aria-label="Détail du bloc">
    <div class="th"><h2>${n.title}</h2><span class="spacer"></span>${st ? html`<${Pill} kind=${st.kind}>${st.label}<//>` : n.label && html`<${Pill} kind=${n.kind}>${n.label}<//>`}</div>
    ${s && html`<p class="sub" style="margin:0">${s.role}</p>`}
    <div class="kvs">
      ${s && html`
        <${KV} k="Depuis" v=${s.running ? since(s.started_at) : s.finished_at ? "arrêté à " + hhmm(s.finished_at * 1000) : "–"} />
        <${KV} k="CPU" v=${s.cpu_pct === null || s.cpu_pct === undefined ? "–" : num(s.cpu_pct, 1) + " %"} />
        <${KV} k="Mémoire" v=${octets(s.mem_bytes)} />
        <${KV} k="Redémarrages" v=${s.restarts} />
        <${KV} k="Image" v=${html`<span class="mono">${s.image.split("/").pop()}</span>`} />`}
      ${id === "collector" && col && html`
        <${KV} k="Session OPC UA" v=${col.opcua ? "connectée" : "déconnectée"} />
        <${KV} k="Tags surveillés" v=${num(col.tags, 0)} />
        <${KV} k="Valeurs reçues" v=${rate(col.notifications_s) + " /s"} />
        <${KV} k="Écrites" v=${rate(col.points_s) + " pts/s"} />
        <${KV} k="File / tampon disque" v=${`${num(col.file, 0)} / ${num(col.tampon_disque, 0)} lots`} />`}
      ${id === "source" && !s && col && html`<${KV} k="Point d'accès" v=${html`<span class="mono">${col.endpoint}</span>`} />`}
      ${id === "source" && d.simulateur && html`
        <${KV} k="Changements" v=${rate(d.simulateur.changements_s) + " /s"} />
        <${KV} k="Scénarios en cours" v=${d.simulateur.scenarios.filter(x => x.en_cours).map(x => x.label).join(", ") || "aucun"} />`}
      ${id === "db" && html`<${KV} k="Conservation brute" v=${retention(d.conservation.brut)} /><${KV} k="Disque hôte" v=${num(d.hote.disk_pct, 0) + " %"} />`}
      ${id === "web" && html`<${KV} k="Requêtes API" v=${rate(d.flux.req_s) + " /s"} />`}
      ${n.group && n.group.map(g => html`<${KV} k=${g.label} v=${svcState(g).label} />`)}
    </div>
    ${id === "collector" && hist && html`<div class="hist-inline">
      <${Switch} on=${hist.actif} label="Historisation" disabled=${!admin}
        onChange=${on => on ? actions.hist({ actif: true }, "Historisation reprise") : setConfirm({ title: "Suspendre l'historisation ?",
          text: "Le collecteur reste connecté mais n'écrit plus dans l'historian : arrêts, alarmes et KPI ne sont plus alimentés jusqu'à la reprise.",
          ok: "Suspendre", onOk: () => actions.hist({ actif: false }, "Historisation suspendue") })} />
      <span>Historisation ${hist.actif ? "active" : "suspendue" + (hist.jusqua ? " jusqu'à " + hhmm(hist.jusqua) : "")}</span></div>`}
    ${s && s.pilotable && html`<div class="actions">
      ${s.running ? html`
        <button type="button" disabled=${!admin} onClick=${() => actions.service(s.service, "restart", s.label)}>Redémarrer</button>
        <button type="button" class="stop" disabled=${!admin} onClick=${() => setConfirm({ title: `Arrêter ${s.label} ?`,
          text: stopWarning(s.service), ok: "Arrêter", onOk: () => actions.service(s.service, "stop", s.label) })}>Arrêter</button>`
      : html`<button type="button" class="primary" disabled=${!admin} onClick=${() => actions.service(s.service, "start", s.label)}>Démarrer</button>`}
      <button type="button" onClick=${() => openLogs(s)}>Journaux complets</button>
    </div>`}
    ${s && !s.pilotable && html`<div class="actions"><button type="button" onClick=${() => openLogs(s)}>Journaux complets</button>
      <span class="sub">${s.service === "migrate" ? "S'exécute à chaque démarrage." : "Sert la console : pas d'action ici."}</span></div>`}
    ${!admin && s && s.pilotable && html`<p class="sub" style="margin:0">Actions réservées aux administrateurs.</p>`}
    ${s && html`<div class="logs mini" aria-label="Derniers journaux">${logs === null ? html`<span class="sub">Chargement…</span>` :
      logs.length ? logs.slice(-14).map(l => html`<div>${logLine(l)}</div>`) : html`<span class="sub">Aucune ligne.</span>`}</div>`}
  </aside>`;
}

// horodatage Docker retiré quand la ligne a déjà le sien ; sinon réduit à l'heure (UTC)
function logLine(l) {
  const m = /^(\S+)T(\d\d:\d\d:\d\d)\.\d+Z (.*)$/.exec(l);
  if (!m) return l;
  return /^\[?\d{4}[-/]\d\d[-/]\d\d|^\d{4}\/|^time=|^t=/.test(m[3]) ? m[3] : m[2] + " " + m[3];
}

function stopWarning(svc) {
  return {
    collector: "La collecte s'arrête : plus aucune valeur n'est écrite dans l'historian tant qu'il est arrêté.",
    db: "Arrêter la base interrompt l'API, Grafana et la collecte (le collecteur met ses valeurs en tampon disque).",
    api: "L'application MES devient indisponible pour les utilisateurs ; la collecte continue.",
    simulator: "Le collecteur perd sa source : les tags passent en qualité mauvaise jusqu'au redémarrage.",
    grafana: "Les tableaux Grafana deviennent indisponibles.",
  }[svc] || "Le service sera arrêté jusqu'à son redémarrage depuis cette console ou ./mesctl start.";
}

// ----------------------------------------------------------------------------- 2 · Services et commandes
function Services({ d, actions, admin, setConfirm, openLogs, toast }) {
  const journal = usePoll("/journal?n=60", 10000);
  const col = d.collecteur, hist = col && col.historisation;
  const app = d.application;
  const layers = ["Acquisition", "Stockage", "Application", "Observabilité", "Autres"];
  const vols = d.volumes.items || [];
  const volTotal = vols.reduce((a, v) => a + Math.max(0, v.bytes), 0);
  const VOL = { "db-data": "Base (historian, MES)", "prometheus-data": "Prometheus", "grafana-data": "Grafana", "collector-spool": "Tampon du collecteur", "ops-data": "Console", "sim-data": "Simulateur" };
  const VCOL = ["#2F5D8A", "#82A1CD", "#7FD3E3", "#06D16F", "#A9BDD6", "#5B7FA8"];
  const suspend = (min, label) => actions.hist({ actif: false, duree_min: min }, "Historisation suspendue " + label);
  const [purge, setPurge] = useState(false);
  return html`<div class="svc-view">
    <div class="svc-top">
      <section class="tile" aria-label="Application">
        <div class="th"><h2>Application MES</h2><span class="spacer"></span>
          <${Pill} kind=${app === "en_service" ? "good" : app === "arretee" ? "bad" : "warn"}>${{ en_service: "En service", arretee: "Arrêtée", partielle: "Partielle" }[app]}<//></div>
        <p class="sub" style="margin:0">Collecteur, API et Grafana. La base, le web et cette console restent en marche : aucune donnée n'est perdue et l'application peut être redémarrée d'ici.</p>
        <div class="actions">
          <button type="button" class="primary" disabled=${!admin || app === "en_service"} onClick=${() => actions.app("start")}>Démarrer</button>
          <button type="button" disabled=${!admin || app === "arretee"} onClick=${() => setConfirm({ title: "Redémarrer l'application ?", text: "Collecteur, API et Grafana redémarrent (environ 20 s d'indisponibilité).", ok: "Redémarrer", danger: false, onOk: () => actions.app("restart") })}>Redémarrer</button>
          <button type="button" class="stop" disabled=${!admin || app === "arretee"} onClick=${() => setConfirm({ title: "Arrêter l'application ?", text: "La collecte s'arrête et l'application MES devient indisponible pour les utilisateurs. La base, le web et cette console restent en marche.", ok: "Arrêter", onOk: () => actions.app("stop") })}>Arrêter</button>
        </div>
        <div class="sub small">Version ${d.version} · Docker ${d.docker ? "joignable" : html`<b style="color:var(--red-fg)">injoignable</b>`}</div>
      </section>
      <section class="tile" aria-label="Historisation">
        <div class="th"><h2>Historisation</h2><span class="spacer"></span>
          ${hist && html`<${Switch} on=${hist.actif} label="Historisation" disabled=${!admin}
            onChange=${on => on ? actions.hist({ actif: true }, "Historisation reprise") : suspend(null, "jusqu'à réactivation")} />`}</div>
        ${!col ? html`<p class="sub">Collecteur arrêté ou injoignable.</p>` : html`
          <div class="big"><b class=${hist.actif ? "" : "warn-tx"}>${hist.actif ? rate(col.points_s) + " pts/s" : "Suspendue"}</b>
            <span class="sub">${hist.actif ? `écrits dans l'historian · ${num(col.tags, 0)} tags` + (hist.dbs_suspendus.length ? ` · suspendue pour ${hist.dbs_suspendus.join(", ")}` : "")
              : (hist.jusqua ? `reprise automatique à ${hhmm(hist.jusqua)}` : "jusqu'à réactivation") + " · le collecteur reste connecté ; arrêts, alarmes et KPI ne sont plus alimentés"}</span></div>
          <div class="actions"><span class="sub">Suspendre :</span>
            ${[[15, "15 min"], [60, "1 h"], [null, "jusqu'à réactivation"]].map(([m, l]) => html`<button type="button" class="chip" disabled=${!admin} onClick=${() => suspend(m, m ? "pour " + l : l)}>${l}</button>`)}</div>`}
        <div class="sub small">Données brutes conservées ${retention(d.conservation.brut)} (purge horaire) · agrégats 1 min : ${retention(d.conservation.agregats_1min)} · 1 h : ${retention(d.conservation.agregats_1h)}. Par DB : onglet Simulation.</div>
      </section>
      <section class="tile" aria-label="Stockage">
        <div class="th"><h2>Stockage</h2><span class="spacer"></span><${Pill} kind=${d.hote.disk_pct >= 90 ? "bad" : d.hote.disk_pct >= 80 ? "warn" : "good"}>Disque ${num(d.hote.disk_pct, 0)} %<//></div>
        <div class="gauge" title=${`${octets(d.hote.disk_used)} utilisés sur ${octets(d.hote.disk_total)}`}><div style=${{ width: d.hote.disk_pct + "%", background: d.hote.disk_pct >= 90 ? "var(--red)" : d.hote.disk_pct >= 80 ? "var(--orange)" : "var(--green)" }}></div></div>
        <div class="sub small">${octets(d.hote.disk_used)} utilisés sur ${octets(d.hote.disk_total)} (disque de Docker)</div>
        <div class="vols">${vols.length ? vols.slice(0, 6).map((v, i) => html`<div><span class="dot" style=${{ background: VCOL[i % VCOL.length] }}></span>${VOL[v.name] || v.name}<b class="num">${octets(v.bytes)}</b></div>`)
          : html`<span class="sub">Taille des volumes en cours de calcul…</span>`}</div>
        ${volTotal > 0 && html`<div class="sub small">Volumes MES : ${octets(volTotal)} · mesuré à ${hhmm(d.volumes.at * 1000)}</div>`}
        <div class="actions"><button type="button" class="sm" onClick=${() => setPurge(true)}>Historian et purge…</button></div>
      </section>
      ${purge && html`<${Purge} admin=${admin} toast=${toast} onClose=${() => setPurge(false)} />`}
    </div>
    <section class="tile svc-table" aria-label="Services">
      <div class="th"><h2>Services</h2><span class="sub">${d.services.filter(s => s.running).length} / ${d.services.length} en service</span></div>
      <div class="scroll"><table>
        <thead><tr><th>Service</th><th>Rôle</th><th>État</th><th>CPU (5 min)</th><th class="num">Mémoire</th><th>Depuis</th><th class="num">Redém.</th><th></th></tr></thead>
        <tbody>${layers.map(layer => {
          const rows = d.services.filter(s => s.couche === layer);
          if (!rows.length) return null;
          return html`<tr class="grp"><td colspan="8">${layer}</td></tr>${rows.map(s => {
            const st = svcState(s);
            return html`<tr>
              <td><b>${s.label}</b></td><td class="sub">${s.role}</td>
              <td><${Pill} kind=${st.kind}>${st.label}<//></td>
              <td class="spark"><${Spark} data=${s.cpu_hist} color=${KIND_COLOR[st.kind]} /> <b class="num">${s.cpu_pct === null || s.cpu_pct === undefined ? "–" : num(s.cpu_pct, 1) + " %"}</b></td>
              <td class="num">${octets(s.mem_bytes)}</td>
              <td class="sub">${s.running ? since(s.started_at) : s.finished_at ? "arrêté " + hhmm(s.finished_at * 1000) : "–"}</td>
              <td class="num">${s.restarts}</td>
              <td class="acts">
                <button type="button" class="sm" onClick=${() => openLogs(s)}>Journaux</button>
                ${s.pilotable && (s.running ? html`
                  <button type="button" class="sm" disabled=${!admin} onClick=${() => actions.service(s.service, "restart", s.label)}>Redémarrer</button>
                  <button type="button" class="sm stop" disabled=${!admin} onClick=${() => setConfirm({ title: `Arrêter ${s.label} ?`, text: stopWarning(s.service), ok: "Arrêter", onOk: () => actions.service(s.service, "stop", s.label) })}>Arrêter</button>`
                : html`<button type="button" class="sm primary" disabled=${!admin} onClick=${() => actions.service(s.service, "start", s.label)}>Démarrer</button>`)}
              </td></tr>`;
          })}`;
        })}</tbody></table></div>
    </section>
    <section class="tile journal" aria-label="Journal des opérations">
      <div class="th"><h2>Journal des opérations</h2><span class="sub">qui a fait quoi depuis cette console</span></div>
      <div class="scroll">${(journal.data || []).length ? journal.data.map(j => html`<div class="jl">
        <span class="num sub">${new Date(j.t).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
        <b>${j.who}</b><span class=${j.ok ? "" : "bad-tx"}>${j.action}${j.ok ? "" : " — échec"}${j.detail && !j.ok ? " : " + j.detail : ""}</span></div>`)
        : html`<span class="sub">Aucune opération pour l'instant.</span>`}</div>
    </section>
  </div>`;
}

// Volume de l'historian par jeu de données, plafond de la base et purge manuelle des plus anciennes données
const JEUX = { brut: "Données brutes", "1min": "Agrégats 1 min", "1h": "Agrégats 1 h" };
const GARDER = { brut: [[1, "1 h"], [3, "3 h"], [6, "6 h"], [12, "12 h"], [24, "1 j"], [72, "3 j"]],
  "1min": [[48, "2 j"], [168, "7 j"], [720, "30 j"], [2160, "90 j"]],
  "1h": [[720, "30 j"], [2160, "90 j"], [8760, "1 an"]] };
function Purge({ admin, toast, onClose }) {
  const st = usePoll("/stockage", 0);
  const [jeu, setJeu] = useState("brut");
  const [garder, setGarder] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sure, setSure] = useState(false);
  const e = st.data;
  const info = e && e.jeux.find(j => j.jeu === jeu);
  const choix = info ? GARDER[jeu].filter(([h]) => h >= info.minimum_h) : [];
  const g = choix.some(([h]) => h === garder) ? garder : (choix[0] || [null])[0];
  const label = (choix.find(([h]) => h === g) || [0, ""])[1];
  const run = async () => {
    setBusy(true);
    try {
      const r = await ops("/stockage/purge", { method: "POST", body: { jeu, garder_h: g } });
      toast({ ok: `Purge terminée : ${r.chunks} bloc(s) supprimé(s), ${octets(r.liberes_octets)} libérés` });
      st.reload();
    } catch (err) { toast({ error: err.message }); }
    setBusy(false); setSure(false);
  };
  return html`<${Dialog} title="Historian : volume et purge" onClose=${onClose}>
    ${!e ? html`<p class="sub">${st.error || "Lecture de la base…"}</p>` : html`
      <p class="sub" style="margin-top:0">Base : <b>${octets(e.base_octets)}</b>${e.plafond_octets > 0
        ? html` sur un plafond de <b>${octets(e.plafond_octets)}</b> (au-delà, les plus anciennes données sont supprimées automatiquement)`
        : " · aucun plafond (HISTORIAN_MAX_SIZE dans site.env)"}.</p>
      <table><thead><tr><th>Jeu</th><th class="num">Taille</th><th class="num">Blocs</th><th>Depuis</th></tr></thead>
        <tbody>${e.jeux.map(j => html`<tr><td>${JEUX[j.jeu]}</td><td class="num">${octets(j.octets)}</td><td class="num">${j.chunks}</td>
          <td class="sub">${j.depuis ? new Date(j.depuis).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–"}</td></tr>`)}</tbody></table>
      <h3 style="margin:16px 0 6px">Purger</h3>
      <div class="actions">
        <select value=${jeu} onChange=${ev => { setJeu(ev.target.value); setSure(false); }} aria-label="Jeu de données" disabled=${!admin}>
          ${Object.entries(JEUX).map(([k, l]) => html`<option value=${k} selected=${k === jeu}>${l}</option>`)}</select>
        <span class="sub">garder les</span>
        <select value=${g} onChange=${ev => { setGarder(Number(ev.target.value)); setSure(false); }} aria-label="Durée à conserver" disabled=${!admin}>
          ${choix.map(([h, l]) => html`<option value=${h} selected=${h === g}>${l}</option>`)}</select>
        <span class="sub">les plus récents</span></div>
      <p class="sub small">Suppression définitive, par blocs entiers. ${jeu === "brut"
        ? `Les agrégats déjà calculés sont conservés. Minimum : ${info.minimum_h} h (recalcul des agrégats en cours).`
        : jeu === "1min" ? "Les agrégats horaires sont conservés." : "Seuls les KPI du MES restent pour la période purgée."}</p>`}
    <div class="row-end"><button type="button" onClick=${onClose}>Fermer</button>
      ${e && (sure
        ? html`<button type="button" class="stop" disabled=${busy || !admin || !g} onClick=${run}>${busy ? "Purge en cours…" : `Confirmer : ${JEUX[jeu].toLowerCase()} de plus de ${label}`}</button>`
        : html`<button type="button" class="stop" disabled=${!admin || !g} onClick=${() => setSure(true)}>Purger…</button>`)}</div>
  <//>`;
}

// ----------------------------------------------------------------------------- 3 · Simulation
const RULES = {
  arrets_h: ["Arrêts par heure", "par instance", 0.1],
  duree_arret_s: ["Durée moyenne d'un arrêt", "s", 5],
  part_defaut: ["Part des arrêts sur défaut", "0 à 1", 0.05],
  derive_ratio: ["Part des moteurs qui dérivent", "0 à 1", 0.01],
  bruit_pct: ["Bruit de mesure", "%", 0.5],
  cadence_pct: ["Cadence (% du nominal)", "%", 5],
  amplitude_pct: ["Amplitude journalière", "%", 1],
  change_ratio: ["Probabilité de changement par cycle", "0 à 1", 0.05],
  ecart: ["Écart type d'un changement", "unités", 0.1],
};
const STRUCT = { moteur: "Moteurs", ligne: "Lignes", compteur_energie: "Compteurs d'énergie", mesure: "Mesures de process" };
const SIM_TABS = [["scenarios", "Scénarios"], ["base", "Comportement de base"], ["live", "Tags en direct"]];

function Simulation({ d, actions, admin, toast }) {
  const sim = usePoll(d.simulateur ? "/simulation/etat" : null, 3000);
  const col = d.collecteur, hist = col && col.historisation;
  const [tab, setTab] = useState(() => { try { return localStorage.getItem("mes-sim-onglet") || "scenarios"; } catch (e) { return "scenarios"; } });
  useEffect(() => { try { localStorage.setItem("mes-sim-onglet", tab); } catch (e) { /* stockage indisponible */ } }, [tab]);
  if (!d.simulateur) {
    const sv = d.services.find(s => s.service === "simulator");
    return html`<section class="tile" style="flex:1"><div class="th"><h2>Simulation</h2></div>
      <p>${sv ? "Le simulateur est arrêté : démarrez-le depuis l'onglet Services pour régler ses règles." :
        "Source de données : automate réel. Le simulateur n'est pas installé sur ce site (profil « sim » non activé)."}</p>
      ${hist && html`<${Perimeters} hist=${hist} actions=${actions} admin=${admin} />`}</section>`;
  }
  const s = sim.data || d.simulateur;
  const old = !s.effets;   // simulateur d'une version précédente
  return html`<div class="simv">
    ${!old && html`<${TimeBar} s=${s} admin=${admin} toast=${toast} reload=${sim.reload} tab=${tab} setTab=${setTab} />`}
    ${old ? html`<section class="tile"><p>Simulateur d'une version précédente : mettez l'application à jour pour les scénarios.</p></section>`
      : tab === "base" ? html`<${Rules} s=${s} hist=${hist} actions=${actions} admin=${admin} toast=${toast} reload=${sim.reload} />`
      : tab === "live" ? html`<${Explorer} />`
      : html`<${Studio} s=${s} admin=${admin} toast=${toast} reload=${sim.reload} />`}
  </div>`;
}

function Perimeters({ hist, actions, admin }) {
  return html`<div class="perims">${hist.perimetres.map(p => html`<div class="perim">
    <${Switch} on=${p.historise && hist.actif} disabled=${!admin || !hist.actif} label=${"Historiser " + p.nom}
      onChange=${on => actions.hist({ dbs: { [p.nom]: on } }, `Historisation ${on ? "reprise" : "suspendue"} pour ${p.nom}`)} />
    <b>${p.nom}</b><span class="sub">${num(p.tags, 0)} tags · ${rate(p.points_s)} pts/s</span></div>`)}</div>`;
}

function Rules({ s, hist, actions, admin, toast, reload }) {
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const dirty = Object.keys(draft).length > 0;
  const val = (db, k, cur) => (draft[db] && draft[db][k] !== undefined ? draft[db][k] : cur);
  const edit = (db, k, v) => setDraft(x => ({ ...x, [db]: { ...(x[db] || {}), [k]: v } }));
  const apply = async body => {
    setBusy(true);
    try { await ops("/simulation/regles", { method: "PUT", body }); setDraft({}); toast({ ok: "Règles appliquées, sans redémarrage" }); reload(); }
    catch (e) { toast({ error: e.message }); }
    setBusy(false);
  };
  const submit = () => {
    const body = { global: {}, dbs: {} };
    for (const [db, rules] of Object.entries(draft)) {
      for (const [k, v] of Object.entries(rules)) {
        if (db === "_global") body.global[k] = Number(v); else (body.dbs[db] = body.dbs[db] || {})[k] = Number(v);
      }
    }
    apply(body);
  };
  const defaults = () => apply({ dbs: Object.fromEntries(s.dbs.map(db => [db.nom, db.defauts])) });
  const perim = Object.fromEntries(((hist && hist.perimetres) || []).map(p => [p.nom, p]));
  const field = (db, k, cur, step, min, max) => html`<input type="number" class="small" step=${step} min=${min} max=${max}
    value=${val(db, k, cur)} disabled=${!admin} onInput=${e => edit(db, k, e.target.value)} aria-label=${k} />`;
  return html`<section class="tile rules" aria-label="Règles de simulation">
    <div class="th"><h2>Règles de simulation</h2><span class="spacer"></span><${Pill} kind="good">Source : simulateur<//></div>
    <div class="scroll">
      <div class="rule-glob">
        <label>Vieillissement des moteurs ${field("_global", "time_acceleration", s.global.time_acceleration, 1, 1, 3600)} <span class="sub">× (dérive, heures de marche)</span></label>
        <label>Période de mise à jour ${field("_global", "update_interval_s", s.global.update_interval_s, 0.1, 0.2, 10)} <span class="sub">s · ${num(s.total_tags, 0)} tags</span></label>
      </div>
      ${s.dbs.map(db => {
        const p = perim[db.nom];
        return html`<div class="rule-db">
          <div class="rule-h"><b>${db.nom}</b><span class="sub">${STRUCT[db.struct] || db.struct} · ${num(db.instances, 0)} instances${p ? ` · ${rate(p.points_s)} pts/s` : ""}</span><span class="spacer"></span>
            ${p && html`<label class="sub hz">Historisé <${Switch} on=${p.historise && hist.actif} disabled=${!admin || !hist.actif} label=${"Historiser " + db.nom}
              onChange=${on => actions.hist({ dbs: { [db.nom]: on } }, `Historisation ${on ? "reprise" : "suspendue"} pour ${db.nom}`)} /></label>`}</div>
          <div class="rule-grid">${Object.entries(db.regles).map(([k, v]) => html`<label class=${v !== db.defauts[k] ? "changed" : ""}>
            <span>${(RULES[k] || [k])[0]}</span>${field(db.nom, k, v, (RULES[k] || [])[2] || 0.1)}<small class="sub">${(RULES[k] || [])[1] || ""}</small></label>`)}</div>
        </div>`;
      })}
    </div>
    <div class="actions">
      <button type="button" class="primary" disabled=${!admin || !dirty || busy} onClick=${submit}>Appliquer</button>
      <button type="button" disabled=${!dirty} onClick=${() => setDraft({})}>Annuler les modifications</button>
      <button type="button" disabled=${!admin || busy} onClick=${defaults}>Valeurs par défaut</button>
      <span class="sub">${dirty ? "modifié, non appliqué" : "appliqué à chaud, conservé au redémarrage"}</span>
    </div>
  </section>`;
}

function Explorer() {
  const [q, setQ] = useState("M101");
  const [list, setList] = useState([]);
  const [sel, setSel] = useState(null);
  const hist = useRef({});
  const [, force] = useState(0);
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => ops(`/simulation/tags?q=${encodeURIComponent(q)}&limite=40`).then(r => {
      if (!live) return; setList(r); if (r.length && !r.find(x => x.nom === sel)) setSel((r.find(x => typeof x.valeur === "number" && !Number.isInteger(x.valeur)) || r[0]).nom);
    }, () => {}), 250);
    return () => { live = false; clearTimeout(t); };
  }, [q]);
  useEffect(() => {   // valeurs en direct des tags listés, toutes les 2 s ; courbe gardée 5 min
    if (!list.length) return;
    let live = true;
    const poll = () => ops(`/simulation/tags?noms=${encodeURIComponent(list.map(x => x.nom).join(","))}`).then(r => {
      if (!live) return;
      const now = Date.now();
      for (const x of r) {
        const h = hist.current[x.nom] = hist.current[x.nom] || [];
        h.push([now, typeof x.valeur === "boolean" ? (x.valeur ? 1 : 0) : x.valeur]);
        while (h.length && h[0][0] < now - 300000) h.shift();
      }
      setList(r); force(n => n + 1);
    }, () => {});
    poll();
    const t = setInterval(poll, 2000);
    return () => { live = false; clearInterval(t); };
  }, [list.map(x => x.nom).join(",")]);
  const fmt = v => (typeof v === "boolean" ? (v ? "VRAI" : "FAUX") : typeof v === "number" ? num(v, Number.isInteger(v) ? 0 : 2) : String(v));
  const pts = (sel && hist.current[sel]) || [];
  const cur = list.find(x => x.nom === sel);
  return html`<section class="tile explorer" aria-label="Données simulées en direct">
    <div class="th"><h2>Données simulées en direct</h2><span class="spacer"></span>
      <input type="search" placeholder="Rechercher un tag (M101, L3, Puissance…)" value=${q} onInput=${e => setQ(e.target.value)} aria-label="Rechercher un tag" /></div>
    <div class="ex-body">
      <div class="ex-list scroll">${list.map(x => html`<button type="button" class=${"tag" + (x.nom === sel ? " on" : "")} onClick=${() => setSel(x.nom)}>
        <span class="mono">${x.nom}</span><b class="num">${fmt(x.valeur)}</b></button>`)}
        ${!list.length && html`<span class="sub">Aucun tag.</span>`}</div>
      <div class="ex-chart">
        <div class="ex-cur"><span class="mono">${sel || ""}</span><b class="num">${cur ? fmt(cur.valeur) : ""}</b></div>
        <${LiveChart} points=${pts} />
        <span class="sub small">5 dernières minutes, relevé toutes les 2 s (valeurs du simulateur, historisées ou non)</span>
      </div>
    </div>
  </section>`;
}

function LiveChart({ points }) {
  const W = 400, H = 150;
  if (points.length < 2) return html`<div class="lchart empty sub">Courbe en cours de constitution…</div>`;
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const x0 = xs[0], x1 = Math.max(xs[xs.length - 1], x0 + 1);
  let y0 = Math.min(...ys), y1 = Math.max(...ys); if (y1 === y0) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * 0.1; y0 -= pad; y1 += pad;
  const X = x => ((x - x0) / (x1 - x0)) * W, Y = y => H - ((y - y0) / (y1 - y0)) * H;
  return html`<div class="lchart">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Tendance du tag">
      <polyline points=${points.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ")} fill="none" stroke="var(--green)" stroke-width="2" vector-effect="non-scaling-stroke" />
    </svg>
    <span class="ymax num">${num(y1 - pad, 2)}</span><span class="ymin num">${num(y0 + pad, 2)}</span>
    <span class="xmin">${hhmmss(x0)}</span><span class="xmax">${hhmmss(x1)}</span>
  </div>`;
}

// ----------------------------------------------------------------------------- scénarios : vitesse, frise, règles
const SPEEDS = [1, 10, 60, 360, 3600];
const TYPES = [["tous", "Tous"], ["moteur", "Moteur"], ["ligne", "Ligne"], ["compteur_energie", "Énergie"], ["mesure", "Mesure"]];
const TYPE_SHORT = Object.fromEntries(TYPES);
const EFF_ICON = {
  valeur: "M4 9h16M4 15h16", decalage: "M12 5v14M5 12h14", facteur: "M6 6l12 12M18 6 6 18", rampe: "M3 20 21 4",
  bruit: "M2 12h3l2-6 3 12 3-9 2 5 2-2h5", sinus: "M2 12c3-8 6-8 9 0s6 8 9 0", figer: "M12 2v20M4 6l16 12M20 6 4 18",
  arret: "M6 6h12v12H6z", defaut: "M12 3 2 21h20zM12 10v5M12 18v.5", aleas: "M4 4h16v16H4zM9 9h.01M15 15h.01M15 9h.01M9 15h.01",
  qualite: "M12 3 4 6v6c0 5 4 8 8 9 4-1 8-4 8-9V6z", coupure: "M9 7V3M15 7V3M6 7h12v4a6 6 0 0 1-12 0zM12 17v4",
};
const EFF_KIND = { arret: "k-stop", defaut: "k-stop", aleas: "k-stop", qualite: "k-com", coupure: "k-com" };
const PARAM_LABEL = { valeur: "Valeur", facteur: "Facteur ×", pct: "Amplitude %", periode_s: "Période", code: "Code d'arrêt",
  arrets_h: "Arrêts par heure", duree_arret_s: "Durée d'un arrêt" };
const DUR_PARAMS = new Set(["periode_s", "duree_arret_s"]);
const UNITS = [[1, "s"], [60, "min"], [3600, "h"], [86400, "j"]];
const RULE_KEYS = ["effet", "cible", "params", "debut_s", "duree_s", "repeter_s", "proba", "note"];
const PLAY = "M7 4v16l13-8z", STOP = "M6 6h12v12H6z", PAUSE = "M7 5h3v14H7zM14 5h3v14h-3z";

function fmtD(s) {
  s = Math.round(s || 0);
  if (s < 60) return s + " s";
  if (s < 3600) return Math.floor(s / 60) + " min" + (s % 60 ? " " + (s % 60) + " s" : "");
  if (s < 86400) return Math.floor(s / 3600) + " h" + (s % 3600 >= 60 ? " " + String(Math.floor((s % 3600) / 60)).padStart(2, "0") : "");
  return Math.floor(s / 86400) + " j" + (s % 86400 >= 3600 ? " " + Math.floor((s % 86400) / 3600) + " h" : "");
}
const fmtClock = t => new Date(t).toLocaleString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(" ", " · ");
const newId = p => p + "-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
const clean = sc => ({ nom: sc.nom, duree_s: sc.duree_s, boucle: !!sc.boucle,
  regles: (sc.regles || []).map(r => Object.fromEntries(RULE_KEYS.map(k => [k, r[k]]))) });
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function cibleName(c, groups) {
  if (c === null || c === undefined) return "Serveur OPC UA";
  if (typeof c === "string") return (groups[c] && groups[c].nom) || "Groupe supprimé";
  return [TYPE_SHORT[c.type] || "Tous", c.instances || (c.type === "tous" ? "" : "tous"), c.contient && `« ${c.contient} »`,
    (c.membres || []).join(", ")].filter(Boolean).join(" · ");
}

// durée saisie au pavé numérique + unité
function DurInput({ value, onChange, disabled, label }) {
  const [unit, setUnit] = useState(() => [...UNITS].reverse().find(([u]) => value >= u && value % u === 0)?.[0] || 1);
  const n = +(value / unit).toFixed(3);
  return html`<span class="dur-in">
    <input type="number" min="0" step="any" value=${n} disabled=${disabled} aria-label=${label}
      onChange=${e => onChange(Math.max(0, Math.round(Number(e.target.value || 0) * unit)))} />
    <select value=${unit} disabled=${disabled} aria-label=${label + ", unité"}
      onChange=${e => { const u = Number(e.target.value); setUnit(u); onChange(Math.round(n * u)); }}>
      ${UNITS.map(([u, l]) => html`<option value=${u} selected=${u === unit}>${l}</option>`)}</select></span>`;
}

function TimeBar({ s, admin, toast, reload, tab, setTab }) {
  const v = Number(s.global.vitesse ?? 1);
  const base = useRef(null);
  if (!base.current || base.current.h !== s.horloge || base.current.v !== v) base.current = { h: s.horloge, at: Date.now(), v };
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick(n => n + 1), 1000); return () => clearInterval(t); }, []);
  const [other, setOther] = useState(false);
  const last = useRef(v || 60);
  if (v) last.current = v;
  const b = base.current;
  const now = (b.h + ((Date.now() - b.at) / 1000) * b.v) * 1000;
  const setSpeed = async x => {
    try { await ops("/simulation/regles", { method: "PUT", body: { global: { vitesse: x } } }); reload(); } catch (e) { toast({ error: e.message }); }
  };
  const stopAll = async () => { try { await ops("/simulation/scenarios-arreter", { method: "POST", body: {} }); reload(); } catch (e) { toast({ error: e.message }); } };
  const running = s.scenarios.filter(x => x.en_cours).length;
  const custom = v && !SPEEDS.includes(v);
  return html`<div class="timebar">
    <div class="seg light speeds" role="group" aria-label="Vitesse du temps">
      ${SPEEDS.map(x => html`<button type="button" class=${v === x ? "on" : ""} disabled=${!admin} aria-pressed=${v === x} onClick=${() => setSpeed(x)}>×${num(x, 0)}</button>`)}
      ${other ? html`<input type="number" class="small" min="1" max="3600" aria-label="Vitesse" value=${custom ? v : ""} autoFocus
          onChange=${e => { const x = Number(e.target.value); setOther(false); if (x >= 1 && x <= 3600) setSpeed(x); }} onBlur=${() => setOther(false)} />`
        : html`<button type="button" class=${custom ? "on" : ""} disabled=${!admin} onClick=${() => setOther(true)}>${custom ? "×" + num(v, 0) : "Autre"}</button>`}
    </div>
    <button type="button" class=${"round" + (v ? "" : " paused")} disabled=${!admin} aria-label=${v ? "Mettre en pause" : "Reprendre"}
      onClick=${() => setSpeed(v ? 0 : last.current)}><${Icon} d=${v ? PAUSE : PLAY} size="18" /></button>
    <div class="clock"><b class="num">${fmtClock(now)}</b>
      <span class="sub small">${!v ? "en pause" : v === 1 ? "temps réel" : `1 min = ${fmtD(60 * v)}`}</span></div>
    ${running > 0 && html`<${Pill} kind="warn">${running} en cours<//>
      <button type="button" class="sm" disabled=${!admin} onClick=${stopAll}>Tout arrêter</button>`}
    <span class="spacer"></span>
    <div class="seg light">${SIM_TABS.map(([id, l]) => html`<button type="button" class=${tab === id ? "on" : ""} aria-pressed=${tab === id} onClick=${() => setTab(id)}>${l}</button>`)}</div>
  </div>`;
}

function Studio({ s, admin, toast, reload }) {
  const groups = Object.fromEntries(s.groupes.map(g => [g.id, g]));
  const saved = Object.fromEntries(s.scenarios.map(x => [x.id, x]));
  const [sel, setSel] = useState(() => (s.scenarios.find(x => x.en_cours) || s.scenarios[0] || {}).id || null);
  const [draft, setDraft] = useState(null);       // copie en cours d'édition : { id, neuf, ...scénario }
  const [ri, setRi] = useState(null);             // règle sélectionnée
  const [gdlg, setGdlg] = useState(null);         // groupe édité : id, ou "" pour un nouveau
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const cur = saved[sel];
  const view = draft && draft.id === sel ? draft : cur ? { id: sel, ...clean(cur) } : null;
  const dirty = !!draft && draft.id === sel && (draft.neuf || !cur || !same(clean(draft), clean(cur)));
  const edit = patch => setDraft({ ...view, neuf: draft && draft.id === sel ? draft.neuf : false, ...patch });
  const editRule = (i, patch) => edit({ regles: view.regles.map((r, k) => (k === i ? { ...r, ...patch } : r)) });
  const pick = id => { setSel(id); setRi(null); if (draft && draft.id !== id && !draft.neuf) setDraft(null); };
  const call = async (fn, ok) => {
    setBusy(true);
    try { const r = await fn(); if (ok) toast({ ok }); reload(); return r || true; } catch (e) { toast({ error: e.message }); return false; } finally { setBusy(false); }
  };
  const save = async () => {
    const r = await call(() => ops(`/simulation/scenarios/${view.id}`, { method: "PUT", body: clean(view) }), `Scénario « ${view.nom} » enregistré`);
    if (r) setDraft(null);
    return r;
  };
  const launch = async () => {
    if (dirty && !(await save())) return;
    call(() => ops(`/simulation/scenarios/${view.id}/lancer`, { method: "POST", body: {} }), `Scénario « ${view.nom} » lancé`);
  };
  const launchId = (id, on) => call(() => ops(`/simulation/scenarios/${id}/${on ? "arreter" : "lancer"}`, { method: "POST", body: {} }));
  const create = () => {
    const id = newId("s");
    setDraft({ id, neuf: true, nom: "Nouveau scénario", duree_s: 3600, boucle: false, regles: [] });
    setSel(id); setRi(null);
  };
  const duplicate = async () => {
    const id = newId("s");
    if (await call(() => ops(`/simulation/scenarios/${id}`, { method: "PUT", body: { ...clean(view), nom: view.nom + " (copie)" } }), "Scénario dupliqué")) {
      setDraft(null); setSel(id);
    }
  };
  const remove = () => setConfirm({
    title: `Supprimer « ${view.nom} » ?`, text: "Le scénario est arrêté puis supprimé.", ok: "Supprimer",
    onOk: async () => { if (await call(() => ops(`/simulation/scenarios/${view.id}`, { method: "DELETE" }), "Scénario supprimé")) { setDraft(null); setSel(null); } },
  });
  const addRule = () => {
    const D = view.duree_s;
    const r = { effet: "facteur", cible: s.groupes[0] ? s.groupes[0].id : { type: "tous" }, params: { facteur: 1.2 },
      debut_s: 0, duree_s: Math.min(600, D), repeter_s: 0, proba: 1, note: "" };
    edit({ regles: [...view.regles, r] });
    setRi(view.regles.length);
  };
  const list = [...s.scenarios.map(x => ({ id: x.id, nom: x.nom, n: x.regles.length, d: x.duree_s, boucle: x.boucle, run: x.en_cours })),
    ...(draft && draft.neuf ? [{ id: draft.id, nom: draft.nom, n: draft.regles.length, d: draft.duree_s, neuf: true }] : [])];
  const rule = view && ri !== null ? view.regles[ri] : null;
  return html`<div class="studio">
    <div class="lib">
      <section class="tile">
        <div class="th"><h2>Scénarios</h2><span class="spacer"></span>
          <button type="button" class="round sm" aria-label="Nouveau scénario" disabled=${!admin} onClick=${create}><${Icon} d="M12 5v14M5 12h14" /></button></div>
        <div class="scroll lib-list">${list.map(x => html`<div class=${"lib-row" + (x.id === sel ? " on" : "")}>
          <button type="button" class=${"play" + (x.run ? " run" : "")} disabled=${!admin || x.neuf}
            aria-label=${(x.run ? "Arrêter " : "Lancer ") + x.nom} onClick=${() => launchId(x.id, !!x.run)}><${Icon} d=${x.run ? STOP : PLAY} size="12" /></button>
          <button type="button" class="lib-name" onClick=${() => pick(x.id)}>
            <b>${x.nom}</b><span class="sub small">${x.run ? `en cours · ${fmtD(x.run.position_s)}` : `${x.n} règle${x.n > 1 ? "s" : ""} · ${fmtD(x.d)}${x.boucle ? " · boucle" : ""}${x.neuf ? " · non enregistré" : ""}`}</span>
          </button></div>`)}</div>
      </section>
      <section class="tile grow">
        <div class="th"><h2>Groupes de variables</h2><span class="spacer"></span>
          <button type="button" class="round sm" aria-label="Nouveau groupe" disabled=${!admin} onClick=${() => setGdlg("")}><${Icon} d="M12 5v14M5 12h14" /></button></div>
        <div class="scroll lib-list">${s.groupes.map(g => html`<button type="button" class="grp-row" onClick=${() => setGdlg(g.id)}>
          <span class=${"ttag t-" + g.type}>${TYPE_SHORT[g.type] || g.type}</span><span class="gn">${g.nom}</span><span class="sub small num">${num(g.tags, 0)}</span></button>`)}
          ${!s.groupes.length && html`<span class="sub small">Aucun groupe.</span>`}</div>
      </section>
    </div>
    ${view ? html`<section class="tile tl">
      <div class="th tl-head">
        <input class="tl-name" value=${view.nom} disabled=${!admin} aria-label="Nom du scénario" onInput=${e => edit({ nom: e.target.value })} />
        ${cur && cur.en_cours && html`<${Pill} kind="warn">En cours · ${fmtD(cur.en_cours.position_s)} / ${fmtD(cur.duree_s)}<//>`}
        <span class="spacer"></span>
        <label class="inl">Durée <${DurInput} key=${view.id + "d"} value=${view.duree_s} disabled=${!admin} label="Durée du scénario" onChange=${x => edit({ duree_s: Math.max(10, x) })} /></label>
        <label class="inl"><input type="checkbox" checked=${view.boucle} disabled=${!admin} onChange=${e => edit({ boucle: e.target.checked })} /> Boucle</label>
      </div>
      <${Timeline} sc=${view} run=${cur && cur.en_cours} speed=${Number(s.global.vitesse ?? 1)} groups=${groups} saved=${cur} ri=${ri} setRi=${setRi}
        admin=${admin} onRule=${editRule} onAdd=${addRule} />
      <div class="actions">
        ${cur && cur.en_cours
          ? html`<button type="button" class="stop" disabled=${!admin || busy} onClick=${() => launchId(view.id, true)}><${Icon} d=${STOP} size="12" />Arrêter</button>`
          : html`<button type="button" class="primary" disabled=${!admin || busy || !view.regles.length} onClick=${launch}><${Icon} d=${PLAY} size="12" />Lancer</button>`}
        ${dirty && html`<button type="button" class="dark" disabled=${!admin || busy} onClick=${save}>Enregistrer</button>
          <button type="button" disabled=${busy} onClick=${() => { if (draft.neuf) { setSel(s.scenarios[0] ? s.scenarios[0].id : null); } setDraft(null); setRi(null); }}>Annuler</button>`}
        <span class="spacer"></span>
        <button type="button" disabled=${!admin || busy || !cur} onClick=${duplicate}>Dupliquer</button>
        <button type="button" class="danger" disabled=${!admin || busy || !cur} onClick=${remove}>Supprimer</button>
      </div>
    </section>` : html`<section class="tile tl empty"><p class="sub">Aucun scénario.</p></section>`}
    <section class="tile rp">${rule
      ? html`<${RulePanel} key=${view.id + ":" + ri} r=${rule} s=${s} groups=${groups} admin=${admin} maxD=${view.duree_s}
          onChange=${p => editRule(ri, p)} onClose=${() => setRi(null)}
          onDup=${() => { edit({ regles: [...view.regles.slice(0, ri + 1), { ...rule }, ...view.regles.slice(ri + 1)] }); setRi(ri + 1); }}
          onDelete=${() => { edit({ regles: view.regles.filter((_, k) => k !== ri) }); setRi(null); }} />`
      : html`<div class="rp-empty sub"><${Icon} d="M3 12h4l3-8 4 16 3-8h4" size="28" /><span>Choisir une règle sur la frise</span></div>`}</section>
    ${gdlg !== null && html`<${GroupDialog} id=${gdlg} g=${groups[gdlg]} s=${s} admin=${admin} toast=${toast} reload=${reload} onClose=${() => setGdlg(null)} />`}
    ${confirm && html`<${Confirm} ...${confirm} onClose=${() => setConfirm(null)} />`}
  </div>`;
}

function niceStep(D, n = 8) {
  const steps = [5, 10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];
  return steps.find(x => D / x <= n) || steps[steps.length - 1];
}

function Timeline({ sc, run, speed, groups, saved, ri, setRi, admin, onRule, onAdd }) {
  const D = sc.duree_s;
  const step = niceStep(D);
  const snap = Math.max(1, niceStep(D, 120));
  const drag = useRef(null);
  const at = useRef({ run: null, t: 0 });
  if (at.current.run !== run) at.current = { run, t: Date.now() };
  const [, tick] = useState(0);
  useEffect(() => { if (!run) return; const t = setInterval(() => tick(n => n + 1), 1000); return () => clearInterval(t); }, [!!run]);
  let pos = null;
  if (run) {
    pos = run.position_s + ((Date.now() - at.current.t) / 1000) * speed;
    pos = sc.boucle ? pos % D : Math.min(pos, D);
  }
  const ticks = [];
  for (let t = 0; t <= D + 0.1; t += step) ticks.push(t);
  const pctX = t => (Math.max(0, Math.min(D, t)) / D) * 100 + "%";
  const startDrag = (e, i, mode) => {
    if (!admin) return;
    e.preventDefault(); e.stopPropagation();
    const track = e.currentTarget.closest(".lane-track");
    e.currentTarget.setPointerCapture && e.currentTarget.setPointerCapture(e.pointerId);
    const r = sc.regles[i];
    drag.current = { i, mode, x0: e.clientX, w: track.getBoundingClientRect().width, debut: r.debut_s, duree: r.duree_s, rep: r.repeter_s, moved: false };
    setRi(i);
  };
  const move = e => {
    const g = drag.current;
    if (!g) return;
    const ds = Math.round((((e.clientX - g.x0) / g.w) * D) / snap) * snap;
    if (ds) g.moved = true;
    if (g.mode === "move") onRule(g.i, { debut_s: Math.max(0, Math.min(D - snap, g.debut + ds)) });
    else onRule(g.i, { duree_s: Math.max(snap, Math.min(g.rep ? g.rep - 1 : D, g.duree + ds)) });
  };
  const end = () => { drag.current = null; };
  const count = (r, i) => {
    if (typeof r.cible === "string") return groups[r.cible] ? groups[r.cible].tags : null;
    const sr = saved && saved.regles[i];
    return sr && same(sr.cible, r.cible) ? sr.tags : null;
  };
  return html`<div class="tl-body" onPointerMove=${move} onPointerUp=${end} onPointerCancel=${end}>
    <div class="lane scale"><span></span><div class="lane-ticks">${ticks.map((t, k) => html`<span style=${{ left: pctX(t), transform: `translateX(${k === 0 ? "0" : t >= D ? "-100%" : "-50%"})` }}>${fmtD(t)}</span>`)}</div></div>
    <div class="lanes scroll">
      ${sc.regles.map((r, i) => {
        const occ = [];
        for (let k = 0; k < 200; k++) {
          const a = r.debut_s + k * (r.repeter_s || 0);
          if (a >= D || (k > 0 && !r.repeter_s)) break;
          occ.push(a);
        }
        const n = count(r, i);
        const active = run && run.actives && run.actives.includes(i);
        return html`<div class=${"lane" + (ri === i ? " sel" : "")}>
          <button type="button" class="lane-l" onClick=${() => setRi(i)}>
            <b>${cibleName(r.cible, groups)}</b><span class="sub small">${n !== null && n !== undefined ? num(n, 0) + (n > 1 ? " tags" : " tag") : r.cible === null ? "global" : ""}${r.proba < 1 ? ` · ${num(r.proba * 100, 0)} %` : ""}</span></button>
          <div class="lane-track">${occ.map((a, k) => html`<button type="button" class=${"bar " + (EFF_KIND[r.effet] || "k-val") + (ri === i ? " sel" : "") + (active ? " live" : "")}
              style=${{ left: pctX(a), width: `calc(${(Math.min(r.duree_s, D - a) / D) * 100}% - 2px)` }}
              aria-label=${`${(s_effLabel(r.effet))} de ${fmtD(a)} à ${fmtD(a + r.duree_s)}`}
              onPointerDown=${e => startDrag(e, i, "move")} onClick=${() => setRi(i)}>
              <${Icon} d=${EFF_ICON[r.effet]} size="13" /><span>${barLabel(r)}</span>
              ${k === 0 && admin && html`<i class="grip" onPointerDown=${e => startDrag(e, i, "size")}></i>`}</button>`)}</div>
        </div>`;
      })}
      <div class="lane"><span></span><button type="button" class="add-rule" disabled=${!admin} onClick=${onAdd}>+ Ajouter une règle</button></div>
      ${pos !== null && html`<div class="playhead" style=${{ left: `calc(var(--lane-l) + (100% - var(--lane-l)) * ${pos / D})` }}><span>${fmtD(pos)}</span></div>`}
    </div>
  </div>`;
}

const EFF_LABEL = { valeur: "Valeur", decalage: "Décalage", facteur: "Facteur", rampe: "Rampe", bruit: "Bruit", sinus: "Sinus", figer: "Figer",
  arret: "Arrêt", defaut: "Défaut", aleas: "Aléas", qualite: "Qualité", coupure: "Coupure" };
const s_effLabel = e => EFF_LABEL[e] || e;
function barLabel(r) {
  const p = r.params || {};
  switch (r.effet) {
    case "valeur": return `= ${num(p.valeur, 2)}`;
    case "decalage": return `${p.valeur >= 0 ? "+" : ""}${num(p.valeur, 2)}`;
    case "facteur": return `× ${num(p.facteur, 2)}`;
    case "rampe": return `Rampe ${p.pct >= 0 ? "+" : ""}${num(p.pct, 0)} %`;
    case "bruit": return `Bruit ±${num(p.pct, 0)} %`;
    case "sinus": return `Sinus ±${num(p.pct, 0)} %`;
    case "arret": return `Arrêt · ${p.code}`;
    case "defaut": return `Défaut · ${p.code}`;
    case "aleas": return `${num(p.arrets_h, 0)} arrêts/h`;
    case "qualite": return "Qualité mauvaise";
    default: return s_effLabel(r.effet);
  }
}

function RulePanel({ r, s, groups, admin, maxD, onChange, onClose, onDup, onDelete }) {
  const eff = s.effets.find(e => e.id === r.effet) || s.effets[0];
  const setEffect = id => {
    const e = s.effets.find(x => x.id === id);
    const params = Object.fromEntries(Object.entries(e.params).map(([k, v]) => [k, r.params && r.params[k] !== undefined ? r.params[k] : v[0]]));
    const patch = { effet: id, params };
    if (e.portee === "global") patch.cible = null;
    else if (r.cible === null) patch.cible = s.groupes[0] ? s.groupes[0].id : { type: "tous" };
    onChange(patch);
  };
  const direct = r.cible !== null && typeof r.cible === "object";
  return html`<div class="rp-in">
    <div class="th"><h2>Règle</h2><span class="spacer"></span>
      <button type="button" class="round sm" aria-label="Dupliquer la règle" disabled=${!admin} onClick=${onDup}><${Icon} d="M8 8h12v12H8zM4 16V4h12" /></button>
      <button type="button" class="round sm danger" aria-label="Supprimer la règle" disabled=${!admin} onClick=${onDelete}><${Icon} d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></button>
      <button type="button" class="round sm" aria-label="Fermer" onClick=${onClose}><${Icon} d="M18 6 6 18M6 6l12 12" /></button></div>
    <div class="scroll rp-body">
      <div class="effs" role="group" aria-label="Effet">${s.effets.map(e => html`<button type="button" class=${"eff " + (EFF_KIND[e.id] || "k-val") + (e.id === r.effet ? " on" : "")}
        disabled=${!admin} aria-pressed=${e.id === r.effet} onClick=${() => setEffect(e.id)}><${Icon} d=${EFF_ICON[e.id]} size="17" /><span>${EFF_LABEL[e.id] || e.label}</span></button>`)}</div>
      ${eff.portee !== "global" && html`<div class="fld"><span class="cap">Cible</span>
        <select disabled=${!admin} aria-label="Cible" onChange=${e => onChange({ cible: e.target.value === "" ? { type: "tous" } : e.target.value })}>
          ${s.groupes.map(g => html`<option value=${g.id} selected=${r.cible === g.id}>${g.nom} · ${num(g.tags, 0)} tags</option>`)}
          <option value="" selected=${direct}>Sélection directe…</option></select>
        ${direct && html`<${SelEditor} sel=${r.cible} types=${s.types} admin=${admin} onChange=${c => onChange({ cible: c })} />`}
        ${eff.portee === "instance" && html`<span class="sub small">Agit sur les moteurs et les lignes ciblés.</span>`}</div>`}
      ${Object.keys(eff.params).length > 0 && html`<div class="fld"><span class="cap">Réglage</span><div class="grid2">
        ${Object.entries(eff.params).map(([k, [, lo, hi]]) => html`<label class="f">${PARAM_LABEL[k] || k}
          ${DUR_PARAMS.has(k)
            ? html`<${DurInput} value=${r.params[k]} disabled=${!admin} label=${PARAM_LABEL[k]} onChange=${x => onChange({ params: { ...r.params, [k]: Math.min(hi, Math.max(lo, x)) } })} />`
            : html`<input type="number" step="any" min=${lo} max=${hi} value=${r.params[k]} disabled=${!admin}
                onChange=${e => onChange({ params: { ...r.params, [k]: Number(e.target.value) } })} />`}</label>`)}</div></div>`}
      <div class="fld"><span class="cap">Quand</span><div class="grid2">
        <label class="f">Début<${DurInput} value=${r.debut_s} disabled=${!admin} label="Début" onChange=${x => onChange({ debut_s: Math.min(maxD - 1, x) })} /></label>
        <label class="f">Durée<${DurInput} value=${r.duree_s} disabled=${!admin} label="Durée" onChange=${x => onChange({ duree_s: Math.max(1, x) })} /></label>
        <label class="f">Répéter toutes les<${DurInput} value=${r.repeter_s} disabled=${!admin} label="Répéter toutes les" onChange=${x => onChange({ repeter_s: x })} /></label>
        <label class="f">Probabilité %<input type="number" min="0" max="100" step="1" value=${Math.round(r.proba * 100)} disabled=${!admin}
          onChange=${e => onChange({ proba: Math.min(1, Math.max(0, Number(e.target.value) / 100)) })} /></label>
      </div></div>
    </div>
  </div>`;
}

// sélection de variables : type, instances, morceau de nom, membres ; aperçu du nombre de tags
function SelEditor({ sel, types, admin, onChange }) {
  const prev = usePreview(sel, admin);
  const members = sel.type === "tous" ? [...new Set(Object.values(types).flat())] : types[sel.type] || [];
  const toggle = m => onChange({ ...sel, membres: (sel.membres || []).includes(m) ? sel.membres.filter(x => x !== m) : [...(sel.membres || []), m] });
  return html`<div class="sel-ed">
    <div class="chips">${TYPES.map(([id, l]) => html`<button type="button" class=${"chip" + (sel.type === id ? " on" : "")} disabled=${!admin}
      onClick=${() => onChange({ ...sel, type: id, membres: [] })}>${l}</button>`)}</div>
    <div class="grid2">
      <label class="f">Instances<input value=${sel.instances || ""} placeholder="M101-M120, L*" disabled=${!admin} onInput=${e => onChange({ ...sel, instances: e.target.value })} /></label>
      <label class="f">Contient<input value=${sel.contient || ""} placeholder="DB_Process_02" disabled=${!admin} onInput=${e => onChange({ ...sel, contient: e.target.value })} /></label>
    </div>
    <div class="chips">${members.map(m => html`<button type="button" class=${"chip" + ((sel.membres || []).includes(m) ? " on" : "")} disabled=${!admin} onClick=${() => toggle(m)}>${m}</button>`)}</div>
    <div class="prev">${prev ? html`<b class="num">${num(prev.tags, 0)}</b> tags · ${num(prev.n_instances, 0)} instances
      <div class="mono small prev-ex">${prev.exemples.slice(0, 6).map(x => html`<span>${x}</span>`)}${prev.tags > 6 ? html`<span>…</span>` : ""}</div>` : html`<span class="sub small">${prev === false ? "Sélection invalide" : "…"}</span>`}</div>
  </div>`;
}

function usePreview(sel, admin) {
  const [r, setR] = useState(null);
  const key = JSON.stringify(sel);
  useEffect(() => {
    if (!admin) return;
    let live = true;
    const t = setTimeout(() => ops("/simulation/apercu", { method: "POST", body: sel }).then(x => live && setR(x), () => live && setR(false)), 300);
    return () => { live = false; clearTimeout(t); };
  }, [key]);
  return r;
}

function GroupDialog({ id, g, s, admin, toast, reload, onClose }) {
  const [v, setV] = useState(() => g ? { nom: g.nom, type: g.type, dbs: g.dbs, instances: g.instances, contient: g.contient, membres: g.membres }
    : { nom: "", type: "moteur", instances: "", contient: "", membres: [] });
  const [err, setErr] = useState(null);
  const [ask, setAsk] = useState(false);
  const users = id ? s.scenarios.filter(x => x.regles.some(r => r.cible === id)).map(x => x.nom) : [];
  const save = async () => {
    try {
      await ops(`/simulation/groupes/${id || newId("g")}`, { method: "PUT", body: v });
      toast({ ok: `Groupe « ${v.nom} » enregistré` }); reload(); onClose();
    } catch (e) { setErr(e.message); }
  };
  const del = async () => {
    try { await ops(`/simulation/groupes/${id}`, { method: "DELETE" }); toast({ ok: "Groupe supprimé" }); reload(); onClose(); }
    catch (e) { setErr(e.message); }
  };
  return html`<${Dialog} title=${g ? "Groupe de variables" : "Nouveau groupe"} onClose=${onClose}>
    <div class="grp-dlg">
      <label class="f">Nom<input value=${v.nom} disabled=${!admin} onInput=${e => setV({ ...v, nom: e.target.value })} /></label>
      <${SelEditor} sel=${v} types=${s.types} admin=${admin} onChange=${x => setV({ ...x, nom: v.nom })} />
      ${err && html`<div class="err">${err}</div>`}
      ${ask && html`<div class="err">Supprimer ce groupe ? <button type="button" class="sm stop" onClick=${del}>Supprimer</button></div>`}
      <div class="row-end">
        ${g && html`<button type="button" class="danger" disabled=${!admin || users.length > 0} title=${users.length ? "Utilisé par : " + users.join(", ") : ""}
          onClick=${() => setAsk(true)}>Supprimer</button>`}
        <span class="spacer"></span>
        <button type="button" onClick=${onClose}>Annuler</button>
        <button type="button" class="dark" disabled=${!admin || !v.nom.trim()} onClick=${save}>Enregistrer</button>
      </div>
      ${users.length > 0 && html`<span class="sub small">Utilisé par ${users.join(", ")}</span>`}
    </div>
  <//>`;
}

// ----------------------------------------------------------------------------- journaux complets
function Logs({ s, onClose }) {
  const [n, setN] = useState(300);
  const [q, setQ] = useState("");
  const r = usePoll(`/services/${s.service}/journaux?lignes=${n}`, 5000);
  const box = useRef();
  const lines = (r.data ? r.data.lignes : []).filter(l => !q || l.toLowerCase().includes(q.toLowerCase()));
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [r.data]);
  return html`<div class="dialog-back" onClick=${e => e.target === e.currentTarget && onClose()}>
    <div class="dialog logs-dlg" role="dialog" aria-label=${"Journaux de " + s.label}>
      <div class="th"><h3 style="margin:0">Journaux · ${s.label}</h3><span class="spacer"></span>
        <input type="search" placeholder="Filtrer" value=${q} onInput=${e => setQ(e.target.value)} aria-label="Filtrer les lignes" />
        <select value=${n} onChange=${e => setN(Number(e.target.value))} aria-label="Nombre de lignes">${[100, 300, 1000, 2000].map(x => html`<option value=${x} selected=${x === n}>${x} lignes</option>`)}</select>
        <button type="button" onClick=${onClose} aria-label="Fermer"><${Icon} d="M18 6 6 18M6 6l12 12" /></button></div>
      <div class="logs" ref=${box}>${r.error ? html`<span class="bad-tx">${r.error}</span>` : lines.map(l => html`<div class=${/ERROR|CRITICAL|Traceback/.test(l) ? "bad-tx" : /WARNING/.test(l) ? "warn-tx" : ""}>${logLine(l)}</div>`)}</div>
      <span class="sub small">Actualisé toutes les 5 s · heures en UTC</span>
    </div></div>`;
}

// ----------------------------------------------------------------------------- coquille
const VIEWS = [
  ["architecture", "Architecture", "M4 6h6v4H4zM14 6h6v4h-6zM9 16h6v4H9zM7 10v3h10v-3M12 13v3"],
  ["services", "Services et commandes", "M4 5h16v4H4zM4 15h16v4H4zM8 7h.01M8 17h.01"],
  ["simulation", "Simulation", "M3 12h4l3-8 4 16 3-8h4"],
  ["grafana", "Supervision Grafana", "M4 19V9M10 19V5M16 19v-7M22 19H2"],
];

function GrafanaView() {
  const src = "/grafana/d/mes-systeme/systeme?" + new URLSearchParams({ orgId: "1", from: "now-1h", to: "now", refresh: "10s" }) + "&kiosk";
  return html`<section class="tile" style="flex:1;padding:0">
    <iframe id="dash" title="Tableau de bord Système" src=${src} style="flex:1;border:0;width:100%;height:100%;border-radius:var(--radius)"></iframe>
    <p class="sub small" style="margin:6px 12px">Le tableau Grafana « Système » nécessite le profil de surveillance (./mesctl monitoring on) et Grafana en service. <a href="/grafana/d/mes-systeme" target="_blank" rel="noopener">Ouvrir dans Grafana ↗</a></p>
  </section>`;
}

function App() {
  const initial = (location.hash || "").slice(1);
  const [view, setView] = useState(VIEWS.some(v => v[0] === initial) ? initial : "architecture");
  const st = usePoll("/etat", 5000);
  const [confirm, setConfirm] = useState(null);
  const [logs, setLogs] = useState(null);
  const [msg, setMsg] = useState(null);
  const toast = m => { setMsg(m); clearTimeout(toast.t); toast.t = setTimeout(() => setMsg(null), 6000); };
  const actions = useActions(st.reload, toast);
  useEffect(() => { history.replaceState(null, "", "#" + view); }, [view]);
  useEffect(() => {
    const f = () => { const h = location.hash.slice(1); if (VIEWS.some(v => v[0] === h)) setView(h); };
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  // Agent Mac (Colima) : lien affiché seulement si l'agent répond sur ce poste
  const [posteMac, setPosteMac] = useState(null);
  useEffect(() => {
    fetch("http://localhost:9300/api/ping").then(r => r.json()).then(j => setPosteMac(j.url)).catch(() => {});
  }, []);
  const d = st.data;
  const admin = !!(d && d.user.admin);
  const title = VIEWS.find(v => v[0] === view)[1].split(" ");
  const hist = d && d.collecteur && d.collecteur.historisation;
  const logout = async () => { await fetch("/api/session", { method: "DELETE" }); location.reload(); };
  return html`<div class="layout">
    <nav class="side" aria-label="Console d'exploitation">
      <div class="brand"><span class="logo"><${Icon} d="M4 12h4l2-6 4 12 2-6h4" size="18" /></span>Exploitation</div>
      ${VIEWS.map(([id, label, icon]) => html`<a href=${"#" + id} class=${view === id ? "on" : ""} aria-current=${view === id ? "page" : undefined}
        onClick=${e => { e.preventDefault(); setView(id); }}><${Icon} d=${icon} />${label}</a>`)}
      <div class="grp">Liens</div>
      <a href="/"><${Icon} d="M3 11l9-7 9 7v9H3z" />Application MES</a>
      ${posteMac && html`<a href=${posteMac} target="_blank" rel="noopener"><${Icon} d="M4 5h16v11H4zM8 20h8M12 16v4" />Poste Mac (Colima)</a>`}
      <div class="who">${d ? html`${d.user.display_name || d.user.username}<br />${d.user.auth === false ? "Accès sans identifiant" : html`${admin ? "Actions autorisées" : "Lecture seule"} · <a href="#" onClick=${e => { e.preventDefault(); logout(); }}>Déconnexion</a>`}` : ""}</div>
    </nav>
    <main class="fit">
      <${Band} over="Console d'exploitation · MES v3" title=${title[0]} accent=${title.slice(1).join(" ")}>
        ${d && html`
          <span class=${"live " + (d.application === "en_service" ? "" : d.application === "arretee" ? "off" : "late")}>Application ${{ en_service: "en service", arretee: "arrêtée", partielle: "partielle" }[d.application]}</span>
          ${hist && html`<span class=${"live " + (hist.actif && !hist.dbs_suspendus.length ? "" : "late")}>Historisation ${hist.actif ? (hist.dbs_suspendus.length ? "partielle" : "active") : "suspendue"}</span>`}
          ${d.application === "en_service"
            ? html`<button type="button" class="stop" disabled=${!admin} onClick=${() => setConfirm({ title: "Arrêter l'application ?", text: "La collecte s'arrête et l'application MES devient indisponible pour les utilisateurs. La base, le web et cette console restent en marche.", ok: "Arrêter", onOk: () => actions.app("stop") })}>Arrêter l'application</button>`
            : html`<button type="button" class="primary" disabled=${!admin} onClick=${() => actions.app("start")}>Démarrer l'application</button>`}`}
      <//>
      ${msg && html`<div class=${"toast " + (msg.error ? "bad" : "good")} role="status">${msg.error || msg.ok}</div>`}
      <div class="view">
        ${!d ? html`<section class="tile"><p>${st.error ? "Agent d'exploitation injoignable : " + st.error : "Chargement…"}</p></section>`
        : view === "architecture" ? html`<${Architecture} d=${d} actions=${actions} admin=${admin} setConfirm=${setConfirm} openLogs=${setLogs} />`
        : view === "services" ? html`<${Services} d=${d} actions=${actions} admin=${admin} setConfirm=${setConfirm} openLogs=${setLogs} toast=${toast} />`
        : view === "simulation" ? html`<${Simulation} d=${d} actions=${actions} admin=${admin} toast=${toast} />`
        : html`<${GrafanaView} />`}
      </div>
    </main>
    ${confirm && html`<${Confirm} ...${confirm} onClose=${() => setConfirm(null)} />`}
    ${logs && html`<${Logs} s=${logs} onClose=${() => setLogs(null)} />`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
