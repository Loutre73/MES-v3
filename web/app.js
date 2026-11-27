// Interface MES v3 : Preact + htm, sans étape de compilation (fonctionne hors ligne).
import { html, render, useState, useEffect } from "./vendor/preact-htm.module.js";
import {
  api, useApi, useAction, Msg, Dialog, Chart, dt, hm, num, pct, dur, dhms, toLocalInput, fromLocalInput, ROLE_LABEL, can,
  Band, Icon, ICON, Info, Pill, ThemeToggle, LangToggle, chooseLang, lineState, useTheme,
} from "./lib.js";
import { tr, N_, LANGS, getLang, useLang } from "./i18n.js";
import { Synthese } from "./synthese.js";
import { Ligne } from "./ligne.js";
import { Planning } from "./planning.js";
import { Recettes } from "./recettes.js";
import { Energie } from "./energie.js";
import { Analyse } from "./analyse.js";
import { Pareto } from "./charts.js";
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
    <div class="row"><h1 style="flex:1">MES <span>v3</span></h1><${LangToggle} save=${false} /></div>
    <${Msg} ...${a.msg} />
    <label class="f">${tr("Identifiant (compte Windows ou local)")}<input value=${u} onInput=${e => setU(e.target.value)} autofocus autocomplete="username" /></label>
    <label class="f">${tr("Mot de passe")}<input type="password" value=${p} onInput=${e => setP(e.target.value)} autocomplete="current-password" /></label>
    <button class="primary" disabled=${a.busy || !u || !p}>${tr("Se connecter")}</button>
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
  const PER = [[1, "24 h"], [7, N_("7 j")], [31, N_("31 j")]];
  return html`<h1>${tr("Arrêts")}</h1>
    <${Msg} ...${a.msg} />
    <div class="row" style="margin-bottom:12px">
      <div class="tabs" style="margin:0;border:none">
        <button class=${onlyTodo ? "on" : ""} onClick=${() => setTodo(true)}>${tr("À qualifier")}</button>
        <button class=${!onlyTodo ? "on" : ""} onClick=${() => setTodo(false)}>${tr("Tous")}</button>
      </div><span class="spacer"></span>
      <div class="seg light" role="group" aria-label=${tr("Période")}>${PER.map(([v, l]) => html`<button type="button" class=${days === v ? "on" : ""} onClick=${() => setDays(v)}>${tr(l)}</button>`)}</div>
    </div>
    <${Msg} error=${list.error} />
    <div class="table-wrap"><table>
      <thead><tr><th>${tr("Ligne")}</th><th>${tr("Début")}</th><th>${tr("Fin")}</th><th class="num">${tr("Durée")}</th><th>${tr("Code")}</th><th>${tr("Cause")}</th><th>${tr("Commentaire")}</th><th></th></tr></thead>
      <tbody>${(list.data || []).map(s => html`<tr>
        <td>${s.machine}</td><td>${dt(s.starts_at)}</td><td>${s.ends_at ? dt(s.ends_at) : html`<span class="badge bad">${tr("en cours")}</span>`}</td>
        <td class="num">${dur(s.duree_s)}</td><td>${s.plc_code ?? ""}</td>
        <td>${s.planned ? html`<span class="badge idle">${tr("planifié")}</span>` : s.cause ? html`${s.cause} <span class="muted">(${s.qualified_by})</span>` : html`<span class="badge warn">${tr("à qualifier")}</span>`}</td>
        <td>${s.comment || ""}</td>
        <td class="row" style="flex-wrap:nowrap"><button onClick=${() => setQual(s)}>${tr("Qualifier")}</button>
          ${can(user, "chef_equipe", "operateur") && s.ends_at && html`<button class="iconbtn" onClick=${() => setSplit(s)} title=${tr("Découper en deux")} aria-label=${tr("Découper en deux")}><${Icon} d=${ICON.cut} /></button>`}</td>
      </tr>`)}</tbody></table></div>
    ${list.data && !list.data.length && html`<p class="muted">${tr("Aucun arrêt.")}</p>`}
    <h2>${tr("Pareto")} <span class="sub" style="font-weight:600">${tr("non planifiés · jj:hh:mm:ss")}</span></h2>
    <div class="panel"><${Pareto} height=${260} fmt=${dhms} empty=${tr("Aucun arrêt.")}
      items=${(par.data || []).map(p => ({ label: p.cause, v: p.minutes * 60, sub: tr("{n} arrêts", { n: p.nombre }), color: p.category === "non_qualifie" ? "var(--orange)" : null }))} /></div>
    ${qual && html`<${QualifyDialog} stop=${qual} causes=${causes.data || []} onClose=${() => setQual(null)}
        onDone=${() => { setQual(null); a.setMsg({ ok: tr("Arrêt qualifié.") }); reload(); }} />`}
    ${split && html`<${SplitDialog} stop=${split} onClose=${() => setSplit(null)}
        onDone=${() => { setSplit(null); a.setMsg({ ok: tr("Arrêt découpé : qualifiez les deux parties.") }); setTodo(false); reload(); }} />`}`;
}

