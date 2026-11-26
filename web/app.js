// Interface MES v3 : Preact + htm, sans étape de compilation (fonctionne hors ligne).
import { html, render, useState, useEffect } from "./vendor/preact-htm.module.js";
import {
  api, useApi, useAction, Msg, Dialog, Chart, dt, hm, num, pct, dur, dhms, toLocalInput, fromLocalInput, ROLE_LABEL, can,
  Band, Icon, ICON, Info, Pill, ThemeToggle, lineState, useTheme,
} from "./lib.js";
import { Synthese } from "./synthese.js";
import { Ligne } from "./ligne.js";
import { Planning } from "./planning.js";
import "./touch.js";

// ----------------------------------------------------------------------------- connexion
function Login({ onLogin }) {
  const [u, setU] = useState(""), [p, setP] = useState("");
  const a = useAction();
  const submit = async e => {
    e.preventDefault();
    const r = await a.run(() => api("/session", { method: "POST", body: { username: u, password: p } }));
    if (r) onLogin(r);
  };
  return html`<div class="login"><form class="panel" onSubmit=${submit}>
    <h1>MES <span>v3</span></h1>
    <${Msg} ...${a.msg} />
    <label class="f">Identifiant (compte Windows ou local)<input value=${u} onInput=${e => setU(e.target.value)} autofocus autocomplete="username" /></label>
    <label class="f">Mot de passe<input type="password" value=${p} onInput=${e => setP(e.target.value)} autocomplete="current-password" /></label>
    <button class="primary" disabled=${a.busy || !u || !p}>Se connecter</button>
  </form></div>`;
}

// ----------------------------------------------------------------------------- arrêts
function Stops({ user }) {
  const [onlyTodo, setTodo] = useState(true);
  const [days, setDays] = useState(7);
  const list = useApi(`/arrets?a_qualifier=${onlyTodo}&jours=${days}`, [], 15000);
  const causes = useApi("/arrets/causes");
  const par = useApi(`/arrets/pareto?jours=${days}`, [list.data]);
  const [qual, setQual] = useState(null), [split, setSplit] = useState(null);
  const a = useAction();
  const reload = () => { list.reload(); par.reload(); };
  const maxMin = Math.max(1, ...(par.data || []).map(p => p.minutes));
  const PER = [[1, "24 h"], [7, "7 j"], [31, "31 j"]];
  return html`<h1>Arrêts</h1>
    <${Msg} ...${a.msg} />
    <div class="row" style="margin-bottom:12px">
      <div class="tabs" style="margin:0;border:none">
        <button class=${onlyTodo ? "on" : ""} onClick=${() => setTodo(true)}>À qualifier</button>
        <button class=${!onlyTodo ? "on" : ""} onClick=${() => setTodo(false)}>Tous</button>
      </div><span class="spacer"></span>
      <div class="seg light" role="group" aria-label="Période">${PER.map(([v, l]) => html`<button type="button" class=${days === v ? "on" : ""} onClick=${() => setDays(v)}>${l}</button>`)}</div>
    </div>
    <${Msg} error=${list.error} />
    <div class="table-wrap"><table>
      <thead><tr><th>Ligne</th><th>Début</th><th>Fin</th><th class="num">Durée</th><th>Code</th><th>Cause</th><th>Commentaire</th><th></th></tr></thead>
      <tbody>${(list.data || []).map(s => html`<tr>
        <td>${s.machine}</td><td>${dt(s.starts_at)}</td><td>${s.ends_at ? dt(s.ends_at) : html`<span class="badge bad">en cours</span>`}</td>
        <td class="num">${dur(s.duree_s)}</td><td>${s.plc_code ?? ""}</td>
        <td>${s.planned ? html`<span class="badge idle">planifié</span>` : s.cause ? html`${s.cause} <span class="muted">(${s.qualified_by})</span>` : html`<span class="badge warn">à qualifier</span>`}</td>
        <td>${s.comment || ""}</td>
        <td class="row" style="flex-wrap:nowrap"><button onClick=${() => setQual(s)}>Qualifier</button>
          ${can(user, "chef_equipe", "operateur") && s.ends_at && html`<button class="iconbtn" onClick=${() => setSplit(s)} title="Découper en deux" aria-label="Découper en deux"><${Icon} d=${ICON.cut} /></button>`}</td>
      </tr>`)}</tbody></table></div>
    ${list.data && !list.data.length && html`<p class="muted">Aucun arrêt.</p>`}
    <h2 class="row">Pareto <${Info}>Arrêts non planifiés, durée cumulée par cause (jj:hh:mm:ss).</${Info}></h2>
    <div class="panel" style="display:grid;gap:10px">${(par.data || []).map(p => html`<div class="hbar">
      <span>${p.cause}</span><span class="num"><b>${dhms(p.minutes * 60)}</b> <span class="muted">· ${p.nombre}</span></span>
      <div class="track"><div class=${p.category === "non_qualifie" ? "warn" : ""} style=${{ width: (p.minutes / maxMin) * 100 + "%" }}></div></div></div>`)}
      ${par.data && !par.data.length && html`<span class="muted">Aucun arrêt.</span>`}</div>
    ${qual && html`<${QualifyDialog} stop=${qual} causes=${causes.data || []} onClose=${() => setQual(null)}
        onDone=${() => { setQual(null); a.setMsg({ ok: "Arrêt qualifié." }); reload(); }} />`}
    ${split && html`<${SplitDialog} stop=${split} onClose=${() => setSplit(null)}
        onDone=${() => { setSplit(null); a.setMsg({ ok: "Arrêt découpé : qualifiez les deux parties." }); setTodo(false); reload(); }} />`}`;
}

function QualifyDialog({ stop, causes, onClose, onDone }) {
  const [cause, setCause] = useState(stop.cause_id || ""), [comment, setComment] = useState(stop.comment || "");
  const a = useAction();
  const cats = [...new Set(causes.map(c => c.category))];
  const save = async () => { if (await a.run(() => api(`/arrets/${stop.id}/qualifier`, { method: "POST", body: { cause_id: +cause, comment: comment || null } }))) onDone(); };
  return html`<${Dialog} title=${`${stop.machine} · ${dt(stop.starts_at)} · ${dur(stop.duree_s)}`} onClose=${onClose}>
    <${Msg} ...${a.msg} />
    <div style="display:grid;gap:12px">
      <label class="f">Cause<select value=${cause} onChange=${e => setCause(e.target.value)}>
        <option value="">Choisir…</option>
        ${cats.map(c => html`<optgroup label=${c}>${causes.filter(x => x.category === c).map(x => html`<option value=${x.id}>${x.label}</option>`)}</optgroup>`)}
      </select></label>
      <label class="f">Commentaire<textarea rows="3" value=${comment} onInput=${e => setComment(e.target.value)}></textarea></label>
      <div class="row"><span class="spacer"></span><button onClick=${onClose}>Annuler</button><button class="primary" disabled=${!cause || a.busy} onClick=${save}>Enregistrer</button></div>
    </div></${Dialog}>`;
}

