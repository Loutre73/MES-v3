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
function Services({ d, actions, admin, setConfirm, openLogs }) {
  const journal = usePoll("/journal?n=60", 10000);
  const col = d.collecteur, hist = col && col.historisation;
  const app = d.application;
  const layers = ["Acquisition", "Stockage", "Application", "Observabilité", "Autres"];
  const vols = d.volumes.items || [];
  const volTotal = vols.reduce((a, v) => a + Math.max(0, v.bytes), 0);
  const VOL = { "db-data": "Base (historian, MES)", "prometheus-data": "Prometheus", "grafana-data": "Grafana", "collector-spool": "Tampon du collecteur", "ops-data": "Console", "sim-data": "Simulateur" };
  const VCOL = ["#2F5D8A", "#82A1CD", "#7FD3E3", "#06D16F", "#A9BDD6", "#5B7FA8"];
  const suspend = (min, label) => actions.hist({ actif: false, duree_min: min }, "Historisation suspendue " + label);
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
      </section>
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

function Simulation({ d, actions, admin, toast }) {
  const sim = usePoll(d.simulateur ? "/simulation/etat" : null, 5000);
  const col = d.collecteur, hist = col && col.historisation;
  if (!d.simulateur) {
    const sv = d.services.find(s => s.service === "simulator");
    return html`<section class="tile" style="flex:1"><div class="th"><h2>Simulation</h2></div>
      <p>${sv ? "Le simulateur est arrêté : démarrez-le depuis l'onglet Services pour régler ses règles." :
        "Source de données : automate réel. Le simulateur n'est pas installé sur ce site (profil « sim » non activé)."}</p>
      ${hist && html`<${Perimeters} hist=${hist} actions=${actions} admin=${admin} />`}</section>`;
  }
  const s = sim.data || d.simulateur;
  return html`<div class="sim">
    <${Rules} s=${s} hist=${hist} actions=${actions} admin=${admin} toast=${toast} reload=${sim.reload} />
    <div class="sim-right">
      <${Explorer} />
      <${Scenarios} s=${s} admin=${admin} toast=${toast} reload=${sim.reload} />
    </div>
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
        <label>Accélération du temps ${field("_global", "time_acceleration", s.global.time_acceleration, 1, 1, 3600)} <span class="sub">× (dérive, heures moteur)</span></label>
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

const SC_HELP = {
  panne_ligne: "La ligne s'arrête sur défaut (code 1) puis redémarre",
  micro_arrets: "20 s d'arrêt toutes les 2 minutes",
  defaut_moteur: "Moteur à l'arrêt, défaut actif",
  derive_moteur: "Courant qui monte progressivement",
  pic_conso: "Puissance de tous les compteurs augmentée",
  coupure_opcua: "Le serveur OPC UA s'arrête : teste la reprise du collecteur",
};

function Scenarios({ s, admin, toast, reload }) {
  const [targets, setTargets] = useState({});
  const go = async (sc, action) => {
    const body = { action };
    if (sc.cible_type) body.cible = targets[sc.id] || sc.cibles[0];
    try {
      const r = await ops(`/simulation/scenarios/${sc.id}`, { method: "POST", body });
      toast({ ok: `Scénario « ${sc.label} » ${action === "stop" ? "arrêté" : "lancé" + (r.cible ? " sur " + r.cible : "")}` });
    } catch (e) { toast({ error: e.message }); }
    reload();
  };
  const param = sc => {
    const p = sc.parametres;
    return [p.duree_min && `${p.duree_min} min`, p.duree_s && `${p.duree_s} s`, p.pct && `+${p.pct} %`].filter(Boolean).join(" · ");
  };
  return html`<section class="tile scen" aria-label="Scénarios">
    <div class="th"><h2>Injecter un scénario</h2><span class="sub">pour tester arrêts, alarmes, dérives et reprise de la collecte</span></div>
    <div class="scen-grid">${s.scenarios.map(sc => {
      const on = sc.en_cours;
      return html`<div class=${"sc" + (on ? " on" : "")}>
        <b>${sc.label}</b><span class="sub small">${SC_HELP[sc.id] || ""} · ${param(sc)}</span>
        ${on ? html`<span class="small warn-tx">En cours${on.cible ? " sur " + on.cible : ""} · fin à ${hhmm(on.fin * 1000)}</span>
            <button type="button" class="sm" disabled=${!admin} onClick=${() => go(sc, "stop")}>Arrêter</button>`
          : html`<div class="sc-act">${sc.cible_type && html`<select class="sm" value=${targets[sc.id] || sc.cibles[0]} disabled=${!admin}
              onChange=${e => setTargets(t => ({ ...t, [sc.id]: e.target.value }))} aria-label="Cible">${sc.cibles.map(c => html`<option value=${c} selected=${c === (targets[sc.id] || sc.cibles[0])}>${c}</option>`)}</select>`}
            <button type="button" class="sm primary" disabled=${!admin} onClick=${() => go(sc, "start")}>Lancer</button></div>`}
      </div>`;
    })}</div>
  </section>`;
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
        : view === "services" ? html`<${Services} d=${d} actions=${actions} admin=${admin} setConfirm=${setConfirm} openLogs=${setLogs} />`
        : view === "simulation" ? html`<${Simulation} d=${d} actions=${actions} admin=${admin} toast=${toast} />`
        : html`<${GrafanaView} />`}
      </div>
    </main>
    ${confirm && html`<${Confirm} ...${confirm} onClose=${() => setConfirm(null)} />`}
    ${logs && html`<${Logs} s=${logs} onClose=${() => setLogs(null)} />`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