function QualifyDialog({ stop, causes, onClose, onDone }) {
  const [cause, setCause] = useState(stop.cause_id || ""), [comment, setComment] = useState(stop.comment || "");
  const a = useAction();
  const cats = [...new Set(causes.map(c => c.category))];
  const save = async () => { if (await a.run(() => api(`/arrets/${stop.id}/qualifier`, { method: "POST", body: { cause_id: +cause, comment: comment || null } }))) onDone(); };
  return html`<${Dialog} title=${`${stop.machine} · ${dt(stop.starts_at)} · ${dur(stop.duree_s)}`} onClose=${onClose}>
    <${Msg} ...${a.msg} />
    <div style="display:grid;gap:12px">
      <label class="f">${tr("Cause")}<select value=${cause} onChange=${e => setCause(e.target.value)}>
        <option value="">${tr("Choisir…")}</option>
        ${cats.map(c => html`<optgroup label=${c}>${causes.filter(x => x.category === c).map(x => html`<option value=${x.id}>${x.label}</option>`)}</optgroup>`)}
      </select></label>
      <label class="f">${tr("Commentaire")}<textarea rows="3" value=${comment} onInput=${e => setComment(e.target.value)}></textarea></label>
      <div class="row"><span class="spacer"></span><button onClick=${onClose}>${tr("Annuler")}</button><button class="primary" disabled=${!cause || a.busy} onClick=${save}>${tr("Enregistrer")}</button></div>
    </div></${Dialog}>`;
}

function SplitDialog({ stop, onClose, onDone }) {
  const mid = new Date((new Date(stop.starts_at).getTime() + (stop.ends_at ? new Date(stop.ends_at).getTime() : Date.now())) / 2);
  const [at, setAt] = useState(toLocalInput(mid));
  const a = useAction();
  const save = async () => { if (await a.run(() => api(`/arrets/${stop.id}/decouper`, { method: "POST", body: { at: fromLocalInput(at) } }))) onDone(); };
  return html`<${Dialog} title=${tr("Découper l'arrêt")} onClose=${onClose}>
    <p class="muted">${stop.machine} · ${dt(stop.starts_at)} → ${stop.ends_at ? hm(stop.ends_at) : "…"}</p>
    <${Msg} ...${a.msg} />
    <label class="f">${tr("Découper à")}<input type="datetime-local" value=${at} onInput=${e => setAt(e.target.value)} /></label>
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button onClick=${onClose}>${tr("Annuler")}</button><button class="primary" disabled=${a.busy} onClick=${save}>${tr("Découper")}</button></div>
  </${Dialog}>`;
}

