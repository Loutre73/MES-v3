// Synthèse usine : bandeau d'indicateurs puis tuiles agrandissables ; la vue tient dans la hauteur de l'écran.
import { html, useState } from "./vendor/preact-htm.module.js";
import {
  api, useApi, Msg, Band, Tile, Pill, Frise, Bars, Ring, Info, Spark, Icon, ICON, Dialog, useToast, lineState, oeeKind,
  hm, num, pct, dur, dhms, dt, nowLabel, fmtDayShort, tr, N_, fmt
} from "./lib.js";
import { Pareto, StackBars, Donut, topN } from "./charts.js";

const PERIODS = [["poste", N_("Poste"), 1], ["jour", N_("Jour"), 1], ["semaine", N_("Semaine"), 7], ["mois", N_("Mois"), 31]];
const KIND_COLOR = { good: "var(--green)", warn: "var(--orange)", bad: "var(--red)", idle: "var(--muted)" };
const DRIFT_COLOR = { normal: "var(--green)", attention: "var(--orange)", alerte: "var(--red)", arret: "var(--muted)", sans_reference: "var(--line)" };
const T = v => new Date(v).getTime();

const fmtDM = fmt({ day: "2-digit", month: "2-digit" });
const dayInput = d => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
// journée de production : de 6 h à 6 h le lendemain
const prodDay = v => { const d = new Date(v + "T06:00"); return d; };

function periodStart(p) {
  const d = new Date();
  if (p === "jour") { d.setHours(6, 0, 0, 0); if (Date.now() < d.getTime()) d.setDate(d.getDate() - 1); }
  if (p === "semaine") { d.setHours(6, 0, 0, 0); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); }
  if (p === "mois") { d.setDate(1); d.setHours(6, 0, 0, 0); }
  return d;
}

// agrégat de postes : sommes et moyennes pondérées par le temps d'ouverture
export function aggregate(shifts) {
  let t = 0, target = 0, kwh = 0, stops = 0, todo = 0, w = 0, oee = 0, av = 0, pf = 0, wp = 0, down = 0;
  for (const s of shifts) {
    if (s.tonnage_t === null || s.tonnage_t === undefined) continue;
    t += s.tonnage_t || 0; target += s.target_t || 0; kwh += s.energy_kwh || 0;
    stops += s.stop_count || 0; todo += s.unqualified_count || 0; down += s.unplanned_stop_s || 0;
    const open = (s.run_s || 0) + (s.unplanned_stop_s || 0);
    if (s.availability !== null && s.availability !== undefined) { w += open; av += s.availability * open; }
    if (s.oee !== null && s.oee !== undefined) { wp += open; oee += s.oee * open; pf += (s.performance || 0) * open; }
  }
  return {
    tonnage_t: t, target_t: target, energy_kwh: kwh, kwh_per_t: t ? kwh / t : null, stop_count: stops, unqualified_count: todo,
    unplanned_stop_s: down, availability: w ? av / w : null, performance: wp ? pf / wp : null, oee: wp ? oee / wp : null,
  };
}