function SplitDialog({ stop, onClose, onDone }) {
  const mid = new Date((new Date(stop.starts_at).getTime() + (stop.ends_at ? new Date(stop.ends_at).getTime() : Date.now())) / 2);
  const [at, setAt] = useState(toLocalInput(mid));
  const a = useAction();
  const save = async () => { if (await a.run(() => api(`/arrets/${stop.id}/decouper`, { method: "POST", body: { at: fromLocalInput(at) } }))) onDone(); };
  return html`<${Dialog} title="Découper l'arrêt" onClose=${onClose}>
    <p class="muted">${stop.machine} · ${dt(stop.starts_at)} → ${stop.ends_at ? hm(stop.ends_at) : "…"}</p>
    <${Msg} ...${a.msg} />
    <label class="f">Découper à<input type="datetime-local" value=${at} onInput=${e => setAt(e.target.value)} /></label>
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button onClick=${onClose}>Annuler</button><button class="primary" disabled=${a.busy} onClick=${save}>Découper</button></div>
  </${Dialog}>`;
}

// ----------------------------------------------------------------------------- alarmes
const PRIO = { critique: "bad", haute: "bad", moyenne: "warn", basse: "idle" };
function Alarms() {
  const [active, setActive] = useState(true), [q, setQ] = useState(""), [days, setDays] = useState(7);
  const list = useApi(`/alarmes?actives=${active}&jours=${days}&q=${encodeURIComponent(q)}`, [], 5000);
  const a = useAction();
  return html`<h1>Alarmes</h1><${Msg} ...${a.msg} /><${Msg} error=${list.error} />
    <div class="row" style="margin-bottom:12px">
      <div class="tabs" style="margin:0;border:none">
        <button class=${active ? "on" : ""} onClick=${() => setActive(true)}>En cours</button>
        <button class=${!active ? "on" : ""} onClick=${() => setActive(false)}>Historique</button></div>
      <input placeholder="Rechercher…" value=${q} onInput=${e => setQ(e.target.value)} />
      ${!active && html`<select value=${days} onChange=${e => setDays(+e.target.value)}><option value="1">24 h</option><option value="7">7 jours</option><option value="31">31 jours</option><option value="365">1 an</option></select>`}
      <span class="spacer"></span>
      <button onClick=${() => a.run(() => api("/alarmes/acquitter-tout", { method: "POST" }), "Alarmes acquittées.").then(list.reload)}>Tout acquitter</button>
    </div>
    <div class="table-wrap"><table><thead><tr><th>Priorité</th><th>Machine</th><th>Message</th><th>Apparition</th><th>Disparition</th><th>Acquittement</th><th></th></tr></thead>
      <tbody>${(list.data || []).map(x => html`<tr>
        <td><span class=${"badge " + PRIO[x.priority]}>${x.priority}</span></td><td>${x.machine}</td>
        <td>${x.cleared_at ? x.message : html`<b>${x.message}</b>`}</td><td>${dt(x.raised_at)}</td>
        <td>${x.cleared_at ? dt(x.cleared_at) : html`<span class="badge bad">active</span>`}</td>
        <td>${x.ack_at ? html`${dt(x.ack_at)} <span class="muted">${x.ack_by}</span>` : ""}</td>
        <td>${!x.ack_at && html`<button onClick=${() => a.run(() => api(`/alarmes/${x.id}/acquitter`, { method: "POST" })).then(list.reload)}>Acquitter</button>`}</td></tr>`)}</tbody></table></div>
    ${list.data && !list.data.length && html`<p class="muted">Aucune alarme.</p>`}`;
}

// ----------------------------------------------------------------------------- moteurs
const DRIFT = { normal: "good", attention: "warn", alerte: "bad", arret: "idle", sans_reference: "idle" };
const DRIFT_LABEL = { normal: "Normal", attention: "Attention", alerte: "Alerte", arret: "À l'arrêt", sans_reference: "Sans référence" };
function Motors({ user }) {
  const list = useApi("/moteurs", [], 30000);
  const settings = useApi("/parametres");
  const [sel, setSel] = useState(null), [filter, setFilter] = useState("");
  const ms = (list.data || []).filter(m => !filter || (m.status || "sans_reference") === filter);
  const counts = {};
  (list.data || []).forEach(m => { const s = m.status || "sans_reference"; counts[s] = (counts[s] || 0) + 1; });
  const selected = sel && (list.data || []).find(m => m.id === sel);
  const st = settings.data || {};
  return html`<h1>Moteurs</h1><${Msg} error=${list.error} />
    <div class="legend" style="margin-bottom:12px">
      <button class=${!filter ? "primary" : ""} onClick=${() => setFilter("")}>Tous ${(list.data || []).length}</button>
      ${Object.keys(DRIFT).map(s => counts[s] ? html`<button class=${filter === s ? "primary" : ""} onClick=${() => setFilter(s)}><span class=${"badge " + DRIFT[s]}>${DRIFT_LABEL[s]}</span> ${counts[s]}</button>` : "")}
      <span class="spacer"></span>
      ${settings.data && html`<${Info}>Écart du courant moyen (${st.derive_fenetre_min} min) à la référence.<br />Attention ±${st.derive_seuil_attention_pct} % · alerte ±${st.derive_seuil_alerte_pct} %.</${Info}>`}
    </div>
    ${selected && html`<${MotorDetail} m=${selected} user=${user} settings=${st} onChange=${list.reload} onClose=${() => setSel(null)} />`}
    <div class="motors" style=${selected ? "margin-top:16px" : ""}>${ms.map(m => {
      const s = m.status || "sans_reference";
      return html`<div class=${`tile ${DRIFT[s]} ${sel === m.id ? "sel" : ""}`} onClick=${() => setSel(m.id)} title=${DRIFT_LABEL[s]}>
        <div class="n">${m.name}</div>
        <div class="d">${m.deviation_pct === null || m.deviation_pct === undefined ? DRIFT_LABEL[s] : (m.deviation_pct > 0 ? "+" : "") + num(m.deviation_pct) + " %"}</div>
        <div class="muted num" style="font-size:12px">${num(m.mean_current)} A</div></div>`;
    })}</div>`;
}