// ----------------------------------------------------------------------------- alarmes
const PRIO = { critique: "bad", haute: "bad", moyenne: "warn", basse: "idle" };
function Alarms() {
  const [active, setActive] = useState(true), [q, setQ] = useState(""), [days, setDays] = useState(7);
  const list = useApi(`/alarmes?actives=${active}&jours=${days}&q=${encodeURIComponent(q)}`, [], 5000);
  const a = useAction();
  return html`<h1>${tr("Alarmes")}</h1><${Msg} ...${a.msg} /><${Msg} error=${list.error} />
    <div class="row" style="margin-bottom:12px">
      <div class="tabs" style="margin:0;border:none">
        <button class=${active ? "on" : ""} onClick=${() => setActive(true)}>${tr("En cours")}</button>
        <button class=${!active ? "on" : ""} onClick=${() => setActive(false)}>${tr("Historique")}</button></div>
      <input placeholder=${tr("Rechercher…")} value=${q} onInput=${e => setQ(e.target.value)} />
      ${!active && html`<select value=${days} onChange=${e => setDays(+e.target.value)}><option value="1">24 h</option><option value="7">${tr("7 jours")}</option><option value="31">${tr("31 jours")}</option><option value="365">${tr("1 an")}</option></select>`}
      <span class="spacer"></span>
      <button onClick=${() => a.run(() => api("/alarmes/acquitter-tout", { method: "POST" }), tr("Alarmes acquittées.")).then(list.reload)}>${tr("Tout acquitter")}</button>
    </div>
    <div class="table-wrap"><table><thead><tr><th>${tr("Priorité")}</th><th>${tr("Machine")}</th><th>${tr("Message")}</th><th>${tr("Apparition")}</th><th>${tr("Disparition")}</th><th>${tr("Acquittement")}</th><th></th></tr></thead>
      <tbody>${(list.data || []).map(x => html`<tr>
        <td><span class=${"badge " + PRIO[x.priority]}>${tr(x.priority)}</span></td><td>${x.machine}</td>
        <td>${x.cleared_at ? x.message : html`<b>${x.message}</b>`}</td><td>${dt(x.raised_at)}</td>
        <td>${x.cleared_at ? dt(x.cleared_at) : html`<span class="badge bad">${tr("active")}</span>`}</td>
        <td>${x.ack_at ? html`${dt(x.ack_at)} <span class="muted">${x.ack_by}</span>` : ""}</td>
        <td>${!x.ack_at && html`<button onClick=${() => a.run(() => api(`/alarmes/${x.id}/acquitter`, { method: "POST" })).then(list.reload)}>${tr("Acquitter")}</button>`}</td></tr>`)}</tbody></table></div>
    ${list.data && !list.data.length && html`<p class="muted">${tr("Aucune alarme.")}</p>`}`;
}

