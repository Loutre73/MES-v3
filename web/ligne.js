// Détail d'une ligne : frise du poste où l'on qualifie un arrêt en le sélectionnant, synoptique des moteurs,
// production par heure, recette planifiée comparée à la production, alarmes. La vue tient dans la hauteur de l'écran.
import { html, useState, useEffect } from "./vendor/preact-htm.module.js";
import {
  api, useApi, Msg, Band, Tile, Pill, Frise, Ticks, Bars, Chart, Info, Icon, ICON, useToast, lineState, oeeKind, can,
  hm, num, pct, dur, dt, toLocalInput, fromLocalInput, fmtDay,
} from "./lib.js";

const T = v => new Date(v).getTime();
const KIND_COLOR = { good: "var(--green)", warn: "var(--orange)", bad: "var(--red)", idle: "var(--text2)" };
const DRIFT_KIND = { normal: "good", attention: "warn", alerte: "bad", arret: "idle", sans_reference: "idle" };
const DRIFT_LABEL = { normal: "Normal", attention: "Attention", alerte: "Alerte", arret: "À l'arrêt", sans_reference: "Sans référence" };
// causes proposées en premier (les plus fréquentes sur une ligne) ; les autres sont dans la liste
const QUICK = ["MEC", "ELEC", "REG", "MAT", "AVAL", "QUAL"];