const MOTOR_PERIODS = [[1, "24 h"], [7, "7 j"], [31, "31 j"]];
function MotorDetail({ m, user, settings, onChange, onClose }) {
  const [days, setDays] = useState(1);
  const [ref, setRef0] = useState(false);
  const hist = useApi(`/moteurs/${m.id}/historique?jours=${days}`, [m.time]);
  const trend = useApi(m.tag_courant ? `/tendance/${m.tag_courant}?minutes=${days * 1440}` : null, [m.time]);
  const [nominal, setNominal] = useState("");
  const a = useAction();
  const setRef = body => a.run(() => api(`/moteurs/${m.id}/reference`, { method: "POST", body }), r => `Référence : ${num(r.ref_current, 2)} A.`).then(() => { setRef0(false); onChange(); });
  const pts = (hist.data || []).filter(h => h.deviation_pct !== null).map(h => [new Date(h.time).getTime(), h.deviation_pct]);
  const cur = ((trend.data || {}).points || []).filter(p => p[1] !== null).map(p => [new Date(p[0]).getTime(), p[1]]);
  const w = +settings.derive_seuil_attention_pct || 10, al = +settings.derive_seuil_alerte_pct || 20;
  const rc = m.ref_current;
  const status = m.status || "sans_reference";
  const col = { good: "var(--green)", warn: "var(--orange)", bad: "var(--red)", idle: "var(--primary)" }[DRIFT[status]];
  return html`<div class="panel" style="display:flex;flex-direction:column;gap:12px">
    <div class="row">
      <h2 style="margin:0">${m.name}</h2><${Pill} kind=${DRIFT[status]}>${DRIFT_LABEL[status]}</${Pill}>
      <div class="mstat" style="margin-left:12px">
        <div><span>Mesuré</span><b>${num(m.mean_current)} A</b></div>
        <div><span>Référence</span><b>${num(rc, 1)} A</b></div>
        <div><span>Écart</span><b style=${{ color: col }}>${m.deviation_pct === null || m.deviation_pct === undefined ? "–" : (m.deviation_pct > 0 ? "+" : "") + num(m.deviation_pct) + " %"}</b></div>
      </div>
      <span class="spacer"></span>
      <div class="seg light" role="group" aria-label="Période">${MOTOR_PERIODS.map(([v, l]) => html`<button type="button" class=${days === v ? "on" : ""} onClick=${() => setDays(v)}>${l}</button>`)}</div>
      ${can(user, "maintenance") && html`<button class="iconbtn" title="Référence" aria-label="Référence" onClick=${() => setRef0(!ref)}><${Icon} d=${ICON.target} /></button>`}
      <button class="iconbtn" aria-label="Fermer" onClick=${onClose}><${Icon} d=${ICON.close} /></button>
    </div>
    <${Msg} ...${a.msg} />
    ${ref && html`<div class="row" style="background:var(--fill2);border-radius:12px;padding:10px 12px">
      <span class="muted">${m.method === "mesure" ? "Mesurée" : "Nominale"}${m.set_at ? " · " + dt(m.set_at) : ""}</span><span class="spacer"></span>
      <button onClick=${() => setRef({ method: "mesure" })} disabled=${a.busy}>Recaler sur la mesure</button>
      <input class="small" type="number" step="0.1" placeholder="A" value=${nominal} onInput=${e => setNominal(e.target.value)} />
      <button onClick=${() => setRef({ method: "nominal", value: +nominal })} disabled=${!nominal || a.busy}>Fixer</button></div>`}
    <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px">
      <div><div class="row" style="gap:8px;margin-bottom:6px"><b>Courant</b><span class="muted">A</span></div>
        <${Chart} height=${220} unit="A" color=${col} series=${[{ points: cur, color: col }]}
          zones=${rc ? [{ from: rc * (1 - w / 100), to: rc * (1 + w / 100), color: "var(--green-t)" }] : []}
          bands=${rc ? [{ y: rc, color: "var(--green-fg)", label: "Réf. " + num(rc, 1) + " A" }] : []} /></div>
      <div><div class="row" style="gap:8px;margin-bottom:6px"><b>Écart à la référence</b><span class="muted">%</span></div>
        <${Chart} height=${220} unit="%" series=${[{ points: pts, color: col }]} empty="Pas de référence ou pas de mesure."
          zones=${[{ from: -w, to: w, color: "var(--green-t)" }, { from: w, to: al, color: "var(--orange-t)" }, { from: -al, to: -w, color: "var(--orange-t)" },
            { from: al, to: Infinity, color: "var(--red-t)" }, { from: -Infinity, to: -al, color: "var(--red-t)" }]} /></div>
    </div>
  </div>`;
}

// ----------------------------------------------------------------------------- énergie
function Energy() {
  const [days, setDays] = useState(7);
  const { data, error } = useApi(`/energie?jours=${days}`, [], 60000);
  if (error) return html`<${Msg} error=${error} />`;
  if (!data) return html`<p class="muted">Chargement…</p>`;
  const total = data.par_compteur.reduce((s, c) => s + (c.kwh || 0), 0);
  const tons = data.par_shift.reduce((s, c) => s + (c.tonnage_t || 0), 0);
  const maxK = Math.max(1, ...data.par_compteur.map(c => c.kwh || 0));
  const lines = [...new Set(data.par_shift.map(s => s.line))];
  const colors = ["#1f5fbf", "#c77c02", "#1e8e4e", "#8a3fbf", "#c62f2f", "#00838f"];
  return html`<div class="row"><h1>Énergie</h1><span class="spacer"></span>
      <select value=${days} onChange=${e => setDays(+e.target.value)}><option value="1">24 h</option><option value="7">7 jours</option><option value="31">31 jours</option></select></div>
    <div class="kpis">
      <div class="panel kpi"><div class="v">${num(data.puissance_kw, 0)} kW</div><div class="l">Puissance</div></div>
      <div class="panel kpi"><div class="v">${num(total / 1000, 1)} MWh</div><div class="l">Consommation</div></div>
      <div class="panel kpi"><div class="v">${num(tons ? total / tons : null)} kWh/t</div><div class="l">Ratio</div></div>
    </div>
    <h2>kWh/t par poste</h2>
    <div class="panel"><${Chart} area=${false} unit="kWh/t" series=${lines.map((l, i) => ({ color: colors[i % colors.length], points: data.par_shift.filter(s => s.line === l && s.kwh_per_t !== null).map(s => [new Date(s.starts_at).getTime(), s.kwh_per_t]) }))} />
      <div class="legend">${lines.map((l, i) => html`<span><span class="badge" style=${{ background: colors[i % colors.length], color: "#fff" }}>${l}</span></span>`)}</div></div>
    <h2>Compteurs</h2>
    <div class="panel">${data.par_compteur.map(c => html`<div style="margin-bottom:8px">
      <div class="row"><span>${c.compteur} <span class="muted">${[c.line, c.usage].filter(Boolean).join(" · ")}</span></span><span class="spacer"></span><span>${num(c.kwh, 0)} kWh</span></div>
      <div class="bar"><div style=${{ width: ((c.kwh || 0) / maxK) * 100 + "%" }}></div></div></div>`)}
      ${!data.par_compteur.length && html`<span class="muted">Aucun compteur.</span>`}</div>`;
}