// ----------------------------------------------------------------------------- moteurs
const DRIFT = { normal: "good", attention: "warn", alerte: "bad", arret: "idle", sans_reference: "idle" };
const DRIFT_FR = { normal: N_("Normal"), attention: N_("Attention"), alerte: N_("Alerte"), arret: N_("À l'arrêt"), sans_reference: N_("Sans référence") };
const DRIFT_LABEL = new Proxy(DRIFT_FR, { get: (o, k) => (o[k] ? tr(o[k]) : undefined) });
function Motors({ user }) {
  const list = useApi("/moteurs", [], 30000);
  const settings = useApi("/parametres");
  const [sel, setSel] = useState(null), [filter, setFilter] = useState("");
  const ms = (list.data || []).filter(m => !filter || (m.status || "sans_reference") === filter);
  const counts = {};
  (list.data || []).forEach(m => { const s = m.status || "sans_reference"; counts[s] = (counts[s] || 0) + 1; });
  const selected = sel && (list.data || []).find(m => m.id === sel);
  const st = settings.data || {};
  return html`<h1>${tr("Moteurs")}</h1><${Msg} error=${list.error} />
    <div class="legend" style="margin-bottom:12px">
      <button class=${!filter ? "primary" : ""} onClick=${() => setFilter("")}>${tr("Tous")} ${(list.data || []).length}</button>
      ${Object.keys(DRIFT).map(s => counts[s] ? html`<button class=${filter === s ? "primary" : ""} onClick=${() => setFilter(s)}><span class=${"badge " + DRIFT[s]}>${DRIFT_LABEL[s]}</span> ${counts[s]}</button>` : "")}
      <span class="spacer"></span>
      ${settings.data && html`<${Info}>${tr("Écart du courant moyen ({n} min) à la référence.", { n: st.derive_fenetre_min })}<br />${tr("Attention ±{a} % · alerte ±{b} %.", { a: st.derive_seuil_attention_pct, b: st.derive_seuil_alerte_pct })}</${Info}>`}
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

const MOTOR_PERIODS = [[1, "24 h"], [7, N_("7 j")], [31, N_("31 j")]];
function MotorDetail({ m, user, settings, onChange, onClose }) {
  const [days, setDays] = useState(1);
  const [ref, setRef0] = useState(false);
  const hist = useApi(`/moteurs/${m.id}/historique?jours=${days}`, [m.time]);
  const trend = useApi(m.tag_courant ? `/tendance/${m.tag_courant}?minutes=${days * 1440}` : null, [m.time]);
  const [nominal, setNominal] = useState("");
  const a = useAction();
  const setRef = body => a.run(() => api(`/moteurs/${m.id}/reference`, { method: "POST", body }), r => tr("Référence : {v} A.", { v: num(r.ref_current, 2) })).then(() => { setRef0(false); onChange(); });
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
        <div><span>${tr("Mesuré")}</span><b>${num(m.mean_current)} A</b></div>
        <div><span>${tr("Référence")}</span><b>${num(rc, 1)} A</b></div>
        <div><span>${tr("Écart")}</span><b style=${{ color: col }}>${m.deviation_pct === null || m.deviation_pct === undefined ? "–" : (m.deviation_pct > 0 ? "+" : "") + num(m.deviation_pct) + " %"}</b></div>
      </div>
      <span class="spacer"></span>
      <div class="seg light" role="group" aria-label=${tr("Période")}>${MOTOR_PERIODS.map(([v, l]) => html`<button type="button" class=${days === v ? "on" : ""} onClick=${() => setDays(v)}>${tr(l)}</button>`)}</div>
      ${can(user, "maintenance") && html`<button class="iconbtn" title=${tr("Référence")} aria-label=${tr("Référence")} onClick=${() => setRef0(!ref)}><${Icon} d=${ICON.target} /></button>`}
      <button class="iconbtn" aria-label=${tr("Fermer")} onClick=${onClose}><${Icon} d=${ICON.close} /></button>
    </div>
    <${Msg} ...${a.msg} />
    ${ref && html`<div class="row" style="background:var(--fill2);border-radius:12px;padding:10px 12px">
      <span class="muted">${m.method === "mesure" ? tr("Mesurée") : tr("Nominale")}${m.set_at ? " · " + dt(m.set_at) : ""}</span><span class="spacer"></span>
      <button onClick=${() => setRef({ method: "mesure" })} disabled=${a.busy}>${tr("Recaler sur la mesure")}</button>
      <input class="small" type="number" step="0.1" placeholder="A" value=${nominal} onInput=${e => setNominal(e.target.value)} />
      <button onClick=${() => setRef({ method: "nominal", value: +nominal })} disabled=${!nominal || a.busy}>${tr("Fixer")}</button></div>`}
    <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px">
      <div><div class="row" style="gap:8px;margin-bottom:6px"><b>${tr("Courant")}</b><span class="muted">A</span></div>
        <${Chart} height=${220} unit="A" color=${col} series=${[{ points: cur, color: col }]}
          zones=${rc ? [{ from: rc * (1 - w / 100), to: rc * (1 + w / 100), color: "var(--green-t)" }] : []}
          bands=${rc ? [{ y: rc, color: "var(--green-fg)", label: tr("Réf.") + " " + num(rc, 1) + " A" }] : []} /></div>
      <div><div class="row" style="gap:8px;margin-bottom:6px"><b>${tr("Écart à la référence")}</b><span class="muted">%</span></div>
        <${Chart} height=${220} unit="%" series=${[{ points: pts, color: col }]} empty=${tr("Pas de référence ou pas de mesure.")}
          zones=${[{ from: -w, to: w, color: "var(--green-t)" }, { from: w, to: al, color: "var(--orange-t)" }, { from: -al, to: -w, color: "var(--orange-t)" },
            { from: al, to: Infinity, color: "var(--red-t)" }, { from: -Infinity, to: -al, color: "var(--red-t)" }]} /></div>
    </div>
  </div>`;
}