export function Ligne({ user, route }) {
  const lid = +route[1], shiftId = route[2] ? +route[2] : null;
  const detail = useApi(`/lignes/${lid}${shiftId ? "?shift_id=" + shiftId : ""}`, [lid, shiftId], 15000);
  const board = useApi("/tableau-de-bord", [], 30000);
  const causes = useApi("/arrets/causes");
  const [open, setOpen] = useState(null);
  const [sel, setSel] = useState(null);
  const [toast, showToast] = useToast();
  const d = detail.data;

  // sélection initiale : le premier arrêt à qualifier du poste
  useEffect(() => {
    if (!d || sel) return;
    const first = d.arrets.find(s => !s.cause_id && !s.planned);
    if (first) setSel(first.id);
  }, [d]);

  if (detail.error && !d) return html`<${Msg} error=${detail.error} />`;
  if (!d) return html`<p class="muted">Chargement…</p>`;
  const l = d.ligne, s = d.shift;
  const st = lineState(l);
  const stops = d.arrets;
  const todo = stops.filter(x => !x.cause_id && !x.planned);
  const selStop = stops.find(x => x.id === sel) || null;
  const go = id => { location.hash = `#/ligne/${lid}${id ? "/" + id : ""}`; setSel(null); };

  const qualify = async (stop, causeId, comment) => {
    try {
      await api(`/arrets/${stop.id}/qualifier`, { method: "POST", body: { cause_id: causeId, comment: comment || null } });
      const c = (causes.data || []).find(x => x.id === causeId);
      showToast(`Arrêt de ${hm(stop.starts_at)} qualifié : ${c ? c.label : "cause enregistrée"}.`);
      // passe au prochain arrêt à qualifier
      const next = todo.find(x => x.id !== stop.id && T(x.starts_at) > T(stop.starts_at)) || todo.find(x => x.id !== stop.id);
      setSel(next ? next.id : stop.id);
      detail.reload();
    } catch (e) { showToast(e.message, true); }
  };

  const lines = board.data ? board.data.lignes : [];
  return html`<div class="view">
    <${Band} over=${html`<a href="#/">Synthèse</a> › Ligne`} title=${html`${l.name} <${Pill} kind=${st.kind}>${st.label}</${Pill}>`}
      back=${html`<a class="backlink" href="#/" aria-label="Retour à la synthèse"><${Icon} d=${ICON.back} /></a>`}>
      ${[["TRS", pct(s?.oee)], ["Dispo", pct(s?.availability)], ["Perf", pct(s?.performance)],
         ["Production", `${num(s?.tonnage_t, 0)} / ${num(s?.target_t, 0)} t`], ["Débit", `${num(l.debit, 0)} t/h`], ["Énergie", `${num(s?.kwh_per_t, 1)} kWh/t`]]
        .map(([k, v]) => html`<div class="kv"><span>${k}</span><b>${v}</b></div>`)}
      ${lines.length > 1 && html`<div class="seg" role="group" aria-label="Lignes">${lines.map(x => html`<button type="button" class=${x.id === lid ? "on" : ""}
        onClick=${() => { location.hash = `#/ligne/${x.id}`; setSel(null); }}>${x.name}</button>`)}</div>`}
    </${Band}>

    <article class="tile" style="flex:none;gap:8px">
      <div class="th">
        <h2>Frise du poste</h2>
        ${s && html`<span class="sub">${fmtDay.format(new Date(s.starts_at))} · ${hm(s.starts_at)}–${hm(s.ends_at)}</span>
          ${s.team && html`<${Pill} kind="info">${s.team}</${Pill}>`}${s.recipe_label && html`<${Pill} kind="idle">${s.recipe_label}</${Pill}>`}`}
        <span class="spacer"></span>
        <${Info} label="Légende"><div class="legend"><span><i style="background:var(--green)"></i>Marche</span><span><i style="background:var(--orange)"></i>Ralenti</span>
          <span><i style="background:var(--red)"></i>Arrêt qualifié</span><span><i style="background:repeating-linear-gradient(135deg,var(--red) 0 3px,#FF8A80 3px 6px)"></i>À qualifier</span>
          <span><i style="background:repeating-linear-gradient(135deg,var(--muted) 0 3px,var(--fill) 3px 6px)"></i>Planifié</span></div></${Info}>
        <button type="button" class="xb" disabled=${!d.navigation.precedent} onClick=${() => go(d.navigation.precedent)} aria-label="Poste précédent" title="Poste précédent"><${Icon} d=${ICON.back} /></button>
        <button type="button" class="xb" disabled=${!d.navigation.suivant} onClick=${() => go(d.navigation.suivant)} aria-label="Poste suivant" title="Poste suivant"><${Icon} d=${ICON.next} /></button>
      </div>
      ${s ? html`<${Frise} start=${s.starts_at} end=${s.ends_at} stops=${stops} slows=${d.ralentis} sel=${sel} onPick=${x => setSel(x.id)} kind=${s.kind} />
        <${Ticks} start=${s.starts_at} end=${s.ends_at} every=${1} />
        <${StopPanel} stop=${selStop} todo=${todo} stops=${stops} causes=${causes.data || []} user=${user}
          onSelect=${setSel} onQualify=${qualify} onSplit=${() => { detail.reload(); showToast("Arrêt découpé : qualifiez les deux parties."); }} onError=${e => showToast(e, true)} />`
        : html`<p class="empty">Aucun poste. <a href="#/planning">Planning →</a></p>`}
    </article>

    <section class="tiles" style="grid-template-columns:minmax(0,1.15fr) minmax(0,1fr) minmax(0,1fr);grid-template-rows:minmax(0,1fr) minmax(0,1fr)">
      <${Tile} id="syno" title="Moteurs" open=${open} onToggle=${setOpen} cls="syno" col="1 / 2" row="1 / 3"
        detail=${html`<${Synoptic} motors=${d.moteurs} big />`}>
        <${Synoptic} motors=${d.moteurs} />
      </${Tile}>
      <${Tile} id="prod" title="Production par heure" open=${open} onToggle=${setOpen} col="2 / 4" row="1 / 2"
        extra=${html`<${Info}>Barres : tonnes produites. Tirets : objectif horaire${s?.target_t ? ` (${num(s.target_t / Math.max(1, d.production_h.length), 0)} t/h)` : ""}.</${Info}>`}
        detail=${html`<${ProdDetail} rows=${d.production_h} />`}>
        <div style="flex:1;min-height:0"><${Bars} data=${d.production_h.map(r => ({ label: hm(r.h).slice(0, 2) + "h", v: r.t, target: r.objectif,
          color: r.t === null ? "var(--fill)" : r.objectif && r.t < 0.9 * r.objectif ? "var(--orange)" : "var(--green)" }))} unit="t" labelEvery=${1} /></div>
      </${Tile}>
      <${Tile} id="recette" title="Recette vs réel" open=${open} onToggle=${setOpen} col="2 / 3" row="2 / 3"
        detail=${html`<${RecipeCompare} shift=${s} big />`}>
        <${RecipeCompare} shift=${s} />
      </${Tile}>
      <${Tile} id="alarmes" title="Alarmes" open=${open} onToggle=${setOpen} col="3 / 4" row="2 / 3"
        extra=${d.alarmes.length ? html`<${Pill} kind="bad">${d.alarmes.length}</${Pill}>` : ""}
        detail=${html`<${AlarmList} alarms=${d.alarmes} onAck=${async id => { await api(`/alarmes/${id}/acquitter`, { method: "POST" }); detail.reload(); }} big />`}>
        <${AlarmList} alarms=${d.alarmes} onAck=${async id => { await api(`/alarmes/${id}/acquitter`, { method: "POST" }); detail.reload(); }} />
      </${Tile}>
    </section>
    ${toast}
  </div>`;
}

// ----------------------------------------------------------------------------- panneau de l'arrêt sélectionné
function StopPanel({ stop, todo, stops, causes, user, onSelect, onQualify, onSplit, onError }) {
  const [comment, setComment] = useState("");
  const [other, setOther] = useState("");
  const [cut, setCut] = useState(null);
  useEffect(() => { setComment(stop?.comment || ""); setOther(""); setCut(null); }, [stop?.id]);
  if (!stop) return html`<div class="lst"><div>${todo.length ? html`<${Pill} kind="warn">${todo.length} à qualifier</${Pill}><span class="sub">Touchez un arrêt sur la frise.</span>` : html`<${Pill} kind="good">Tout est qualifié</${Pill}>`}</div></div>`;
  const i = stops.findIndex(x => x.id === stop.id);
  const quick = QUICK.map(c => causes.find(x => x.code === c)).filter(Boolean);
  const canSplit = can(user, "chef_equipe", "operateur") && stop.ends_at;
  const doCut = async () => {
    try { await api(`/arrets/${stop.id}/decouper`, { method: "POST", body: { at: fromLocalInput(cut) } }); setCut(null); onSplit(); }
    catch (e) { onError(e.message); }
  };
  return html`<div style="display:grid;grid-template-columns:minmax(220px,.9fr) minmax(0,2fr);gap:16px;background:var(--fill2);border-radius:12px;padding:12px 14px">
    <div style="display:flex;flex-direction:column;gap:6px;min-width:0">
      <div style="display:flex;align-items:center;gap:8px"><b style="font-size:18px" class="num">${hm(stop.starts_at)} → ${stop.ends_at ? hm(stop.ends_at) : "en cours"}</b>
        ${stop.planned ? html`<${Pill} kind="idle">Planifié</${Pill}>` : stop.cause_id ? html`<${Pill} kind="good">${stop.cause}</${Pill}>` : html`<${Pill} kind="warn">À qualifier</${Pill}>`}</div>
      <span class="sub num">${dur(stop.duree_s)}${stop.plc_code !== null && stop.plc_code !== undefined ? " · code " + stop.plc_code : ""}${stop.qualified_by ? " · " + stop.qualified_by : ""}</span>
      <div class="row" style="gap:6px;margin-top:auto">
        <button type="button" class="iconbtn" disabled=${i <= 0} onClick=${() => onSelect(stops[i - 1].id)} aria-label="Arrêt précédent" title="Arrêt précédent"><${Icon} d=${ICON.back} /></button>
        <button type="button" class="iconbtn" disabled=${i >= stops.length - 1} onClick=${() => onSelect(stops[i + 1].id)} aria-label="Arrêt suivant" title="Arrêt suivant"><${Icon} d=${ICON.next} /></button>
        ${canSplit && html`<button type="button" class=${"iconbtn" + (cut ? " dark" : "")} onClick=${() => setCut(cut ? null : toLocalInput(new Date((T(stop.starts_at) + T(stop.ends_at)) / 2)))} aria-label="Découper" title="Découper"><${Icon} d=${ICON.cut} /></button>`}
        ${todo.length > 0 && html`<span class="spacer"></span><${Pill} kind="warn">${todo.length} à qualifier</${Pill}>`}
      </div>
      ${cut && html`<div class="row" style="gap:6px"><input type="datetime-local" value=${cut} onInput=${e => setCut(e.target.value)} aria-label="Instant de découpe" />
        <button class="dark" onClick=${doCut}>Découper</button></div>`}
    </div>
    <div style="display:flex;flex-direction:column;gap:8px;min-width:0">
      <div class="chips">
        ${quick.map(c => html`<button type="button" class=${"chip" + (stop.cause_id === c.id ? " on" : "")} disabled=${stop.planned} onClick=${() => onQualify(stop, c.id, comment)}>${c.label}</button>`)}
        <select value=${other} disabled=${stop.planned} onChange=${e => { setOther(e.target.value); if (e.target.value) onQualify(stop, +e.target.value, comment); }} aria-label="Autre cause">
          <option value="">Autre…</option>${causes.filter(c => !QUICK.includes(c.code)).map(c => html`<option value=${c.id}>${c.label}</option>`)}</select>
      </div>
      <input placeholder="Commentaire" value=${comment} onInput=${e => setComment(e.target.value)} disabled=${stop.planned} />
    </div>
  </div>`;
}

// ----------------------------------------------------------------------------- synoptique des moteurs
function Synoptic({ motors, big = false }) {
  const [sel, setSel] = useState(null);
  const m = motors.find(x => x.id === sel) || motors[0];
  const trend = useApi(m && m.tag_courant ? `/tendance/${m.tag_courant}?minutes=1440` : null, [m?.id], 120000);
  if (!motors.length) return html`<p class="empty">Aucun moteur rattaché. <a href="#/catalogue">Catalogue →</a></p>`;
  const list = big ? motors : motors.slice(0, 6);
  return html`<div style="display:flex;flex-direction:column;gap:8px;flex:1;min-height:0">
    <div style=${{ display: "grid", gridTemplateColumns: `repeat(${big ? 6 : 3}, minmax(0, 1fr))`, gap: "6px" }}>
      ${list.map(x => { const k = DRIFT_KIND[x.status || "sans_reference"]; return html`<button type="button" onClick=${() => setSel(x.id)}
        style=${{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: "1px", padding: "6px 9px", borderRadius: "10px", minWidth: 0,
          border: x.id === m.id ? "2px solid var(--text)" : "2px solid transparent", background: "var(--fill2)", borderLeft: `5px solid ${KIND_COLOR[k]}` }}>
        <span style="font-size:12px;font-weight:700;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${x.name}</span>
        <span class="num" style=${{ fontSize: "14px", fontWeight: 800, color: KIND_COLOR[k] }}>${x.deviation_pct === null || x.deviation_pct === undefined ? DRIFT_LABEL[x.status || "sans_reference"] : (x.deviation_pct > 0 ? "+" : "") + num(x.deviation_pct) + " %"}</span></button>`; })}
    </div>
    <div style="display:flex;align-items:center;gap:8px"><b style="font-size:13px">${m.name}</b>
      <span class="sub num">${num(m.mean_current)} A</span>
      <${Pill} kind=${DRIFT_KIND[m.status || "sans_reference"]}>${m.deviation_pct === null || m.deviation_pct === undefined ? DRIFT_LABEL[m.status || "sans_reference"] : (m.deviation_pct > 0 ? "+" : "") + num(m.deviation_pct) + " %"}</${Pill}>
      <span class="spacer"></span><${Info}>Courant sur 24 h. Bande verte : ±10 % autour de la référence.<br />${diagnosis(m)}</${Info}>
      <a class="btn iconbtn" href="#/moteurs" title="Moteurs" aria-label="Moteurs"><${Icon} d=${ICON.link} /></a></div>
    <div style="flex:1;min-height:90px;display:flex"><${Current} points=${trend.data?.points || []} refc=${m.ref_current} kind=${DRIFT_KIND[m.status || "sans_reference"]} /></div>
  </div>`;
}

function diagnosis(m) {
  const v = m.deviation_pct;
  if (v === null || v === undefined) return m.status === "arret" ? "Moteur à l'arrêt : pas de calcul de dérive." : "Pas de référence : fixez-la dans Moteurs.";
  if (m.status === "alerte") return `Courant ${v > 0 ? "au-dessus" : "en dessous"} de la référence de ${num(Math.abs(v))} % : inspection à prévoir (usure, frottement, charge).`;
  if (m.status === "attention") return `Écart de ${num(Math.abs(v))} % : à surveiller.`;
  return "Courant dans la bande de référence.";
}

function Current({ points, refc, kind }) {
  const col = { good: "var(--green-fg)", warn: "var(--orange)", bad: "var(--red)", idle: "var(--primary)" }[kind] || "var(--primary)";
  const pts = points.filter(p => p[1] !== null).map(p => [T(p[0]), p[1]]);
  return html`<${Chart} height=${null} unit="A" empty="Pas de mesure sur 24 h." series=${[{ points: pts, color: col }]}
    zones=${refc ? [{ from: refc * 0.9, to: refc * 1.1, color: "var(--green-t)" }] : []}
    bands=${refc ? [{ y: refc, color: "var(--green-fg)", label: "Réf. " + num(refc, 1) + " A" }] : []} />`;
}

// ----------------------------------------------------------------------------- production, recette, alarmes
function ProdDetail({ rows }) {
  let cum = 0, cumT = 0;
  return html`<div style="height:260px"><${Bars} data=${rows.map(r => ({ label: hm(r.h), v: r.t, target: r.objectif, color: r.t !== null && r.objectif && r.t < 0.9 * r.objectif ? "var(--orange)" : "var(--green)" }))} unit="t" labelEvery=${1} /></div>
    <div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Heure</th><th class="num">Produit (t)</th><th class="num">Objectif (t)</th><th class="num">Cumul (t)</th><th class="num">Cumul objectif (t)</th></tr></thead>
    <tbody>${rows.map(r => { cum += r.t || 0; cumT += r.objectif || 0; return html`<tr><td>${hm(r.h)}</td><td class="num">${num(r.t, 1)}</td><td class="num">${num(r.objectif, 1)}</td>
      <td class="num">${r.t === null ? "" : num(cum, 0)}</td><td class="num">${num(cumT, 0)}</td></tr>`; })}</tbody></table></div>`;
}

function RecipeCompare({ shift, big = false }) {
  const rid = shift?.recipe_id;
  const recipe = useApi(rid ? `/recettes/${rid}` : null, [rid]);
  const vid = recipe.data?.versions?.find(v => v.status === "validee")?.id;
  const cmp = useApi(vid ? `/recettes/versions/${vid}/comparer?avec=production` : null, [vid], 60000);
  if (!rid) return html`<p class="empty">Aucune recette planifiée sur ce poste. Choisissez-la dans le <a href="#/planning">planning</a>.</p>`;
  if (cmp.error) return html`<p class="empty"><b>${recipe.data?.label}</b> : ${cmp.error}</p>`;
  if (!cmp.data) return html`<p class="empty">${recipe.data ? (vid ? "Lecture de l'automate…" : "Aucune version validée de cette recette.") : "Chargement…"}</p>`;
  const rows = big ? cmp.data.lignes : cmp.data.lignes.slice().sort((a, b) => b.ecart - a.ecart).slice(0, 5);
  return html`<div style="display:flex;align-items:center;gap:8px"><b>${recipe.data.label}</b><span class="sub">${cmp.data.reference}</span><span class="spacer"></span>
      <${Pill} kind=${cmp.data.ecarts ? "warn" : "good"}>${cmp.data.ecarts ? cmp.data.ecarts + " écart" + (cmp.data.ecarts > 1 ? "s" : "") : "Conforme"}</${Pill}></div>
    <div class="scroll" style="flex:1"><table style="font-size:13px"><thead><tr><th>Paramètre</th><th class="num">Recette</th><th class="num">Réel</th></tr></thead>
      <tbody>${rows.map(r => html`<tr class=${r.ecart ? "diff" : ""}><td>${r.label}</td><td class="num">${num(r.a, 1)} ${r.unit || ""}</td><td class="num">${r.ecart ? html`<b>${num(r.b, 1)}</b>` : num(r.b, 1)} ${r.unit || ""}</td></tr>`)}</tbody></table></div>
    ${!big && html`<a href=${`#/recettes/${rid}`} style="font-size:13px;font-weight:600">Ouvrir la recette →</a>`}`;
}

function AlarmList({ alarms, onAck, big = false }) {
  if (!alarms.length) return html`<p class="empty">Aucune alarme active sur la ligne.</p>`;
  return html`<div class="lst scroll" style="flex:1">${(big ? alarms : alarms.slice(0, 5)).map(a => html`<div>
    <${Pill} kind=${a.priority === "critique" || a.priority === "haute" ? "bad" : a.priority === "moyenne" ? "warn" : "idle"}>${a.priority}</${Pill}>
    <span class="t" title=${a.message}>${a.machine} · ${a.message}</span><span class="spacer"></span><span class="sub num">${big ? dt(a.raised_at) : hm(a.raised_at)}</span>
    ${!a.ack_at && html`<button type="button" style="padding:3px 10px;font-size:12px" onClick=${() => onAck(a.id)}>Acquitter</button>`}</div>`)}</div>`;
}