// ----------------------------------------------------------------------------- recettes
function Recipes({ user, route }) {
  const id = route[1];
  if (id === "nouvelle") return html`<${RecipeNew} />`;
  if (id) return html`<${RecipeDetail} id=${+id} user=${user} />`;
  return html`<${RecipeList} user=${user} />`;
}

function RecipeList({ user }) {
  const types = useApi("/recettes/types");
  const [f, setF] = useState({ q: "", type_id: "", statut: "", archivees: false, param: "", op: ">", valeur: "" });
  const qs = new URLSearchParams({ q: f.q, archivees: f.archivees });
  if (f.type_id) qs.set("type_id", f.type_id);
  if (f.statut) qs.set("statut", f.statut);
  if (f.param && f.valeur !== "") { qs.set("param", f.param); qs.set("op", f.op); qs.set("valeur", f.valeur); }
  const list = useApi(`/recettes?${qs}`);
  const params = (types.data || []).flatMap(t => t.definition.params.map(p => ({ ...p, type: t.label })));
  return html`<div class="row"><h1>Recettes</h1><span class="spacer"></span>
      ${can(user, "chef_equipe") && html`<a class="btn primary" href="#/recettes/nouvelle">Nouvelle recette</a>`}</div>
    <div class="panel row" style="margin-bottom:12px;align-items:end">
      <label class="f" style="flex:1">Recherche<input placeholder="Code, libellé, produit…" value=${f.q} onInput=${e => setF({ ...f, q: e.target.value })} /></label>
      <label class="f">Type<select value=${f.type_id} onChange=${e => setF({ ...f, type_id: e.target.value })}><option value="">Tous</option>${(types.data || []).map(t => html`<option value=${t.id}>${t.label}</option>`)}</select></label>
      <label class="f">Statut<select value=${f.statut} onChange=${e => setF({ ...f, statut: e.target.value })}><option value="">Tous</option><option value="brouillon">Brouillon</option><option value="validee">Validée</option></select></label>
      <label class="f">Paramètre<select value=${f.param} onChange=${e => setF({ ...f, param: e.target.value })}><option value="">—</option>${params.map(p => html`<option value=${p.name}>${p.label}</option>`)}</select></label>
      <select value=${f.op} onChange=${e => setF({ ...f, op: e.target.value })}>${[">", ">=", "=", "<=", "<"].map(o => html`<option>${o}</option>`)}</select>
      <input class="small" type="number" value=${f.valeur} onInput=${e => setF({ ...f, valeur: e.target.value })} />
      <label class="row"><input type="checkbox" checked=${f.archivees} onChange=${e => setF({ ...f, archivees: e.target.checked })} /> archivées</label>
    </div>
    <${Msg} error=${list.error} />
    <div class="table-wrap"><table><thead><tr><th>Code</th><th>Libellé</th><th>Produit</th><th>Type</th><th>Version</th><th>Statut</th><th>Dernier chargement</th></tr></thead>
      <tbody>${(list.data || []).map(r => html`<tr style="cursor:pointer" onClick=${() => (location.hash = `#/recettes/${r.id}`)}>
        <td><a href=${`#/recettes/${r.id}`}>${r.code}</a></td><td>${r.label}</td><td>${r.product}</td><td>${r.type}</td><td>v${r.version}</td>
        <td><${Status} s=${r.status} archived=${r.archived} /></td><td>${dt(r.dernier_chargement)}</td></tr>`)}</tbody></table></div>
    ${list.data && !list.data.length && html`<p class="muted">Aucune recette ne correspond.</p>`}`;
}

const Status = ({ s, archived }) => archived ? html`<span class="badge idle">archivée</span>` :
  s === "validee" ? html`<span class="badge good">validée</span>` : html`<span class="badge warn">brouillon</span>`;

function ParamTable({ tdef, values, edit, onChange, compare }) {
  return html`<div class="table-wrap"><table><thead><tr><th>Paramètre</th><th class="num">Valeur</th><th>Unité</th><th class="num">Min</th><th class="num">Max</th>
      ${compare && html`<th class="num">${compare.comparee}</th>`}</tr></thead>
    <tbody>${tdef.params.map(p => {
      const c = compare && compare.lignes.find(l => l.param === p.name);
      const v = values[p.name];
      const out = v !== "" && v !== undefined && ((p.min !== undefined && p.min !== null && +v < p.min) || (p.max !== undefined && p.max !== null && +v > p.max));
      return html`<tr class=${c && c.ecart ? "diff" : ""}>
        <td>${p.label}<div class="muted" style="font-size:12px">${p.name}</div></td>
        <td class="num">${edit ? html`<input class="small" type="number" step="any" style=${out ? "border-color:var(--red)" : ""} value=${v ?? ""} onInput=${e => onChange({ ...values, [p.name]: e.target.value })} />` : num(v, p.datatype.startsWith("Int") ? 0 : 2)}</td>
        <td>${p.unit || ""}</td><td class="num">${p.min ?? ""}</td><td class="num">${p.max ?? ""}</td>
        ${compare && html`<td class="num">${c ? num(c.b, 2) : ""}${c && c.ecart ? html` <span class="badge warn">écart</span>` : ""}</td>`}</tr>`;
    })}</tbody></table></div>`;
}

function RecipeNew() {
  const types = useApi("/recettes/types");
  const [f, setF] = useState({ type_id: "", code: "", label: "", product: "" });
  const [values, setValues] = useState({});
  const a = useAction();
  const t = (types.data || []).find(x => x.id === +f.type_id);
  useEffect(() => { if (t) setValues(Object.fromEntries(t.definition.params.map(p => [p.name, p.default]))); }, [f.type_id]);
  useEffect(() => { if (types.data && types.data.length === 1 && !f.type_id) setF({ ...f, type_id: String(types.data[0].id) }); }, [types.data]);
  const save = async () => {
    const r = await a.run(() => api("/recettes", { method: "POST", body: { ...f, type_id: +f.type_id, values } }));
    if (r) location.hash = `#/recettes/${r.id}`;
  };
  return html`<p><a href="#/recettes">← Recettes</a></p><h1>Nouvelle recette</h1><${Msg} ...${a.msg} />
    <div class="panel row" style="margin-bottom:12px;align-items:end">
      <label class="f">Type<select value=${f.type_id} onChange=${e => setF({ ...f, type_id: e.target.value })}><option value="">Choisir…</option>${(types.data || []).map(t => html`<option value=${t.id}>${t.label}</option>`)}</select></label>
      <label class="f">Code<input value=${f.code} onInput=${e => setF({ ...f, code: e.target.value })} /></label>
      <label class="f" style="flex:1">Libellé<input value=${f.label} onInput=${e => setF({ ...f, label: e.target.value })} /></label>
      <label class="f">Produit<input value=${f.product} onInput=${e => setF({ ...f, product: e.target.value })} /></label></div>
    ${t && html`<${ParamTable} tdef=${t.definition} values=${values} edit=${true} onChange=${setValues} />`}
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button class="primary" disabled=${!t || !f.code || !f.label || a.busy} onClick=${save}>Créer (brouillon v1)</button></div>`;
}