// ----------------------------------------------------------------------------- catalogue
function Catalog({ user }) {
  const [tab, setTab] = useState("decouverte");
  return html`<h1>${tr("Catalogue automate")}</h1>
    <div class="tabs">${[["decouverte", N_("Découverte")], ["machines", N_("Machines")], ["types", N_("Types de machines")], ["csv", N_("Import / export")]].map(([k, l]) =>
      html`<button class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${tr(l)}</button>`)}</div>
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
      x => tr("Appliqué : {a} machines créées, {b} mises à jour, {c} tags bruts, {d} désactivées. La collecte prend les changements en compte sous une minute.", { a: x.machines_creees, b: x.machines_mises_a_jour, c: x.tags_bruts_crees, d: x.machines_desactivees }));
    if (r) setRun(null);
  };
  const byType = {};
  (run ? run.instances : []).forEach(i => { (byType[i.type] = byType[i.type] || []).push(i); });
  return html`<${Msg} ...${a.msg} />
    <div class="panel row" style="margin-bottom:16px"><b style="flex:1">${tr("Découverte de l'automate")}</b>
      <${Info}>${tr("Parcourt les blocs de données de l'automate, reconnaît les machines d'après les types déclarés et propose de les ajouter avec leurs tags.")}</${Info}>
      <button class="primary" onClick=${start} disabled=${a.busy}>${a.busy ? tr("Découverte…") : tr("Lancer la découverte")}</button></div>
    ${run && html`
      <p>${tr("{a} instances reconnues sur {b} nœuds · {c} tags dans des structures inconnues · {d} machines disparues.", { a: run.instances.length, b: run.total_nodes, c: run.unknown_tags, d: run.disappeared.length })}</p>
      ${Object.entries(byType).map(([t, is]) => html`<h2>${t} (${is.length})
          <button style="margin-left:8px" onClick=${() => setSel({ ...sel, ...Object.fromEntries(is.map(i => [i.base_node, true])) })}>${tr("Tout cocher")}</button>
          <button onClick=${() => setSel({ ...sel, ...Object.fromEntries(is.map(i => [i.base_node, false])) })}>${tr("Tout décocher")}</button></h2>
        <div class="table-wrap" style="max-height:320px;overflow:auto"><table><thead><tr><th></th><th>${tr("Instance")}</th><th>${tr("Bloc")}</th><th>${tr("État")}</th><th class="num">${tr("Couverture")}</th><th>${tr("Membres manquants")}</th></tr></thead>
          <tbody>${is.map(i => html`<tr><td><input type="checkbox" checked=${!!sel[i.base_node]} onChange=${e => setSel({ ...sel, [i.base_node]: e.target.checked })} /></td>
            <td>${i.name}</td><td>${i.db}</td><td><span class=${"badge " + (i.status === "nouveau" ? "good" : i.status === "existant" ? "idle" : "warn")}>${tr(i.status)}</span></td>
            <td class="num">${pct(i.coverage)}</td><td>${i.missing.join(", ")}</td></tr>`)}</tbody></table></div>`)}
      ${run.unknown.length > 0 && html`<h2>${tr("Structures non reconnues")}</h2><div class="table-wrap"><table><thead><tr><th class="num">${tr("Instances")}</th><th>${tr("Membres")}</th><th>${tr("Exemples")}</th></tr></thead>
        <tbody>${run.unknown.slice(0, 20).map(u => html`<tr><td class="num">${u.count}</td><td style="font-size:12px">${u.members.join(", ")}</td><td style="font-size:12px">${u.examples.join(", ")}</td></tr>`)}</tbody></table></div>
`}
      ${run.disappeared.length > 0 && html`<h2>${tr("Machines disparues de l'automate")}</h2><p>${run.disappeared.map(d => d.name).join(", ")}</p>`}
      <div class="panel row" style="margin-top:16px">
        <label class="row"><input type="checkbox" checked=${opts.include_unknown} onChange=${e => setOpts({ ...opts, include_unknown: e.target.checked })} /> ${tr("importer aussi les structures inconnues comme tags bruts")}</label>
        <label class="row"><input type="checkbox" checked=${opts.disable_missing} onChange=${e => setOpts({ ...opts, disable_missing: e.target.checked })} /> ${tr("désactiver les machines disparues")}</label>
        <span class="spacer"></span><button class="primary" disabled=${a.busy} onClick=${apply}>${tr("Appliquer ({n} instances)", { n: Object.values(sel).filter(Boolean).length })}</button></div>`}`;
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
    <div class="row" style="margin-bottom:12px"><select value=${type} onChange=${e => setType(e.target.value)}><option value="">${tr("Tous les types")}</option>${(types.data || []).map(t => html`<option value=${t.code}>${t.label}</option>`)}</select>
      <input placeholder=${tr("Filtrer…")} value=${q} onInput=${e => setQ(e.target.value)} /><span class="muted">${rows.length} ${tr("machines")}</span></div>
    <div class="table-wrap"><table><thead><tr><th>${tr("Machine")}</th><th>${tr("Type")}</th><th>${tr("Zone")}</th><th>${tr("Attributs")}</th><th class="num">${tr("Tags")}</th><th>${tr("Active")}</th><th></th></tr></thead>
      <tbody>${rows.slice(0, 500).map(m => html`<tr><td>${m.name}</td><td>${m.type_label}</td><td>${m.area}</td>
        <td style="font-size:12px">${Object.entries(m.attributes || {}).map(([k, v]) => `${k} = ${v}`).join(" · ")}</td><td class="num">${m.tags}</td>
        <td>${m.enabled ? tr("oui") : html`<span class="badge idle">${tr("non")}</span>`}</td>
        <td>${user.role === "admin" && html`<button onClick=${() => setEd({ ...m, attributes: { ...(m.attributes || {}) } })}>${tr("Modifier")}</button>`}</td></tr>`)}</tbody></table></div>
    ${ed && html`<${Dialog} title=${tr("Machine") + " " + ed.name} onClose=${() => setEd(null)}>
      <div style="display:grid;gap:12px">
        <label class="f">${tr("Nom")}<input value=${ed.name} onInput=${e => setEd({ ...ed, name: e.target.value })} /></label>
        <label class="f">${tr("Zone (chemin, séparé par /)")}<input value=${ed.area || ""} onInput=${e => setEd({ ...ed, area: e.target.value })} /></label>
        ${((tdef && tdef.definition.attributes) || []).map(at => html`<label class="f">${at.name}${at.unit ? ` (${at.unit})` : ""}${at.required ? " *" : ""}
          <input value=${ed.attributes[at.name] ?? ""} onInput=${e => setEd({ ...ed, attributes: { ...ed.attributes, [at.name]: e.target.value } })} /></label>`)}
        <label class="row"><input type="checkbox" checked=${ed.enabled} onChange=${e => setEd({ ...ed, enabled: e.target.checked })} /> ${tr("active (collectée)")}</label>
        <div class="row"><span class="spacer"></span><button onClick=${() => setEd(null)}>${tr("Annuler")}</button><button class="primary" onClick=${async () => {
          const attrs = Object.fromEntries(Object.entries(ed.attributes).map(([k, v]) => [k, v !== "" && !isNaN(+v) ? +v : v]));
          if (await a.run(() => api(`/catalogue/machines/${ed.id}`, { method: "PATCH", body: { name: ed.name, area: ed.area, attributes: attrs, enabled: ed.enabled } }), tr("Machine mise à jour."))) { setEd(null); list.reload(); }
        }}>${tr("Enregistrer")}</button></div></div></${Dialog}>`}`;
}