export function Synthese({ user }) {
  const [period, setPeriod] = useState("poste");
  const [range, setRange] = useState(null);   // période précise : { du: "AAAA-MM-JJ", au: "AAAA-MM-JJ" }
  const [pick, setPick] = useState(null);
  const [open, setOpen] = useState(null);
  const [toast, showToast] = useToast();
  const board = useApi("/tableau-de-bord", [], 10000);
  const custom = period === "dates" && range;
  const p = custom ? ["dates", N_("Période"), 1] : PERIODS.find(x => x[0] === period);
  const from = custom ? prodDay(range.du) : periodStart(period);
  const to = custom ? new Date(prodDay(range.au).getTime() + 86400e3) : new Date();
  const days = Math.max(1, Math.min(62, Math.ceil((to - from) / 86400e3)));
  const win = `depuis=${encodeURIComponent(from.toISOString())}${custom ? "&jusqua=" + encodeURIComponent(to.toISOString()) : ""}`;
  const shifts = useApi(period === "poste" ? null : `/shifts?depuis=${encodeURIComponent(from.toISOString())}&jours=${days}`, [period], 60000);
  const week = useApi(`/shifts?depuis=${encodeURIComponent(new Date(Date.now() - 7 * 86400e3).toISOString())}&jours=8`, [], 300000);
  const hourly = useApi("/usine/horaire?heures=24", [], 60000);
  const rate = useApi("/usine/debit?heures=8", [], 60000);
  const todo = useApi("/arrets?a_qualifier=true&jours=2", [], 15000);
  const stops = useApi("/arrets?jours=1", [], 15000);
  const causes = useApi("/arrets/causes");
  const pareto = useApi(period === "poste" ? `/arrets/pareto?jours=1` : `/arrets/pareto?${win}`, [period], 60000);
  const alarms = useApi("/alarmes?actives=true&jours=31", [], 10000);
  const motors = useApi("/moteurs", [], 60000);
  const shiftStart = (board.data?.lignes || []).find(l => l.shift_id)?.starts_at;
  const eWin = period === "poste" ? (shiftStart ? `depuis=${encodeURIComponent(shiftStart)}` : "jours=1") : win;
  const energy = useApi(`/energie/analyse?${eWin}`, [period, shiftStart, range], 60000);

  const b = board.data;
  if (board.error && !b) return html`<${Msg} error=${board.error} />`;
  if (!b) return html`<p class="muted">${tr("Chargement…")}</p>`;
  const lines = b.lignes;
  const cur = aggregate(lines);
  const k = period === "poste" ? cur : aggregate((shifts.data || []).filter(x => T(x.starts_at) < to.getTime()));
  const lag = b.retard_collecte_s;
  const shiftLine = lines.find(l => l.shift_id);
  const reload = () => { board.reload(); todo.reload(); stops.reload(); pareto.reload(); };

  const qualify = async (s, cause) => {
    try {
      await api(`/arrets/${s.id}/qualifier`, { method: "POST", body: { cause_id: cause.id, comment: null } });
      showToast(tr("Arrêt {m} de {h} qualifié : {c}.", { m: s.machine, h: hm(s.starts_at), c: cause.label }));
      reload();
    } catch (e) { showToast(e.message, true); }
  };
  const ackAlarm = async id => { try { await api(`/alarmes/${id}/acquitter`, { method: "POST" }); alarms.reload(); board.reload(); } catch (e) { showToast(e.message, true); } };

  const kpis = [
    { l: tr("TRS"), v: pct(k.oee), d: `${tr("Dispo")} ${pct(k.availability)} · ${tr("Perf")} ${pct(k.performance)}`, c: KIND_COLOR[oeeKind(k.oee)], href: null },
    { l: tr("Production"), v: num(k.tonnage_t, 0) + " t", d: k.target_t ? `${pct(k.tonnage_t / k.target_t)} · ${tr("obj.")} ${num(k.target_t, 0)} t` : "–" },
    { l: tr("Débit"), v: num(lines.reduce((a, l) => a + (l.debit || 0), 0), 0) + " t/h", d: tr("{a} / {b} en marche", { a: lines.filter(l => lineState(l).kind === "good").length, b: lines.length }) },
    { l: tr("Énergie"), v: num(k.kwh_per_t, 1) + " kWh/t", d: num(k.energy_kwh, 0) + " kWh" },
    { l: tr("Arrêts"), v: String(k.stop_count ?? 0), d: `${dhms(k.unplanned_stop_s)} · ` + tr("{n} à qualif.", { n: b.arrets_a_qualifier }), c: b.arrets_a_qualifier ? "var(--orange)" : null, href: "#/arrets" },
    { l: tr("Alarmes"), v: String(b.alarmes_actives), d: tr("{n} non acq.", { n: b.alarmes_non_acquittees }), c: b.alarmes_actives ? "var(--red)" : null, href: "#/alarmes" },
  ];
  const quick = (causes.data || []).slice().sort((x, y) => (x.category === "panne" ? -1 : 0) - (y.category === "panne" ? -1 : 0));
  const tiles = { open, onToggle: setOpen };

  return html`<div class="view">
    <${Band} over=${nowLabel()} title=${tr("Synthèse")} accent=${tr("usine")}>
      <div class="seg" role="group" aria-label=${tr("Période")}>
        ${PERIODS.map(([id, label]) => html`<button type="button" class=${period === id ? "on" : ""} aria-pressed=${period === id} onClick=${() => setPeriod(id)}>${tr(label)}</button>`)}
        <button type="button" class=${custom ? "on" : ""} aria-pressed=${!!custom} aria-label=${tr("Période précise")} title=${tr("Période précise")}
          onClick=${() => setPick(range || { du: dayInput(Date.now() - 6 * 86400e3), au: dayInput(Date.now()) })}>
          <${Icon} d=${ICON.calendar} size="14" />${custom ? `${fmtDM.format(prodDay(range.du))} → ${fmtDM.format(prodDay(range.au))}` : ""}</button>
      </div>
      <span class=${"live" + (lag === null ? " off" : lag > 60 ? " late" : "")}>${lag === null ? tr("Collecte arrêtée") : lag > 60 ? `${tr("Retard")} ${dur(lag)}` : tr("En direct")}</span>
    </${Band}>

    <section class="kstrip" aria-label=${tr("Indicateurs clés")}>
      ${kpis.map(x => html`<${x.href ? "a" : "div"} class="kcell" href=${x.href}>
        <span class="l">${x.l}</span><span class="v" style=${x.c ? { color: x.c } : {}}>${x.v}</span><span class="d">${x.d}</span></${x.href ? "a" : "div"}>`)}
    </section>

    <section class="tiles" style="grid-template-columns:repeat(12,minmax(0,1fr));grid-template-rows:minmax(0,1fr) minmax(0,.82fr) minmax(0,1.1fr)">
      <${Tile} id="prod" title=${period === "poste" ? tr("Production du poste") : tr("Production") + " · " + tr(p[1]).toLowerCase()} col="span 3" ...${tiles}
        extra=${html`<${Info}>${tr("Barres : tonnes par heure, 12 dernières heures.")}</${Info}>`}
        detail=${html`<${ProductionDetail} lines=${lines} hourly=${hourly.data} shiftLine=${shiftLine} />`}>
        <div style="display:flex;align-items:baseline;gap:8px"><span class="big">${num(k.tonnage_t, 0)} t</span><span class="sub">${tr("sur")} ${num(k.target_t, 0)} t</span></div>
        <div class="bar"><div style=${{ width: Math.min(100, k.target_t ? (k.tonnage_t / k.target_t) * 100 : 0) + "%", background: "var(--green)" }}></div></div>
        <div style="flex:1;min-height:0"><${Bars} data=${hourlyProd(hourly.data).slice(-12)} unit="t" labelEvery=${3} slots=${12} /></div>
      </${Tile}>

      <${Tile} id="trs" title="TRS" col="span 3" ...${tiles} detail=${html`<${TrsDetail} lines=${lines} week=${week.data || []} />`}>
        <div style="display:flex;align-items:center;gap:14px;flex:1;min-height:0">
          <${Ring} value=${k.oee} label="TRS" />
          <div style="display:grid;gap:6px;flex:1;min-width:0">
            ${[[N_("Disponibilité"), k.availability], [N_("Performance"), k.performance], [N_("Qualité"), k.oee !== null ? 1 : null]].map(([l, v]) => html`<div class="hbar">
              <span>${tr(l)}</span><b class="num">${pct(v)}</b><div class="track"><div style=${{ width: (v || 0) * 100 + "%", background: KIND_COLOR[oeeKind(v)] }}></div></div></div>`)}
          </div>
        </div>
      </${Tile}>

      <${Tile} id="todo" title=${tr("À traiter")} col="span 3" ...${tiles}
        extra=${b.arrets_a_qualifier ? html`<${Pill} kind="warn">${b.arrets_a_qualifier}</${Pill}>` : ""}
        detail=${html`<${TodoDetail} stops=${todo.data || []} causes=${causes.data || []} onQualify=${qualify} />`}>
        <div class="lst scroll" style="flex:1">
          ${(todo.data || []).slice(0, 3).map(s => html`<div style="flex-direction:column;align-items:stretch;gap:6px">
            <div style="display:flex;gap:8px;align-items:center"><span class="t">${s.machine} · ${hm(s.starts_at)}</span><span class="spacer"></span><span class="sub num">${dur(s.duree_s)}</span></div>
            <div class="chips">${quick.slice(0, 3).map(c => html`<button type="button" class="chip" style="padding:4px 10px;font-size:12px" onClick=${() => qualify(s, c)}>${c.label}</button>`)}
              <a class="chip" style="padding:4px 10px;font-size:12px;text-decoration:none" href=${`#/ligne/${s.machine_id}`}>${tr("Autre…")}</a></div>
          </div>`)}
          ${todo.data && !todo.data.length && html`<p class="empty">${tr("Tous les arrêts sont qualifiés.")}</p>`}
        </div>
      </${Tile}>

      <${Tile} id="alarmes" title=${tr("Alarmes")} col="span 3" ...${tiles}
        extra=${b.alarmes_actives ? html`<${Pill} kind="bad">${b.alarmes_actives}</${Pill}>` : ""}
        detail=${html`<${AlarmDetail} alarms=${alarms.data || []} onAck=${ackAlarm} onAckAll=${async () => { await api("/alarmes/acquitter-tout", { method: "POST" }); alarms.reload(); board.reload(); }} />`}>
        <div class="lst scroll" style="flex:1">
          ${(alarms.data || []).slice(0, 4).map(a => html`<div>
            <${Pill} kind=${a.priority === "critique" || a.priority === "haute" ? "bad" : a.priority === "moyenne" ? "warn" : "idle"}>${a.priority}</${Pill}>
            <span class="t" title=${a.message}>${a.machine && !a.message.startsWith(a.machine) ? a.machine + " · " : ""}${a.message}</span><span class="spacer"></span><span class="sub num">${hm(a.raised_at)}</span></div>`)}
          ${alarms.data && !alarms.data.length && html`<p class="empty">${tr("Aucune alarme active.")}</p>`}
        </div>
      </${Tile}>

      <article class="tile" style="grid-column:span 12">
        <div class="th"><h2>${tr("Lignes")}</h2></div>
        <div style=${{ display: "grid", gridTemplateColumns: `repeat(${Math.max(1, lines.length)}, minmax(0, 1fr))`, gap: "10px", flex: 1, minHeight: 0 }}>
          ${lines.map(l => html`<${LineCard} l=${l} stops=${(stops.data || []).filter(s => s.machine_id === l.id)}
            rate=${(rate.data || []).filter(r => r.line_id === l.id).map(r => [T(r.t), r.v])} />`)}
          ${!lines.length && html`<p class="empty">${tr("Aucune ligne.")} <a href="#/catalogue">${tr("Catalogue →")}</a></p>`}
        </div>
      </article>

      <${Tile} id="arrets" title=${tr("Arrêts · causes")} col="span 3" ...${tiles} detail=${html`<${ParetoDetail} pareto=${pareto.data || []} />`}>
        <div class="scroll" style="display:grid;gap:7px;align-content:start;flex:1">${paretoBars(pareto.data || [], 4)}</div>
      </${Tile}>

      <${Tile} id="moteurs" title=${tr("Moteurs · dérive")} col="span 3" ...${tiles} detail=${html`<${MotorDetail} motors=${motors.data || []} />`}
        extra=${b.moteurs_en_alerte ? html`<${Pill} kind="bad">${b.moteurs_en_alerte} ${tr("en alerte")}</${Pill}>` : ""}>
        <${MotorDots} motors=${motors.data || []} />
      </${Tile}>

      <${Tile} id="energie" title=${tr("Énergie")} col="span 3" ...${tiles}
        detail=${html`<${EnergyDetail} lines=${lines} energy=${energy.data} />`}>
        <div style="display:flex;align-items:baseline;gap:8px"><span class="big" style="font-size:26px">${num(k.kwh_per_t, 1)}</span><span class="sub">kWh/t · ${num(k.energy_kwh, 0)} kWh</span></div>
        <${EnergySplit} t=${energy.data?.total} />
        <div style="flex:1;min-height:0"><${Bars} data=${hourlyEnergy(hourly.data).slice(-12)} unit="kWh" color="var(--sky)" slots=${12} labelEvery=${3} /></div>
      </${Tile}>

      <${Tile} id="poste" title=${tr("Poste & recettes")} col="span 3" ...${tiles} detail=${html`<${ShiftDetail} lines=${lines} />`}>
        ${shiftLine ? html`<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap"><b style="font-size:18px">${shiftLine.shift_label || tr("Poste en cours")}</b>
          <span class="sub">${hm(shiftLine.starts_at)}–${hm(shiftLine.ends_at)} · ${tr("reste")} ${dur((T(shiftLine.ends_at) - Date.now()) / 1000)}</span></div>` : html`<p class="empty">${tr("Aucun poste en cours.")}</p>`}
        <div class="lst scroll" style="flex:1">
          ${lines.map(l => html`<div><span class="t">${l.name}</span><span class="sub">${l.team ? tr("Éq.") + " " + l.team : "–"}</span><span class="spacer"></span><span class="sub">${l.recipe_label || ""}</span></div>`)}
        </div>
        <a href="#/planning" style="font-size:13px;font-weight:600">${tr("Planning →")}</a>
      </${Tile}>
    </section>
    ${pick && html`<${Dialog} title=${tr("Période")} onClose=${() => setPick(null)}>
      <div style="display:grid;gap:12px">
        <div class="row">
          <label class="f" style="flex:1">${tr("Du")}<input type="date" value=${pick.du} max=${pick.au} onInput=${e => setPick({ ...pick, du: e.target.value })} /></label>
          <label class="f" style="flex:1">${tr("Au")}<input type="date" value=${pick.au} min=${pick.du} max=${dayInput(Date.now())} onInput=${e => setPick({ ...pick, au: e.target.value })} /></label>
        </div>
        <div class="row"><span class="spacer"></span>
          ${custom && html`<button onClick=${() => { setRange(null); setPeriod("jour"); setPick(null); }}>${tr("Effacer")}</button>`}
          <button class="primary" disabled=${!pick.du || !pick.au || pick.au < pick.du || (prodDay(pick.au) - prodDay(pick.du)) / 86400e3 > 61}
            onClick=${() => { setRange(pick); setPeriod("dates"); setPick(null); }}>${tr("Appliquer")}</button></div>
      </div></${Dialog}>`}
    ${toast}
  </div>`;
}