function RecipeDetail({ id, user }) {
  const r = useApi(`/recettes/${id}`);
  const [vid, setVid] = useState(null);
  const [edit, setEdit] = useState(null), [comment, setComment] = useState("");
  const [cmp, setCmp] = useState(null), [cmpWith, setCmpWith] = useState("production");
  const [dup, setDup] = useState(null);
  const [load, setLoad] = useState(null);
  const a = useAction();
  const chef = can(user, "chef_equipe");
  useEffect(() => { if (r.data && !vid) setVid(r.data.versions[0].id); }, [r.data]);
  useEffect(() => {  // suivi du chargement automate
    if (!load || load.result !== "en_cours") return;
    const t = setTimeout(() => api(`/recettes/chargements/${load.id}`).then(x => { setLoad(x); if (x.result !== "en_cours") r.reload(); }), 700);
    return () => clearTimeout(t);
  }, [load]);
  if (r.error) return html`<${Msg} error=${r.error} />`;
  if (!r.data || !vid) return html`<p class="muted">Chargement…</p>`;
  const rec = r.data, tdef = rec.type.definition;
  const v = rec.versions.find(x => x.id === vid) || rec.versions[0];
  const latest = rec.versions[0];
  const doCompare = async () => setCmp(await a.run(() => api(`/recettes/versions/${v.id}/comparer?avec=${cmpWith}`)) || null);
  const saveVersion = async () => {
    const x = await a.run(() => api(`/recettes/${id}/versions`, { method: "POST", body: { values: edit, comment } }), y => `Version ${y.version} créée en brouillon.`);
    if (x) { setEdit(null); setComment(""); setVid(x.id); r.reload(); }
  };
  const LOAD_TXT = { en_cours: ["warn", "Chargement en cours…"], acceptee: ["good", "Acceptée par l'automate"], refusee: ["bad", "Refusée par l'automate"], delai_depasse: ["bad", "Pas de réponse de l'automate"], erreur: ["bad", "Erreur de communication"] };
  return html`<p><a href="#/recettes">← Recettes</a></p>
    <div class="row"><h1 style="margin:0">${rec.code} · ${rec.label}</h1><${Status} s=${v.status} archived=${rec.archived} /><span class="spacer"></span>
      ${chef && html`<button onClick=${() => setDup({ code: rec.code + "-COPIE", label: "" })}>Dupliquer</button>
        <button onClick=${() => a.run(() => api(`/recettes/${id}/archiver`, { method: "POST" }), rec.archived ? "Recette réactivée." : "Recette archivée.").then(r.reload)}>${rec.archived ? "Désarchiver" : "Archiver"}</button>`}</div>
    <p class="muted">${rec.type.label}${rec.product ? " · produit " + rec.product : ""} · créée par ${rec.created_by || "?"}</p>
    <${Msg} ...${a.msg} />
    <div class="row" style="margin-bottom:12px">
      <label class="row">Version <select value=${v.id} onChange=${e => { setVid(+e.target.value); setCmp(null); setEdit(null); }}>
        ${rec.versions.map(x => html`<option value=${x.id}>v${x.version} · ${x.status} · ${dt(x.created_at)}</option>`)}</select></label>
      <span class="muted">${v.comment || ""} (${v.created_by}${v.validated_by ? `, validée par ${v.validated_by}` : ""})</span>
      <span class="spacer"></span>
      ${chef && !edit && html`<button onClick=${() => setEdit({ ...v.values })}>Modifier</button>`}
      ${chef && v.status === "brouillon" && !edit && html`<button onClick=${() => a.run(() => api(`/recettes/versions/${v.id}/valider`, { method: "POST" }), "Version validée.").then(r.reload)}>Valider</button>`}
      ${chef && v.status === "validee" && !rec.archived && !edit && html`<button class="primary" disabled=${load && load.result === "en_cours"} onClick=${async () => {
        const x = await a.run(() => api(`/recettes/versions/${v.id}/charger`, { method: "POST" }));
        if (x) setLoad({ id: x.id, result: "en_cours" });
      }}>Charger dans l'automate</button>`}
    </div>
    ${load && html`<div class=${LOAD_TXT[load.result][0] === "good" ? "ok" : LOAD_TXT[load.result][0] === "bad" ? "err" : "panel"} style="margin-bottom:12px">
      ${LOAD_TXT[load.result][1]}${load.plc_code ? ` (code ${load.plc_code})` : ""}${load.details && load.details.motif ? ` : ${load.details.motif}` : ""}${load.details && load.details.erreur ? ` : ${load.details.erreur}` : ""}</div>`}
    ${!edit && html`<div class="row" style="margin-bottom:12px">
      <label class="row">Comparer avec <select value=${cmpWith} onChange=${e => setCmpWith(e.target.value)}>
        <option value="production">la production en cours (automate)</option>
        ${rec.versions.filter(x => x.id !== v.id).map(x => html`<option value=${x.id}>la version ${x.version}</option>`)}</select></label>
      <button onClick=${doCompare} disabled=${a.busy}>Comparer</button>
      ${cmp && html`<span class=${"badge " + (cmp.ecarts ? "warn" : "good")}>${cmp.ecarts ? `${cmp.ecarts} écart(s)` : "identique"}</span><button onClick=${() => setCmp(null)}>Masquer</button>`}</div>`}
    <${ParamTable} tdef=${tdef} values=${edit || v.values} edit=${!!edit} onChange=${setEdit} compare=${!edit && cmp} />
    ${edit && html`<div class="row" style="margin-top:12px"><input style="flex:1" placeholder="Motif de la modification" value=${comment} onInput=${e => setComment(e.target.value)} />
      <button onClick=${() => setEdit(null)}>Annuler</button><button class="primary" disabled=${a.busy} onClick=${saveVersion}>Enregistrer v${latest.version + 1}</button></div>`}
    <h2>Chargements</h2>
    <div class="table-wrap"><table><thead><tr><th>Date</th><th>Version</th><th>Par</th><th>Résultat</th><th>Détail</th></tr></thead>
      <tbody>${rec.chargements.map(l => html`<tr><td>${dt(l.requested_at)}</td><td>v${l.version}</td><td>${l.requested_by}</td>
        <td><span class=${"badge " + (LOAD_TXT[l.result] || ["idle"])[0]}>${l.result}</span></td><td>${l.plc_code ? "code " + l.plc_code + " " : ""}${(l.details || {}).motif || (l.details || {}).erreur || ""}</td></tr>`)}</tbody></table></div>
    ${!rec.chargements.length && html`<p class="muted">Jamais chargée.</p>`}
    ${dup && html`<${Dialog} title="Dupliquer la recette" onClose=${() => setDup(null)}>
      <div style="display:grid;gap:12px"><label class="f">Nouveau code<input value=${dup.code} onInput=${e => setDup({ ...dup, code: e.target.value })} /></label>
      <label class="f">Libellé<input value=${dup.label} placeholder=${"Copie de " + rec.label} onInput=${e => setDup({ ...dup, label: e.target.value })} /></label>
      <div class="row"><span class="spacer"></span><button onClick=${() => setDup(null)}>Annuler</button><button class="primary" onClick=${async () => {
        const x = await a.run(() => api(`/recettes/${id}/dupliquer`, { method: "POST", body: { code: dup.code, label: dup.label || null } }));
        setDup(null); if (x) location.hash = `#/recettes/${x.id}`;
      }}>Dupliquer</button></div></div></${Dialog}>`}`;
}