function Types() {
  const types = useApi("/catalogue/types");
  return html`${(types.data || []).map(t => html`<div class="panel" style="margin-bottom:12px"><div class="row"><b>${t.label}</b><span class="muted">${t.code} · v${t.version}</span><span class="spacer"></span><span>${t.machines} ${tr("machines")}</span></div>
      <div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>${tr("Membre")}</th><th>${tr("Rôle")}</th><th>${tr("Unité")}</th><th>${tr("Deadband")}</th><th>${tr("Signature")}</th></tr></thead>
        <tbody>${t.definition.members.map(m => html`<tr><td>${m.path}${m.optional ? html` <span class="muted">${tr("(optionnel)")}</span>` : ""}</td><td>${m.role || ""}</td><td>${m.unit || ""}</td>
          <td>${m.deadband_kind !== "none" ? `${m.deadband_kind === "percent" ? m.deadband_value + " %" : m.deadband_value}` : ""}</td><td>${t.definition.signature.some(s => s.path === m.path) ? tr("oui") : ""}</td></tr>`)}</tbody></table></div></div>`)}`;
}

function CsvIO() {
  const a = useAction();
  const upload = (kind) => async e => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    await a.run(() => api(`/catalogue/import/${kind}`, { method: "POST", body: fd }),
      r => tr("{n} lignes traitées", { n: r.importees ?? r.mis_a_jour }) + (r.erreurs.length ? ", " + tr("{n} erreurs :", { n: r.erreurs.length }) + " " + r.erreurs.slice(0, 5).join(" ; ") : "."));
    e.target.value = "";
  };
  return html`<${Msg} ...${a.msg} />
    <div class="grid">
      <div class="panel"><h2 class="row" style="margin-top:0">${tr("Machines")} <${Info}>${tr("machine ; type ; zone ; noeud_opcua ; actif ; attr:…")}</${Info}></h2>
        <div class="row"><a class="btn" href="/api/catalogue/export/machines.csv">${tr("Exporter")}</a><label class="btn">${tr("Importer…")}<input type="file" accept=".csv" hidden onChange=${upload("machines")} /></label></div></div>
      <div class="panel"><h2 class="row" style="margin-top:0">${tr("Tags")} <${Info}>${tr("Unités, rôles, deadbands, historisation et activation, par node_id.")}</${Info}></h2>
        <div class="row"><a class="btn" href="/api/catalogue/export/tags.csv">${tr("Exporter")}</a><label class="btn">${tr("Importer…")}<input type="file" accept=".csv" hidden onChange=${upload("tags")} /></label></div></div>
    </div>`;
}