function LineCard({ l, stops, rate }) {
  const st = lineState(l);
  const prog = l.target_t ? Math.min(1, (l.tonnage_t || 0) / l.target_t) : null;
  return html`<a href=${`#/ligne/${l.id}`} class="lcard" style="text-decoration:none;color:inherit;background:var(--fill2);border-radius:12px;padding:10px 12px;display:flex;flex-direction:column;gap:7px;min-width:0;min-height:0;overflow:hidden">
    <div style="display:flex;align-items:center;gap:8px"><b style="font-size:16px">${l.name}</b><span class="spacer"></span><${Pill} kind=${st.kind}>${st.label}</${Pill}></div>
    <div style="display:flex;align-items:baseline;gap:6px"><span class="num" style=${{ fontSize: "22px", fontWeight: 800, whiteSpace: "nowrap", color: KIND_COLOR[oeeKind(l.oee)] }}>${pct(l.oee)}</span><span class="sub">TRS</span>
      <span class="spacer"></span><span class="sub num" style="white-space:nowrap">${num(l.tonnage_t, 0)}${prog !== null ? ` / ${num(l.target_t, 0)}` : ""} t</span>
      ${l.unqualified_count ? html`<span title=${tr("Arrêts à qualifier")}><${Pill} kind="warn">${l.unqualified_count}</${Pill}></span>` : ""}</div>
    ${l.starts_at && html`<${Frise} start=${l.starts_at} end=${l.ends_at} small stops=${stops.filter(s => T(s.ends_at || Date.now()) > T(l.starts_at) && T(s.starts_at) < T(l.ends_at))} />`}
    <div style="display:flex;align-items:flex-end;gap:8px;flex:none" title=${tr("Débit sur 8 h (t/h) ; tirets : cadence nominale")}>
      <div style="flex:1;min-width:0"><${Spark} points=${rate} nominal=${l.cadence_nominale_th} color=${KIND_COLOR[st.kind === "idle" ? "good" : st.kind]} height=${24} x0=${Date.now() - 8 * 3600e3} x1=${Date.now()} /></div>
      <b class="num" style="font-size:14px;white-space:nowrap">${num(l.debit, 0)} t/h</b></div>
  </a>`;
}

function hourlyProd(h) {
  const by = {};
  (h?.production || []).forEach(r => { by[r.h] = (by[r.h] || 0) + (r.t || 0); });
  return Object.keys(by).sort().map(k => ({ label: hm(k).slice(0, 2) + "h", v: by[k], t: T(k) }));
}
function hourlyEnergy(h) {
  return (h?.energie || []).map(r => ({ label: hm(r.h).slice(0, 2) + "h", v: r.kwh, t: T(r.h) }));
}

function paretoBars(rows, n) {
  const max = Math.max(1, ...rows.map(r => r.minutes));
  if (!rows.length) return html`<p class="empty">${tr("Aucun arrêt sur la période.")}</p>`;
  return rows.slice(0, n).map(r => html`<div class="hbar"><span>${r.cause}</span><span class="sub num"><b style="color:var(--text)">${dhms(r.minutes * 60)}</b> · ${r.nombre}</span>
    <div class="track"><div class=${r.category === "non_qualifie" ? "warn" : ""} style=${{ width: (r.minutes / max) * 100 + "%" }}></div></div></div>`);
}

function MotorDots({ motors }) {
  const counts = {};
  motors.forEach(m => { const s = m.status || "sans_reference"; counts[s] = (counts[s] || 0) + 1; });
  const sorted = motors.slice().sort((a, b) => Math.abs(b.deviation_pct || 0) - Math.abs(a.deviation_pct || 0)).slice(0, 100);
  return html`<a href="#/moteurs" style="flex:1;min-height:0;display:grid;grid-template-columns:repeat(20,1fr);gap:3px;align-content:start;text-decoration:none" aria-label=${tr("Carte des moteurs")}>
      ${sorted.map(m => html`<span title=${`${m.name} : ${m.deviation_pct === null || m.deviation_pct === undefined ? "–" : num(m.deviation_pct) + " %"}`}
        style=${{ aspectRatio: "1", borderRadius: "50%", background: DRIFT_COLOR[m.status || "sans_reference"] }}></span>`)}
    </a>
    <div class="legend" style="font-size:12px;gap:10px">
      <span><i style="background:var(--green)"></i>${counts.normal || 0} ${tr("normaux")}</span>
      <span><i style="background:var(--orange)"></i>${counts.attention || 0}</span>
      <span><i style="background:var(--red)"></i>${counts.alerte || 0}</span>
      <span><i style="background:var(--muted)"></i>${(counts.arret || 0) + (counts.sans_reference || 0)}</span>
    </div>`;
}

// ----------------------------------------------------------------------------- tuiles agrandies
function ProductionDetail({ lines, hourly, shiftLine }) {
  const rows = hourlyProd(hourly);
  let cum = 0;
  const start = shiftLine ? T(shiftLine.starts_at) : Date.now() - 8 * 3600e3;
  const end = shiftLine ? T(shiftLine.ends_at) : Date.now();
  const target = lines.reduce((a, l) => a + (l.target_t || 0), 0);
  const pts = rows.filter(r => r.t >= start - 1).map(r => { cum += r.v; return [r.t + 3600e3, cum]; });
  const W = 800, H = 240, P = 36, max = Math.max(1, target, cum) * 1.08;
  const X = t => P + ((t - start) / Math.max(1, end - start)) * (W - 2 * P), Y = v => H - P + 10 - (v / max) * (H - P - 10);
  return html`<div style="display:grid;grid-template-columns:2fr 1fr;gap:20px;min-height:0">
    <div><h3 style="margin:0 0 10px;font-size:14px">${tr("Cumul du poste")}</h3>
      <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label=${tr("Production cumulée")}>
        <line x1=${X(start)} x2=${X(end)} y1=${Y(0)} y2=${Y(target)} style=${{ stroke: "var(--text2)" }} stroke-dasharray="6 5" stroke-width="2" />
        <polyline fill="none" style=${{ stroke: "var(--green)" }} stroke-width="3" points=${[[start, 0], ...pts].map(p => `${X(p[0])},${Y(p[1])}`).join(" ")} />
        ${[0, 0.5, 1].map(f => html`<text x=${X(start + f * (end - start))} y=${H - 6} text-anchor="middle" font-size="12">${hm(start + f * (end - start))}</text>`)}
        <text x=${X(end) - 4} y=${Y(target) - 6} text-anchor="end" font-size="12">${tr("Objectif")} ${num(target, 0)} t</text>
      </svg></div>
    <div class="lst">${lines.map(l => html`<a href=${`#/ligne/${l.id}`} style="text-decoration:none;color:inherit;flex-direction:column;align-items:stretch">
      <div style="display:flex"><span class="t">${l.name}</span><span class="spacer"></span><b class="num">${num(l.tonnage_t, 0)} / ${num(l.target_t, 0)} t</b></div>
      <div class="bar"><div style=${{ width: Math.min(100, l.target_t ? (l.tonnage_t / l.target_t) * 100 : 0) + "%", background: "var(--green)" }}></div></div></a>`)}</div>
  </div>`;
}

function TrsDetail({ lines, week }) {
  const days = {};
  week.forEach(s => {
    const d = new Date(s.starts_at); if (d.getHours() < 6) d.setDate(d.getDate() - 1); d.setHours(0, 0, 0, 0);
    (days[d.getTime()] = days[d.getTime()] || []).push(s);
  });
  const bars = Object.keys(days).sort().map(k => { const a = aggregate(days[k]); return { label: fmtDayShort.format(new Date(+k)), v: a.oee === null ? 0 : a.oee * 100, color: KIND_COLOR[oeeKind(a.oee)] }; });
  return html`<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
    <div><h3 style="margin:0 0 10px;font-size:14px">${tr("Par ligne · poste")}</h3>
      <div class="table-wrap"><table><thead><tr><th>${tr("Ligne")}</th><th class="num">TRS</th><th class="num">${tr("Dispo")}</th><th class="num">${tr("Perf")}</th><th class="num">${tr("Arrêts")}</th></tr></thead>
      <tbody>${lines.map(l => html`<tr><td><a href=${`#/ligne/${l.id}`}>${l.name}</a></td><td class="num"><b style=${{ color: KIND_COLOR[oeeKind(l.oee)] }}>${pct(l.oee)}</b></td>
        <td class="num">${pct(l.availability)}</td><td class="num">${pct(l.performance)}</td><td class="num">${l.stop_count ?? "–"} · ${dur(l.unplanned_stop_s)}</td></tr>`)}</tbody></table></div></div>
    <div><h3 style="margin:0 0 10px;font-size:14px">${tr("Usine · 7 jours")} <span class="sub">${tr("(obj. 75 %)")}</span></h3><div style="height:220px"><${Bars} data=${bars} unit="%" labelEvery=${1} target=${75} /></div></div>
  </div>`;
}

function TodoDetail({ stops, causes, onQualify }) {
  const [sel, setSel] = useState({});
  if (!stops.length) return html`<p class="empty">${tr("Tous les arrêts sont qualifiés.")}</p>`;
  return html`<div class="table-wrap"><table><thead><tr><th>${tr("Ligne")}</th><th>${tr("Début")}</th><th class="num">${tr("Durée")}</th><th>${tr("Code")}</th><th>${tr("Cause")}</th><th></th></tr></thead>
    <tbody>${stops.map(s => html`<tr><td>${s.machine}</td><td>${dt(s.starts_at)}</td><td class="num">${dur(s.duree_s)}</td><td>${s.plc_code ?? ""}</td>
      <td><select value=${sel[s.id] || ""} onChange=${e => setSel({ ...sel, [s.id]: e.target.value })}><option value="">${tr("Choisir…")}</option>
        ${causes.map(c => html`<option value=${c.id}>${c.label}</option>`)}</select></td>
      <td class="row" style="flex-wrap:nowrap"><button class="primary" disabled=${!sel[s.id]} onClick=${() => onQualify(s, causes.find(c => c.id === +sel[s.id]))}>${tr("Qualifier")}</button>
        <a class="btn iconbtn" title=${tr("Sur la frise")} aria-label=${tr("Sur la frise")} href=${`#/ligne/${s.machine_id}`}><${Icon} d=${ICON.link} /></a></td></tr>`)}</tbody></table></div>`;
}

function AlarmDetail({ alarms, onAck, onAckAll }) {
  return html`<div class="row" style="margin-bottom:8px"><${Pill} kind=${alarms.length ? "bad" : "good"}>${alarms.length}</${Pill}><span class="spacer"></span>
      <button onClick=${onAckAll}>${tr("Tout acquitter")}</button><a class="btn" href="#/alarmes">${tr("Historique")}</a></div>
    <div class="table-wrap"><table><thead><tr><th>${tr("Priorité")}</th><th>${tr("Machine")}</th><th>${tr("Message")}</th><th>${tr("Apparition")}</th><th>${tr("État")}</th><th></th></tr></thead>
    <tbody>${alarms.map(a => html`<tr><td><${Pill} kind=${a.priority === "critique" || a.priority === "haute" ? "bad" : a.priority === "moyenne" ? "warn" : "idle"}>${a.priority}</${Pill}></td>
      <td>${a.machine}</td><td>${a.message}</td><td>${dt(a.raised_at)}</td><td>${a.cleared_at ? "disparue" : html`<b>${tr("active")}</b>`}</td>
      <td>${!a.ack_at && html`<button onClick=${() => onAck(a.id)}>${tr("Acquitter")}</button>`}</td></tr>`)}</tbody></table></div>`;
}

function ParetoDetail({ pareto }) {
  return html`<div class="row" style="margin-bottom:8px"><span class="sub">${tr("non planifiés · jj:hh:mm:ss")}</span><span class="spacer"></span><a class="btn" href="#/arrets">${tr("Arrêts →")}</a></div>
    <${Pareto} height=${340} fmt=${dhms} items=${pareto.map(r => ({ label: r.cause, v: r.minutes * 60, sub: tr("{n} arrêts", { n: r.nombre }), color: r.category === "non_qualifie" ? "var(--orange)" : null }))} />`;
}

function MotorDetail({ motors }) {
  const sorted = motors.slice().sort((a, b) => Math.abs(b.deviation_pct || 0) - Math.abs(a.deviation_pct || 0));
  return html`<div class="row" style="margin-bottom:8px"><span class="spacer"></span><a class="btn" href="#/moteurs">${tr("Moteurs →")}</a></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px">
    ${sorted.map(m => html`<a href="#/moteurs" style=${{ textDecoration: "none", color: "inherit", background: "var(--fill2)", borderRadius: "10px", padding: "8px 10px", borderLeft: `4px solid ${DRIFT_COLOR[m.status || "sans_reference"]}` }}>
      <div style="font-weight:700;font-size:13px">${m.name}</div>
      <div class="num" style="font-size:16px;font-weight:800">${m.deviation_pct === null || m.deviation_pct === undefined ? "–" : (m.deviation_pct > 0 ? "+" : "") + num(m.deviation_pct) + " %"}</div>
      <div class="sub num" style="font-size:12px">${num(m.mean_current)} A</div></a>`)}</div>`;
}

// part de l'énergie consommée en production et hors production
function EnergySplit({ t, wide = false }) {
  if (!t || !t.kwh) return html`<div class="split"></div>`;
  const p = t.prod_kwh / t.kwh;
  return html`<div style=${{ display: "flex", flexDirection: "column", gap: "4px", ...(wide ? { flex: 1, maxWidth: "460px" } : {}) }}>
    <div class="split" role="img" aria-label=${tr("En production {a} %, hors production {b} %", { a: Math.round(p * 100), b: Math.round((1 - p) * 100) })}>
      <div style=${{ width: p * 100 + "%", background: "var(--green)" }}></div><div style=${{ flex: 1, background: "var(--orange)" }}></div></div>
    <div class="legend" style="font-size:12px;gap:10px"><span><i style="background:var(--green)"></i>${tr("Prod.")} ${Math.round(p * 100)} %</span>
      <span><i style="background:var(--orange)"></i>${tr("Hors prod.")} ${num(t.hors_kwh, 0)} kWh</span></div></div>`;
}

function EnergyDetail({ lines, energy }) {
  if (!energy) return html`<p class="empty">${tr("Chargement…")}</p>`;
  const serie = energy.serie.map(r => ({ label: energy.pas === "jour" ? fmtDayShort.format(new Date(r.t)) : hm(r.t).slice(0, 2) + "h", parts: [r.prod_kwh, r.hors_kwh] }));
  const by = {};
  energy.compteurs.forEach(c => { by[c.line || tr("Commun")] = (by[c.line || tr("Commun")] || 0) + c.kwh; });
  return html`<div class="row" style="margin-bottom:8px"><${EnergySplit} t=${energy.total} wide /><span class="spacer"></span><a class="btn" href="#/energie">${tr("Énergie →")}</a></div>
    <div style="display:grid;grid-template-columns:3fr 2fr;gap:24px;flex:1;min-height:300px">
      <div style="display:flex;flex-direction:column;min-height:0"><${StackBars} data=${serie} names=${[tr("En production"), tr("Hors production")]} colors=${["var(--green)", "var(--orange)"]} unit="kWh"
        labelEvery=${serie.length > 16 ? Math.ceil(serie.length / 12) : 1} /></div>
      <${Donut} items=${topN(Object.entries(by).map(([label, v]) => ({ label, v })), 6)} unit="kWh" center=${num(energy.total.kwh / 1000, 1)} centerSub="MWh" size=${160} />
    </div>`;
}

function ShiftDetail({ lines }) {
  const d = new Date(); d.setHours(6, 0, 0, 0); if (Date.now() < d.getTime()) d.setDate(d.getDate() - 1);
  const day = useApi(`/shifts?depuis=${encodeURIComponent(d.toISOString())}&jours=1`);
  const tmpl = [...new Set((day.data || []).map(s => s.starts_at))].sort();
  return html`<div class="row" style="margin-bottom:10px"><span class="spacer"></span><a class="btn primary" href="#/planning">${tr("Planning")}</a></div>
    <div class="table-wrap"><table><thead><tr><th>${tr("Ligne")}</th>${tmpl.map(t => html`<th>${(day.data || []).find(s => s.starts_at === t)?.label} · ${hm(t)}</th>`)}</tr></thead>
    <tbody>${lines.map(l => html`<tr><td><b>${l.name}</b></td>${tmpl.map(t => {
      const s = (day.data || []).find(x => x.line_id === l.id && x.starts_at === t);
      if (!s) return html`<td></td>`;
      const label = s.kind === "maintenance" ? tr("Maintenance") : s.kind === "arret" ? tr("Arrêt planifié") : s.recipe_label || tr("Production");
      return html`<td>${s.team ? html`<b>${s.team}</b> · ` : ""}${label}<div class="sub num">${s.tonnage_t !== null && s.tonnage_t !== undefined ? `${num(s.tonnage_t, 0)} / ` : ""}${num(s.target_t, 0)} t</div></td>`;
    })}</tr>`)}</tbody></table></div>`;
}