// ----------------------------------------------------------------------------- catalogue
function Catalog({ user }) {
  const [tab, setTab] = useState("decouverte");
  return html`<h1>Catalogue automate</h1>
    <div class="tabs">${[["decouverte", "Découverte"], ["machines", "Machines"], ["types", "Types de machines"], ["csv", "Import / export"]].map(([k, l]) =>
      html`<button class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${l}</button>`)}</div>
    ${tab === "decouverte" && html`<${Discovery} />`}
    ${tab === "machines" && html`<${Machines} user=${user} />`}
    ${tab === "types" && html`<${Types} />`}
    ${tab === "csv" && html`<${CsvIO} />`}`;
}

function Discovery() {
  const [run, setRun] = useState(null);
  const [sel, setSel] = useState({});
  const [opts, setOpts] = useState({ include_unknown: false, disable_missing: false });
  const a = useAction();
  const start = async () => {
    const r = await a.run(() => api("/catalogue/decouverte", { method: "POST" }));
    if (r) { setRun(r); setSel(Object.fromEntries(r.instances.filter(i => i.status !== "existant" && !i.missing.length).map(i => [i.base_node, true]))); }
  };
  const apply = async () => {
    const selections = run.instances.filter(i => sel[i.base_node]).map(i => ({ base_node: i.base_node, area: i.db }));
    const r = await a.run(() => api(`/catalogue/decouverte/${run.id}/appliquer`, { method: "POST", body: { selections, ...opts } }),
      x => `Appliqué : ${x.machines_creees} machines créées, ${x.machines_mises_a_jour} mises à jour, ${x.tags_bruts_crees} tags bruts, ${x.machines_desactivees} désactivées. La collecte prend les changements en compte sous une minute.`);
    if (r) setRun(null);
  };
  const byType = {};
  (run ? run.instances : []).forEach(i => { (byType[i.type] = byType[i.type] || []).push(i); });
  return html`<${Msg} ...${a.msg} />
    <div class="panel row" style="margin-bottom:16px"><b style="flex:1">Découverte de l'automate</b>
      <${Info}>Parcourt les blocs de données de l'automate, reconnaît les machines d'après les types déclarés et propose de les ajouter avec leurs tags.</${Info}>
      <button class="primary" onClick=${start} disabled=${a.busy}>${a.busy ? "Découverte…" : "Lancer la découverte"}</button></div>
    ${run && html`
      <p>${run.instances.length} instances reconnues sur ${run.total_nodes} nœuds · ${run.unknown_tags} tags dans des structures inconnues · ${run.disappeared.length} machines disparues.</p>
      ${Object.entries(byType).map(([t, is]) => html`<h2>${t} (${is.length})
          <button style="margin-left:8px" onClick=${() => setSel({ ...sel, ...Object.fromEntries(is.map(i => [i.base_node, true])) })}>Tout cocher</button>
          <button onClick=${() => setSel({ ...sel, ...Object.fromEntries(is.map(i => [i.base_node, false])) })}>Tout décocher</button></h2>
        <div class="table-wrap" style="max-height:320px;overflow:auto"><table><thead><tr><th></th><th>Instance</th><th>Bloc</th><th>État</th><th class="num">Couverture</th><th>Membres manquants</th></tr></thead>
          <tbody>${is.map(i => html`<tr><td><input type="checkbox" checked=${!!sel[i.base_node]} onChange=${e => setSel({ ...sel, [i.base_node]: e.target.checked })} /></td>
            <td>${i.name}</td><td>${i.db}</td><td><span class=${"badge " + (i.status === "nouveau" ? "good" : i.status === "existant" ? "idle" : "warn")}>${i.status}</span></td>
            <td class="num">${pct(i.coverage)}</td><td>${i.missing.join(", ")}</td></tr>`)}</tbody></table></div>`)}
      ${run.unknown.length > 0 && html`<h2>Structures non reconnues</h2><div class="table-wrap"><table><thead><tr><th class="num">Instances</th><th>Membres</th><th>Exemples</th></tr></thead>
        <tbody>${run.unknown.slice(0, 20).map(u => html`<tr><td class="num">${u.count}</td><td style="font-size:12px">${u.members.join(", ")}</td><td style="font-size:12px">${u.examples.join(", ")}</td></tr>`)}</tbody></table></div>
`}
      ${run.disappeared.length > 0 && html`<h2>Machines disparues de l'automate</h2><p>${run.disappeared.map(d => d.name).join(", ")}</p>`}
      <div class="panel row" style="margin-top:16px">
        <label class="row"><input type="checkbox" checked=${opts.include_unknown} onChange=${e => setOpts({ ...opts, include_unknown: e.target.checked })} /> importer aussi les structures inconnues comme tags bruts</label>
        <label class="row"><input type="checkbox" checked=${opts.disable_missing} onChange=${e => setOpts({ ...opts, disable_missing: e.target.checked })} /> désactiver les machines disparues</label>
        <span class="spacer"></span><button class="primary" disabled=${a.busy} onClick=${apply}>Appliquer (${Object.values(sel).filter(Boolean).length} instances)</button></div>`}`;
}