// ----------------------------------------------------------------------------- utilisateurs, journal
function Users() {
  const list = useApi("/utilisateurs");
  const journal = useApi("/journal?limit=100");
  const [f, setF] = useState({ username: "", display_name: "", role: "operateur", password: "", active: true });
  const a = useAction();
  return html`<h1>${tr("Utilisateurs")}</h1><${Msg} ...${a.msg} />
    <div class="table-wrap"><table><thead><tr><th>${tr("Identifiant")}</th><th>${tr("Nom")}</th><th>${tr("Rôle")}</th><th>${tr("Source")}</th><th>${tr("Langue")}</th><th>${tr("Actif")}</th><th>${tr("Dernière connexion")}</th></tr></thead>
      <tbody>${(list.data || []).map(u => html`<tr><td>${u.username}</td><td>${u.display_name}</td>
        <td><select value=${u.role} onChange=${e => a.run(() => api("/utilisateurs", { method: "POST", body: { ...u, role: e.target.value, password: null } }), tr("Rôle modifié.")).then(list.reload)}>
          ${Object.entries(ROLE_LABEL).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select></td>
        <td>${u.source === "ldap" ? "Active Directory" : tr("local")}</td>
        <td><select value=${u.langue || ""} onChange=${e => a.run(() => api("/utilisateurs", { method: "POST", body: { ...u, langue: e.target.value || null, password: null } }), tr("Langue modifiée.")).then(list.reload)}>
          <option value="">${tr("Navigateur")}</option>${Object.entries(LANGS).map(([k, [name]]) => html`<option value=${k}>${name}</option>`)}</select></td>
        <td><input type="checkbox" checked=${u.active} onChange=${e => a.run(() => api("/utilisateurs", { method: "POST", body: { ...u, active: e.target.checked, password: null } })).then(list.reload)} /></td>
        <td>${dt(u.last_login)}</td></tr>`)}</tbody></table></div>
    <h2 class="row">${tr("Compte local")} <${Info}>${tr("Les comptes Active Directory sont créés à la première connexion. Mot de passe local : 8 caractères minimum.")}</${Info}></h2>
    <div class="panel row" style="align-items:end">
      <label class="f">${tr("Identifiant")}<input value=${f.username} onInput=${e => setF({ ...f, username: e.target.value })} /></label>
      <label class="f">${tr("Nom affiché")}<input value=${f.display_name} onInput=${e => setF({ ...f, display_name: e.target.value })} /></label>
      <label class="f">${tr("Rôle")}<select value=${f.role} onChange=${e => setF({ ...f, role: e.target.value })}>${Object.entries(ROLE_LABEL).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select></label>
      <label class="f">${tr("Mot de passe")}<input type="password" value=${f.password} onInput=${e => setF({ ...f, password: e.target.value })} /></label>
      <button class="primary" disabled=${!f.username || f.password.length < 8} onClick=${async () => {
        if (await a.run(() => api("/utilisateurs", { method: "POST", body: f }), tr("Compte créé."))) { setF({ ...f, username: "", display_name: "", password: "" }); list.reload(); }
      }}>${tr("Créer")}</button></div>
    <h2>${tr("Journal")}</h2>
    <div class="table-wrap" style="max-height:400px;overflow:auto"><table><thead><tr><th>${tr("Date")}</th><th>${tr("Utilisateur")}</th><th>${tr("Action")}</th><th>${tr("Objet")}</th><th>${tr("Détails")}</th></tr></thead>
      <tbody>${(journal.data || []).map(j => html`<tr><td>${dt(j.at)}</td><td>${j.actor}</td><td>${j.action}</td><td>${j.entity ? `${j.entity} ${j.entity_id ?? ""}` : ""}</td>
        <td style="font-size:12px">${j.details ? JSON.stringify(j.details) : ""}</td></tr>`)}</tbody></table></div>`;
}

// ----------------------------------------------------------------------------- application
// [route, libellé, composant, rôles autorisés, groupe du menu, vue « une hauteur d'écran »]
const PAGES = [
  ["", N_("Synthèse"), Synthese, null, "top", true],
  ["ligne", N_("Ligne"), Ligne, null, "hidden", true],
  ["planning", N_("Planning"), Planning, null, "outils", true],
  ["arrets", N_("Arrêts"), Stops, null, "outils"],
  ["alarmes", N_("Alarmes"), Alarms, null, "outils"],
  ["moteurs", N_("Moteurs"), Motors, null, "outils"],
  ["energie", N_("Énergie"), Energie, null, "outils", true],
  ["recettes", N_("Recettes"), Recettes, null, "outils", true],
  ["analyse", N_("Analyse"), Analyse, null, "outils", true],
  ["catalogue", N_("Catalogue"), Catalog, ["admin"], "reglages"],
  ["utilisateurs", N_("Utilisateurs"), Users, ["admin"], "reglages"],
];

function useHash() {
  const [h, setH] = useState(location.hash);
  useEffect(() => { const f = () => setH(location.hash); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  return h.replace(/^#\/?/, "").split("/");
}

const LOGO = "M3 20V9l6 4V9l6 4V5h6v15z";
const LINE_DOT = { good: "#06D16F", warn: "#FF9500", bad: "#FF3B30", idle: "#A9BDD6" };

// langue du compte : appliquée à la connexion ; un compte sans langue reprend celle de ce navigateur
function applyUserLang(u) {
  if (!u) return u;
  if (u.langue && LANGS[u.langue]) chooseLang(u.langue, false);
  else if (u.auth !== false) chooseLang(getLang(), true);
  return u;
}

function App() {
  const [user, setUser0] = useState(undefined);
  const setUser = u => setUser0(applyUserLang(u));
  useLang();  // nouveau rendu de toute l'interface au changement de langue
  const route = useHash();
  const counts = useApi(user ? "/tableau-de-bord" : null, [user], 15000);
  useEffect(() => {
    api("/session").then(setUser, () => setUser(null));
    const out = () => setUser(null);
    addEventListener("mes-logout", out);
    return () => removeEventListener("mes-logout", out);
  }, []);
  if (user === undefined) return html`<p class="muted" style="padding:24px">${tr("Chargement…")}</p>`;
  if (!user) return html`<${Login} onLogin=${u => api("/session").then(setUser, () => setUser(u))} />`;
  const pages = PAGES.filter(p => !p[3] || p[3].includes(user.role));
  const page = pages.find(p => p[0] === route[0]) || pages[0];
  const Comp = page[2];
  const c = counts.data || {};
  const badge = { alarmes: [c.alarmes_non_acquittees, ""], arrets: [c.arrets_a_qualifier, "warn"], moteurs: [c.moteurs_en_alerte, ""] };
  const link = p => html`<a href=${"#/" + p[0]} class=${p === page ? "on" : ""}>${tr(p[1])}${badge[p[0]] && badge[p[0]][0] ? html`<span class=${"cnt " + badge[p[0]][1]}>${badge[p[0]][0]}</span>` : ""}</a>`;
  const group = g => pages.filter(p => p[4] === g).map(link);
  return html`<div class="layout">
    <nav class="side" aria-label=${tr("Navigation principale")}>
      <div class="brand"><span class="logo"><${Icon} d=${LOGO} /></span>MES</div>
      ${group("top")}
      ${(c.lignes || []).length > 0 && html`<p class="grp">${tr("Lignes")}</p>`}
      ${(c.lignes || []).map(l => html`<a href=${"#/ligne/" + l.id} class=${page[0] === "ligne" && +route[1] === l.id ? "on" : ""}>
        <span class="ldot" style=${{ background: LINE_DOT[lineState(l).kind] }}></span>${l.name}${l.unqualified_count ? html`<span class="cnt warn">${l.unqualified_count}</span>` : ""}</a>`)}
      <p class="grp">${tr("Outils")}</p>${group("outils")}
      ${group("reglages").length > 0 && html`<p class="grp">${tr("Réglages")}</p>`}${group("reglages")}
      <div class="who">${user.display_name || user.username}<br />${ROLE_LABEL[user.role] !== user.display_name ? html`${ROLE_LABEL[user.role]}<br />` : ""}
        ${user.auth === false ? html`<span>${tr("Accès sans identifiant")}</span>` : html`<a href="#" onClick=${e => { e.preventDefault(); api("/session", { method: "DELETE" }).finally(() => setUser(null)); }}>${tr("Se déconnecter")}</a>`}
        <div style="margin-top:8px">${tr("version")} ${user.version}</div></div></nav>
    ${page[5]
      ? html`<main class="fit"><${Comp} user=${user} route=${route} key=${page[0] + "/" + (route[1] || "")} /></main>`
      : html`<main><${Band} over=${"MES · " + (page[4] === "reglages" ? tr("Réglages") : tr("Outils"))} title=${tr(page[1])} />
          <div class="page"><${Comp} user=${user} route=${route} key=${route.join("/")} /></div></main>`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