function Machines({ user }) {
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const types = useApi("/catalogue/types");
  const list = useApi(`/catalogue/machines${type ? "?type=" + type : ""}`);
  const [ed, setEd] = useState(null);
  const a = useAction();
  const rows = (list.data || []).filter(m => !q || (m.name + m.area + m.base_node).toLowerCase().includes(q.toLowerCase()));
  const tdef = ed && (types.data || []).find(t => t.code === ed.type);
  return html`<${Msg} ...${a.msg} />
    <div class="row" style="margin-bottom:12px"><select value=${type} onChange=${e => setType(e.target.value)}><option value="">Tous les types</option>${(types.data || []).map(t => html`<option value=${t.code}>${t.label}</option>`)}</select>
      <input placeholder="Filtrer…" value=${q} onInput=${e => setQ(e.target.value)} /><span class="muted">${rows.length} machines</span></div>
    <div class="table-wrap"><table><thead><tr><th>Machine</th><th>Type</th><th>Zone</th><th>Attributs</th><th class="num">Tags</th><th>Active</th><th></th></tr></thead>
      <tbody>${rows.slice(0, 500).map(m => html`<tr><td>${m.name}</td><td>${m.type_label}</td><td>${m.area}</td>
        <td style="font-size:12px">${Object.entries(m.attributes || {}).map(([k, v]) => `${k} = ${v}`).join(" · ")}</td><td class="num">${m.tags}</td>
        <td>${m.enabled ? "oui" : html`<span class="badge idle">non</span>`}</td>
        <td>${user.role === "admin" && html`<button onClick=${() => setEd({ ...m, attributes: { ...(m.attributes || {}) } })}>Modifier</button>`}</td></tr>`)}</tbody></table></div>
    ${ed && html`<${Dialog} title=${"Machine " + ed.name} onClose=${() => setEd(null)}>
      <div style="display:grid;gap:12px">
        <label class="f">Nom<input value=${ed.name} onInput=${e => setEd({ ...ed, name: e.target.value })} /></label>
        <label class="f">Zone (chemin, séparé par /)<input value=${ed.area || ""} onInput=${e => setEd({ ...ed, area: e.target.value })} /></label>
        ${((tdef && tdef.definition.attributes) || []).map(at => html`<label class="f">${at.name}${at.unit ? ` (${at.unit})` : ""}${at.required ? " *" : ""}
          <input value=${ed.attributes[at.name] ?? ""} onInput=${e => setEd({ ...ed, attributes: { ...ed.attributes, [at.name]: e.target.value } })} /></label>`)}
        <label class="row"><input type="checkbox" checked=${ed.enabled} onChange=${e => setEd({ ...ed, enabled: e.target.checked })} /> active (collectée)</label>
        <div class="row"><span class="spacer"></span><button onClick=${() => setEd(null)}>Annuler</button><button class="primary" onClick=${async () => {
          const attrs = Object.fromEntries(Object.entries(ed.attributes).map(([k, v]) => [k, v !== "" && !isNaN(+v) ? +v : v]));
          if (await a.run(() => api(`/catalogue/machines/${ed.id}`, { method: "PATCH", body: { name: ed.name, area: ed.area, attributes: attrs, enabled: ed.enabled } }), "Machine mise à jour.")) { setEd(null); list.reload(); }
        }}>Enregistrer</button></div></div></${Dialog}>`}`;
}

function Types() {
  const types = useApi("/catalogue/types");
  return html`${(types.data || []).map(t => html`<div class="panel" style="margin-bottom:12px"><div class="row"><b>${t.label}</b><span class="muted">${t.code} · v${t.version}</span><span class="spacer"></span><span>${t.machines} machines</span></div>
      <div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>Membre</th><th>Rôle</th><th>Unité</th><th>Deadband</th><th>Signature</th></tr></thead>
        <tbody>${t.definition.members.map(m => html`<tr><td>${m.path}${m.optional ? html` <span class="muted">(optionnel)</span>` : ""}</td><td>${m.role || ""}</td><td>${m.unit || ""}</td>
          <td>${m.deadband_kind !== "none" ? `${m.deadband_kind === "percent" ? m.deadband_value + " %" : m.deadband_value}` : ""}</td><td>${t.definition.signature.some(s => s.path === m.path) ? "oui" : ""}</td></tr>`)}</tbody></table></div></div>`)}`;
}

function CsvIO() {
  const a = useAction();
  const upload = (kind) => async e => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    await a.run(() => api(`/catalogue/import/${kind}`, { method: "POST", body: fd }),
      r => `${r.importees ?? r.mis_a_jour} lignes traitées${r.erreurs.length ? `, ${r.erreurs.length} erreurs : ${r.erreurs.slice(0, 5).join(" ; ")}` : "."}`);
    e.target.value = "";
  };
  return html`<${Msg} ...${a.msg} />
    <div class="grid">
      <div class="panel"><h2 class="row" style="margin-top:0">Machines <${Info}>machine ; type ; zone ; noeud_opcua ; actif ; attr:…</${Info}></h2>
        <div class="row"><a class="btn" href="/api/catalogue/export/machines.csv">Exporter</a><label class="btn">Importer…<input type="file" accept=".csv" hidden onChange=${upload("machines")} /></label></div></div>
      <div class="panel"><h2 class="row" style="margin-top:0">Tags <${Info}>Unités, rôles, deadbands, historisation et activation, par node_id.</${Info}></h2>
        <div class="row"><a class="btn" href="/api/catalogue/export/tags.csv">Exporter</a><label class="btn">Importer…<input type="file" accept=".csv" hidden onChange=${upload("tags")} /></label></div></div>
    </div>`;
}

// ----------------------------------------------------------------------------- analyse (Grafana)
function Analysis() {
  const theme = useTheme();
  const dashes = [["mes-performance", "Performance et TRS"], ["mes-energie", "Énergie"], ["mes-tendances", "Tendances historian"], ["mes-collecte", "Santé de la collecte"]];
  const [d, setD] = useState(dashes[0][0]);
  return html`<div class="row"><h1>Analyse</h1><span class="spacer"></span><a class="btn" href="/grafana/" target="_blank" rel="noopener">Grafana ↗</a></div>
    <div class="tabs">${dashes.map(([k, l]) => html`<button class=${d === k ? "on" : ""} onClick=${() => setD(k)}>${l}</button>`)}</div>
    <iframe src=${`/grafana/d/${d}?kiosk&theme=${theme === "sombre" ? "dark" : "light"}`} style="width:100%;height:calc(100vh - 230px);border:1px solid var(--line);border-radius:10px;background:var(--card)"></iframe>`;
}

// ----------------------------------------------------------------------------- utilisateurs, journal
function Users() {
  const list = useApi("/utilisateurs");
  const journal = useApi("/journal?limit=100");
  const [f, setF] = useState({ username: "", display_name: "", role: "operateur", password: "", active: true });
  const a = useAction();
  return html`<h1>Utilisateurs</h1><${Msg} ...${a.msg} />
    <div class="table-wrap"><table><thead><tr><th>Identifiant</th><th>Nom</th><th>Rôle</th><th>Source</th><th>Actif</th><th>Dernière connexion</th></tr></thead>
      <tbody>${(list.data || []).map(u => html`<tr><td>${u.username}</td><td>${u.display_name}</td>
        <td><select value=${u.role} onChange=${e => a.run(() => api("/utilisateurs", { method: "POST", body: { ...u, role: e.target.value, password: null } }), "Rôle modifié.").then(list.reload)}>
          ${Object.entries(ROLE_LABEL).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select></td>
        <td>${u.source === "ldap" ? "Active Directory" : "local"}</td>
        <td><input type="checkbox" checked=${u.active} onChange=${e => a.run(() => api("/utilisateurs", { method: "POST", body: { ...u, active: e.target.checked, password: null } })).then(list.reload)} /></td>
        <td>${dt(u.last_login)}</td></tr>`)}</tbody></table></div>
    <h2 class="row">Compte local <${Info}>Les comptes Active Directory sont créés à la première connexion. Mot de passe local : 8 caractères minimum.</${Info}></h2>
    <div class="panel row" style="align-items:end">
      <label class="f">Identifiant<input value=${f.username} onInput=${e => setF({ ...f, username: e.target.value })} /></label>
      <label class="f">Nom affiché<input value=${f.display_name} onInput=${e => setF({ ...f, display_name: e.target.value })} /></label>
      <label class="f">Rôle<select value=${f.role} onChange=${e => setF({ ...f, role: e.target.value })}>${Object.entries(ROLE_LABEL).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select></label>
      <label class="f">Mot de passe<input type="password" value=${f.password} onInput=${e => setF({ ...f, password: e.target.value })} /></label>
      <button class="primary" disabled=${!f.username || f.password.length < 8} onClick=${async () => {
        if (await a.run(() => api("/utilisateurs", { method: "POST", body: f }), "Compte créé.")) { setF({ ...f, username: "", display_name: "", password: "" }); list.reload(); }
      }}>Créer</button></div>
    <h2>Journal</h2>
    <div class="table-wrap" style="max-height:400px;overflow:auto"><table><thead><tr><th>Date</th><th>Utilisateur</th><th>Action</th><th>Objet</th><th>Détails</th></tr></thead>
      <tbody>${(journal.data || []).map(j => html`<tr><td>${dt(j.at)}</td><td>${j.actor}</td><td>${j.action}</td><td>${j.entity ? `${j.entity} ${j.entity_id ?? ""}` : ""}</td>
        <td style="font-size:12px">${j.details ? JSON.stringify(j.details) : ""}</td></tr>`)}</tbody></table></div>`;
}

// ----------------------------------------------------------------------------- application
// [route, libellé, composant, rôles autorisés, groupe du menu, vue « une hauteur d'écran »]
const PAGES = [
  ["", "Synthèse", Synthese, null, "top", true],
  ["ligne", "Ligne", Ligne, null, "hidden", true],
  ["planning", "Planning", Planning, null, "outils", true],
  ["arrets", "Arrêts", Stops, null, "outils"],
  ["alarmes", "Alarmes", Alarms, null, "outils"],
  ["moteurs", "Moteurs", Motors, null, "outils"],
  ["energie", "Énergie", Energy, null, "outils"],
  ["recettes", "Recettes", Recipes, null, "outils"],
  ["analyse", "Analyse", Analysis, null, "outils"],
  ["catalogue", "Catalogue", Catalog, ["admin"], "reglages"],
  ["utilisateurs", "Utilisateurs", Users, ["admin"], "reglages"],
];

function useHash() {
  const [h, setH] = useState(location.hash);
  useEffect(() => { const f = () => setH(location.hash); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  return h.replace(/^#\/?/, "").split("/");
}

const LOGO = "M3 20V9l6 4V9l6 4V5h6v15z";
const LINE_DOT = { good: "#06D16F", warn: "#FF9500", bad: "#FF3B30", idle: "#A9BDD6" };

function App() {
  const [user, setUser] = useState(undefined);
  const route = useHash();
  const counts = useApi(user ? "/tableau-de-bord" : null, [user], 15000);
  useEffect(() => {
    api("/session").then(setUser, () => setUser(null));
    const out = () => setUser(null);
    addEventListener("mes-logout", out);
    return () => removeEventListener("mes-logout", out);
  }, []);
  if (user === undefined) return html`<p class="muted" style="padding:24px">Chargement…</p>`;
  if (!user) return html`<${Login} onLogin=${u => api("/session").then(setUser, () => setUser(u))} />`;
  const pages = PAGES.filter(p => !p[3] || p[3].includes(user.role));
  const page = pages.find(p => p[0] === route[0]) || pages[0];
  const Comp = page[2];
  const c = counts.data || {};
  const badge = { alarmes: [c.alarmes_non_acquittees, ""], arrets: [c.arrets_a_qualifier, "warn"], moteurs: [c.moteurs_en_alerte, ""] };
  const link = p => html`<a href=${"#/" + p[0]} class=${p === page ? "on" : ""}>${p[1]}${badge[p[0]] && badge[p[0]][0] ? html`<span class=${"cnt " + badge[p[0]][1]}>${badge[p[0]][0]}</span>` : ""}</a>`;
  const group = g => pages.filter(p => p[4] === g).map(link);
  return html`<div class="layout">
    <nav class="side" aria-label="Navigation principale">
      <div class="brand"><span class="logo"><${Icon} d=${LOGO} /></span>MES</div>
      ${group("top")}
      ${(c.lignes || []).length > 0 && html`<p class="grp">Lignes</p>`}
      ${(c.lignes || []).map(l => html`<a href=${"#/ligne/" + l.id} class=${page[0] === "ligne" && +route[1] === l.id ? "on" : ""}>
        <span class="ldot" style=${{ background: LINE_DOT[lineState(l).kind] }}></span>${l.name}${l.unqualified_count ? html`<span class="cnt warn">${l.unqualified_count}</span>` : ""}</a>`)}
      <p class="grp">Outils</p>${group("outils")}
      ${group("reglages").length > 0 && html`<p class="grp">Réglages</p>`}${group("reglages")}
      <div class="who">${user.display_name || user.username}<br />${ROLE_LABEL[user.role] !== user.display_name ? html`${ROLE_LABEL[user.role]}<br />` : ""}
        ${user.auth === false ? html`<span>Accès sans identifiant</span>` : html`<a href="#" onClick=${e => { e.preventDefault(); api("/session", { method: "DELETE" }).finally(() => setUser(null)); }}>Se déconnecter</a>`}
        <div style="margin-top:8px">version ${user.version}</div></div></nav>
    ${page[5]
      ? html`<main class="fit"><${Comp} user=${user} route=${route} key=${page[0] + "/" + (route[1] || "")} /></main>`
      : html`<main><${Band} over=${"MES · " + (page[4] === "reglages" ? "Réglages" : "Outils")} title=${page[1]} />
          <div class="page"><${Comp} user=${user} route=${route} key=${route.join("/")} /></div></main>`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
