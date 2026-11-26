// Synthèse usine : bandeau d'indicateurs puis tuiles agrandissables ; la vue tient dans la hauteur de l'écran.
import { html, useState } from "./vendor/preact-htm.module.js";
import {
  api, useApi, Msg, Band, Tile, Pill, Frise, Bars, Ring, useToast, lineState, oeeKind,
  hm, num, pct, dur, dt, nowLabel, fmtDayShort,
} from "./lib.js";

const PERIODS = [["poste", "Poste", 1], ["jour", "Jour", 1], ["semaine", "Semaine", 7], ["mois", "Mois", 31]];
const KIND_COLOR = { good: "var(--green)", warn: "var(--orange)", bad: "var(--red)", idle: "var(--muted)" };
const DRIFT_COLOR = { normal: "var(--green)", attention: "var(--orange)", alerte: "var(--red)", arret: "var(--muted)", sans_reference: "var(--line)" };
const T = v => new Date(v).getTime();

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
  const [open, setOpen] = useState(null);
  const [toast, showToast] = useToast();
  const board = useApi("/tableau-de-bord", [], 10000);
  const p = PERIODS.find(x => x[0] === period);
  const from = periodStart(period);
  const shifts = useApi(period === "poste" ? null : `/shifts?depuis=${encodeURIComponent(from.toISOString())}&jours=${p[2]}`, [period], 60000);
  const week = useApi(`/shifts?depuis=${encodeURIComponent(new Date(Date.now() - 7 * 86400e3).toISOString())}&jours=8`, [], 300000);
  const hourly = useApi("/usine/horaire?heures=24", [], 60000);
  const todo = useApi("/arrets?a_qualifier=true&jours=2", [], 15000);
  const stops = useApi("/arrets?jours=1", [], 15000);
  const causes = useApi("/arrets/causes");
  const pareto = useApi(`/arrets/pareto?jours=${p[2]}`, [period], 60000);
  const alarms = useApi("/alarmes?actives=true&jours=31", [], 10000);
  const motors = useApi("/moteurs", [], 60000);
  const energy = useApi(open === "energie" ? `/energie?jours=${p[2]}` : null, [open, period]);

  const b = board.data;
  if (board.error && !b) return html`<${Msg} error=${board.error} />`;
  if (!b) return html`<p class="muted">Chargement…</p>`;
  const lines = b.lignes;
  const cur = aggregate(lines);
  const k = period === "poste" ? cur : aggregate(shifts.data || []);
  const lag = b.retard_collecte_s;
  const shiftLine = lines.find(l => l.shift_id);
  const reload = () => { board.reload(); todo.reload(); stops.reload(); pareto.reload(); };

  const qualify = async (s, cause) => {
    try {
      await api(`/arrets/${s.id}/qualifier`, { method: "POST", body: { cause_id: cause.id, comment: null } });
      showToast(`Arrêt ${s.machine} de ${hm(s.starts_at)} qualifié : ${cause.label}.`);
      reload();
    } catch (e) { showToast(e.message, true); }
  };
  const ackAlarm = async id => { try { await api(`/alarmes/${id}/acquitter`, { method: "POST" }); alarms.reload(); board.reload(); } catch (e) { showToast(e.message, true); } };

  const kpis = [
    { l: "TRS", v: pct(k.oee), d: `Dispo ${pct(k.availability)} · Perf ${pct(k.performance)}`, c: KIND_COLOR[oeeKind(k.oee)], href: null },
    { l: "Production", v: num(k.tonnage_t, 0) + " t", d: k.target_t ? `${pct(k.tonnage_t / k.target_t)} de ${num(k.target_t, 0)} t` : "Objectif non défini" },
    { l: "Débit actuel", v: num(lines.reduce((a, l) => a + (l.debit || 0), 0), 0) + " t/h", d: `${lines.filter(l => lineState(l).kind === "good").length} / ${lines.length} lignes en marche` },
    { l: "Énergie", v: num(k.kwh_per_t, 1) + " kWh/t", d: num(k.energy_kwh, 0) + " kWh" },
    { l: "Arrêts", v: String(k.stop_count ?? 0), d: `${dur(k.unplanned_stop_s)} · ${b.arrets_a_qualifier} à qualifier`, c: b.arrets_a_qualifier ? "var(--orange)" : null, href: "#/arrets" },
    { l: "Alarmes", v: String(b.alarmes_actives), d: `${b.alarmes_non_acquittees} non acquittée${b.alarmes_non_acquittees > 1 ? "s" : ""}`, c: b.alarmes_actives ? "var(--red)" : null, href: "#/alarmes" },
  ];
  const quick = (causes.data || []).slice().sort((x, y) => (x.category === "panne" ? -1 : 0) - (y.category === "panne" ? -1 : 0));
  const tiles = { open, onToggle: setOpen };

  return html`<div class="view">
    <${Band} over=${nowLabel()} title="Synthèse" accent="usine">
      <div class="seg" role="group" aria-label="Période">
        ${PERIODS.map(([id, label]) => html`<button type="button" class=${period === id ? "on" : ""} aria-pressed=${period === id} onClick=${() => setPeriod(id)}>${label}</button>`)}
      </div>
      <span class=${"live" + (lag === null ? " off" : lag > 60 ? " late" : "")}>${lag === null ? "Collecte arrêtée" : lag > 60 ? `Retard ${dur(lag)}` : "En direct"}</span>
    </${Band}>

    <section class="kstrip" aria-label="Indicateurs clés">
      ${kpis.map(x => html`<${x.href ? "a" : "div"} class="kcell" href=${x.href}>
        <span class="l">${x.l}</span><span class="v" style=${x.c ? { color: x.c } : {}}>${x.v}</span><span class="d">${x.d}</span></${x.href ? "a" : "div"}>`)}
    </section>

    <section class="tiles" style="grid-template-columns:repeat(12,minmax(0,1fr));grid-template-rows:minmax(0,1fr) minmax(0,.82fr) minmax(0,1.1fr)">
      <${Tile} id="prod" title=${period === "poste" ? "Production du poste" : "Production · " + p[1].toLowerCase()} col="span 3" ...${tiles}
        detail=${html`<${ProductionDetail} lines=${lines} hourly=${hourly.data} shiftLine=${shiftLine} />`}>
        <div style="display:flex;align-items:baseline;gap:8px"><span class="big">${num(k.tonnage_t, 0)} t</span><span class="sub">sur ${num(k.target_t, 0)} t</span></div>
        <div class="bar"><div style=${{ width: Math.min(100, k.target_t ? (k.tonnage_t / k.target_t) * 100 : 0) + "%", background: "var(--green)" }}></div></div>
        <div style="flex:1;min-height:0"><${Bars} data=${hourlyProd(hourly.data).slice(-12)} unit="t" labelEvery=${3} slots=${12} /></div>
        <span class="sub">Tonnes par heure, 12 dernières heures</span>
      </${Tile}>

      <${Tile} id="trs" title="TRS" col="span 3" ...${tiles} detail=${html`<${TrsDetail} lines=${lines} week=${week.data || []} />`}>
        <div style="display:flex;align-items:center;gap:14px;flex:1;min-height:0">
          <${Ring} value=${k.oee} label="TRS" />
          <div style="display:grid;gap:6px;flex:1;min-width:0">
            ${[["Disponibilité", k.availability], ["Performance", k.performance], ["Qualité", k.oee !== null ? 1 : null]].map(([l, v]) => html`<div class="hbar">
              <span>${l}</span><b class="num">${pct(v)}</b><div class="track"><div style=${{ width: (v || 0) * 100 + "%", background: KIND_COLOR[oeeKind(v)] }}></div></div></div>`)}
          </div>
        </div>
      </${Tile}>

      <${Tile} id="todo" title="À traiter" col="span 3" ...${tiles}
        extra=${b.arrets_a_qualifier ? html`<${Pill} kind="warn">${b.arrets_a_qualifier}</${Pill}>` : ""}
        detail=${html`<${TodoDetail} stops=${todo.data || []} causes=${causes.data || []} onQualify=${qualify} />`}>
        <div class="lst scroll" style="flex:1">
          ${(todo.data || []).slice(0, 3).map(s => html`<div style="flex-direction:column;align-items:stretch;gap:6px">
            <div style="display:flex;gap:8px;align-items:center"><span class="t">${s.machine} · ${hm(s.starts_at)}</span><span class="spacer"></span><span class="sub num">${dur(s.duree_s)}</span></div>
            <div class="chips">${quick.slice(0, 3).map(c => html`<button type="button" class="chip" style="padding:4px 10px;font-size:12px" onClick=${() => qualify(s, c)}>${c.label}</button>`)}
              <a class="chip" style="padding:4px 10px;font-size:12px;text-decoration:none" href=${`#/ligne/${s.machine_id}`}>Autre…</a></div>
          </div>`)}
          ${todo.data && !todo.data.length && html`<p class="empty">Tous les arrêts sont qualifiés.</p>`}
        </div>
      </${Tile}>

      <${Tile} id="alarmes" title="Alarmes" col="span 3" ...${tiles}
        extra=${b.alarmes_actives ? html`<${Pill} kind="bad">${b.alarmes_actives}</${Pill}>` : ""}
        detail=${html`<${AlarmDetail} alarms=${alarms.data || []} onAck=${ackAlarm} onAckAll=${async () => { await api("/alarmes/acquitter-tout", { method: "POST" }); alarms.reload(); board.reload(); }} />`}>
        <div class="lst scroll" style="flex:1">
          ${(alarms.data || []).slice(0, 4).map(a => html`<div>
            <${Pill} kind=${a.priority === "critique" || a.priority === "haute" ? "bad" : a.priority === "moyenne" ? "warn" : "idle"}>${a.priority}</${Pill}>
            <span class="t" title=${a.message}>${a.machine ? a.machine + " · " : ""}${a.message}</span><span class="spacer"></span><span class="sub num">${hm(a.raised_at)}</span></div>`)}
          ${alarms.data && !alarms.data.length && html`<p class="empty">Aucune alarme active.</p>`}
        </div>
      </${Tile}>

      <article class="tile" style="grid-column:span 12">
        <div class="th"><h2>Lignes</h2><span class="spacer"></span><span class="sub">Cliquez une ligne pour le détail et la qualification des arrêts</span></div>
        <div style=${{ display: "grid", gridTemplateColumns: `repeat(${Math.max(1, lines.length)}, minmax(0, 1fr))`, gap: "10px", flex: 1, minHeight: 0 }}>
          ${lines.map(l => html`<${LineCard} l=${l} stops=${(stops.data || []).filter(s => s.machine_id === l.id)} />`)}
          ${!lines.length && html`<p class="empty">Aucune ligne déclarée. Lancez la découverte dans <a href="#/catalogue">Catalogue</a>.</p>`}
        </div>
      </article>

      <${Tile} id="arrets" title="Arrêts · causes" col="span 3" ...${tiles} detail=${html`<${ParetoDetail} pareto=${pareto.data || []} />`}>
        <div class="scroll" style="display:grid;gap:7px;align-content:start;flex:1">${paretoBars(pareto.data || [], 4)}</div>
      </${Tile}>

      <${Tile} id="moteurs" title="Moteurs · dérive" col="span 3" ...${tiles} detail=${html`<${MotorDetail} motors=${motors.data || []} />`}
        extra=${b.moteurs_en_alerte ? html`<${Pill} kind="bad">${b.moteurs_en_alerte} en alerte</${Pill}>` : ""}>
        <${MotorDots} motors=${motors.data || []} />
      </${Tile}>

      <${Tile} id="energie" title="Énergie" col="span 3" ...${tiles}
        detail=${html`<${EnergyDetail} lines=${lines} hourly=${hourly.data} energy=${energy.data} period=${p[1]} />`}>
        <div style="display:flex;align-items:baseline;gap:8px"><span class="big" style="font-size:26px">${num(k.kwh_per_t, 1)}</span><span class="sub">kWh/t · ${num(k.energy_kwh, 0)} kWh</span></div>
        <div style="flex:1;min-height:0"><${Bars} data=${hourlyEnergy(hourly.data).slice(-12)} unit="kWh" color="var(--sky)" slots=${12} labelEvery=${3} /></div>
      </${Tile}>

      <${Tile} id="poste" title="Poste & recettes" col="span 3" ...${tiles} detail=${html`<${ShiftDetail} lines=${lines} />`}>
        ${shiftLine ? html`<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap"><b style="font-size:18px">${shiftLine.shift_label || "Poste en cours"}</b>
          <span class="sub">${hm(shiftLine.starts_at)}–${hm(shiftLine.ends_at)} · fin dans ${dur((T(shiftLine.ends_at) - Date.now()) / 1000)}</span></div>` : html`<p class="empty">Aucun poste en cours.</p>`}
        <div class="lst scroll" style="flex:1">
          ${lines.map(l => html`<div><span class="t">${l.name}</span><span class="sub">${l.team ? "Équipe " + l.team : "Équipe ?"}</span><span class="spacer"></span><span class="sub">${l.recipe_label || ""}</span></div>`)}
        </div>
        <a href="#/planning" style="font-size:13px;font-weight:600">Ouvrir le planning de la semaine →</a>
      </${Tile}>
    </section>
    ${toast}
  </div>`;
}

function LineCard({ l, stops }) {
  const st = lineState(l);
  const prog = l.target_t ? Math.min(1, (l.tonnage_t || 0) / l.target_t) : null;
  return html`<a href=${`#/ligne/${l.id}`} class="lcard" style="text-decoration:none;color:inherit;background:var(--fill2);border-radius:12px;padding:10px 12px;display:flex;flex-direction:column;gap:7px;min-width:0;min-height:0;overflow:hidden">
    <div style="display:flex;align-items:center;gap:8px"><b style="font-size:16px">${l.name}</b><span class="spacer"></span><${Pill} kind=${st.kind}>${st.label}</${Pill}></div>
    <div style="display:flex;align-items:baseline;gap:6px"><span class="num" style=${{ fontSize: "22px", fontWeight: 800, whiteSpace: "nowrap", color: KIND_COLOR[oeeKind(l.oee)] }}>${pct(l.oee)}</span><span class="sub">TRS</span>
      <span class="spacer"></span>${l.unqualified_count ? html`<${Pill} kind="warn">${l.unqualified_count} à qualif.</${Pill}>` : ""}</div>
    ${l.starts_at && html`<${Frise} start=${l.starts_at} end=${l.ends_at} small stops=${stops.filter(s => T(s.ends_at || Date.now()) > T(l.starts_at) && T(s.starts_at) < T(l.ends_at))} />`}
    <div class="sub num" style="display:flex;gap:10px;flex-wrap:wrap"><span>${num(l.debit, 0)} t/h</span><span>${num(l.tonnage_t, 0)}${prog !== null ? ` / ${num(l.target_t, 0)}` : ""} t</span><span>${num(l.kwh_per_t, 1)} kWh/t</span></div>
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
  if (!rows.length) return html`<p class="empty">Aucun arrêt sur la période.</p>`;
  return rows.slice(0, n).map(r => html`<div class="hbar"><span>${r.cause}</span><span class="sub num">${num(r.minutes, 0)} min · ${r.nombre}</span>
    <div class="track"><div class=${r.category === "non_qualifie" ? "warn" : ""} style=${{ width: (r.minutes / max) * 100 + "%" }}></div></div></div>`);
}

function MotorDots({ motors }) {
  const counts = {};
  motors.forEach(m => { const s = m.status || "sans_reference"; counts[s] = (counts[s] || 0) + 1; });
  const sorted = motors.slice().sort((a, b) => Math.abs(b.deviation_pct || 0) - Math.abs(a.deviation_pct || 0)).slice(0, 100);
  return html`<a href="#/moteurs" style="flex:1;min-height:0;display:grid;grid-template-columns:repeat(20,1fr);gap:3px;align-content:start;text-decoration:none" aria-label="Carte des moteurs">
      ${sorted.map(m => html`<span title=${`${m.name} : ${m.deviation_pct === null || m.deviation_pct === undefined ? "–" : num(m.deviation_pct) + " %"}`}
        style=${{ aspectRatio: "1", borderRadius: "50%", background: DRIFT_COLOR[m.status || "sans_reference"] }}></span>`)}
    </a>
    <div class="legend" style="font-size:12px;gap:10px">
      <span><i style="background:var(--green)"></i>${counts.normal || 0} normaux</span>
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
    <div><p class="sub">Production cumulée de l'usine depuis le début du poste, comparée à l'objectif (tirets).</p>
      <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label="Production cumulée">
        <line x1=${X(start)} x2=${X(end)} y1=${Y(0)} y2=${Y(target)} style=${{ stroke: "var(--text2)" }} stroke-dasharray="6 5" stroke-width="2" />
        <polyline fill="none" style=${{ stroke: "var(--green)" }} stroke-width="3" points=${[[start, 0], ...pts].map(p => `${X(p[0])},${Y(p[1])}`).join(" ")} />
        ${[0, 0.5, 1].map(f => html`<text x=${X(start + f * (end - start))} y=${H - 6} text-anchor="middle" font-size="12">${hm(start + f * (end - start))}</text>`)}
        <text x=${X(end) - 4} y=${Y(target) - 6} text-anchor="end" font-size="12">Objectif ${num(target, 0)} t</text>
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
    <div><h3 style="margin:0 0 10px;font-size:14px">TRS par ligne (poste en cours)</h3>
      <div class="table-wrap"><table><thead><tr><th>Ligne</th><th class="num">TRS</th><th class="num">Disponibilité</th><th class="num">Performance</th><th class="num">Arrêts</th></tr></thead>
      <tbody>${lines.map(l => html`<tr><td><a href=${`#/ligne/${l.id}`}>${l.name}</a></td><td class="num"><b style=${{ color: KIND_COLOR[oeeKind(l.oee)] }}>${pct(l.oee)}</b></td>
        <td class="num">${pct(l.availability)}</td><td class="num">${pct(l.performance)}</td><td class="num">${l.stop_count ?? "–"} · ${dur(l.unplanned_stop_s)}</td></tr>`)}</tbody></table></div></div>
    <div><h3 style="margin:0 0 10px;font-size:14px">TRS usine des 7 derniers jours (%)</h3><div style="height:220px"><${Bars} data=${bars} unit="%" labelEvery=${1} target=${75} /></div>
      <p class="sub">Tirets : objectif de 75 %.</p></div>
  </div>`;
}

function TodoDetail({ stops, causes, onQualify }) {
  const [sel, setSel] = useState({});
  if (!stops.length) return html`<p class="empty">Tous les arrêts sont qualifiés.</p>`;
  return html`<div class="table-wrap"><table><thead><tr><th>Ligne</th><th>Début</th><th class="num">Durée</th><th>Code automate</th><th>Cause</th><th></th></tr></thead>
    <tbody>${stops.map(s => html`<tr><td>${s.machine}</td><td>${dt(s.starts_at)}</td><td class="num">${dur(s.duree_s)}</td><td>${s.plc_code ?? ""}</td>
      <td><select value=${sel[s.id] || ""} onChange=${e => setSel({ ...sel, [s.id]: e.target.value })}><option value="">Choisir…</option>
        ${causes.map(c => html`<option value=${c.id}>${c.label}</option>`)}</select></td>
      <td class="row" style="flex-wrap:nowrap"><button class="primary" disabled=${!sel[s.id]} onClick=${() => onQualify(s, causes.find(c => c.id === +sel[s.id]))}>Qualifier</button>
        <a class="btn" href=${`#/ligne/${s.machine_id}`}>Sur la frise</a></td></tr>`)}</tbody></table></div>`;
}

function AlarmDetail({ alarms, onAck, onAckAll }) {
  return html`<div class="row" style="margin-bottom:8px"><span class="sub">${alarms.length} alarmes actives ou non acquittées</span><span class="spacer"></span>
      <button onClick=${onAckAll}>Tout acquitter</button><a class="btn" href="#/alarmes">Historique</a></div>
    <div class="table-wrap"><table><thead><tr><th>Priorité</th><th>Machine</th><th>Message</th><th>Apparition</th><th>État</th><th></th></tr></thead>
    <tbody>${alarms.map(a => html`<tr><td><${Pill} kind=${a.priority === "critique" || a.priority === "haute" ? "bad" : a.priority === "moyenne" ? "warn" : "idle"}>${a.priority}</${Pill}></td>
      <td>${a.machine}</td><td>${a.message}</td><td>${dt(a.raised_at)}</td><td>${a.cleared_at ? "disparue" : html`<b>active</b>`}</td>
      <td>${!a.ack_at && html`<button onClick=${() => onAck(a.id)}>Acquitter</button>`}</td></tr>`)}</tbody></table></div>`;
}

function ParetoDetail({ pareto }) {
  const total = pareto.reduce((a, r) => a + r.minutes, 0);
  return html`<div style="display:grid;grid-template-columns:2fr 1fr;gap:24px">
    <div style="display:grid;gap:10px;align-content:start">${paretoBars(pareto, 20)}</div>
    <div class="lst">${pareto.map(r => html`<div><span class="t">${r.cause}</span><span class="spacer"></span><span class="num sub">${total ? Math.round((r.minutes / total) * 100) : 0} %</span></div>`)}
      <a href="#/arrets" style="font-weight:600;background:none">Tous les arrêts →</a></div></div>`;
}

function MotorDetail({ motors }) {
  const sorted = motors.slice().sort((a, b) => Math.abs(b.deviation_pct || 0) - Math.abs(a.deviation_pct || 0));
  return html`<p class="sub">Écart du courant moyen à la référence, du plus fort au plus faible. <a href="#/moteurs">Recaler une référence →</a></p>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px">
    ${sorted.map(m => html`<a href="#/moteurs" style=${{ textDecoration: "none", color: "inherit", background: "var(--fill2)", borderRadius: "10px", padding: "8px 10px", borderLeft: `4px solid ${DRIFT_COLOR[m.status || "sans_reference"]}` }}>
      <div style="font-weight:700;font-size:13px">${m.name}</div>
      <div class="num" style="font-size:16px;font-weight:800">${m.deviation_pct === null || m.deviation_pct === undefined ? "–" : (m.deviation_pct > 0 ? "+" : "") + num(m.deviation_pct) + " %"}</div>
      <div class="sub num" style="font-size:12px">${num(m.mean_current)} / ${num(m.ref_current)} A</div></a>`)}</div>`;
}

function EnergyDetail({ lines, hourly, energy, period }) {
  const max = Math.max(1, ...lines.map(l => l.kwh_per_t || 0));
  return html`<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
    <div><h3 style="margin:0 0 10px;font-size:14px">Consommation de l'usine, kWh par heure (24 h)</h3><div style="height:200px"><${Bars} data=${hourlyEnergy(hourly)} unit="kWh" color="var(--sky)" labelEvery=${4} slots=${24} /></div>
      <h3 style="margin:16px 0 10px;font-size:14px">kWh/t par ligne (poste en cours)</h3>
      <div style="display:grid;gap:8px">${lines.map(l => html`<div class="hbar"><span>${l.name}</span><b class="num">${num(l.kwh_per_t, 1)}</b><div class="track"><div style=${{ width: ((l.kwh_per_t || 0) / max) * 100 + "%" }}></div></div></div>`)}</div></div>
    <div><h3 style="margin:0 0 10px;font-size:14px">Compteurs les plus consommateurs · ${period.toLowerCase()}</h3>
      ${energy ? html`<div class="lst">${(energy.par_compteur || []).slice(0, 12).map(c => html`<div><span class="t">${c.compteur}</span><span class="sub">${c.line || c.usage || ""}</span><span class="spacer"></span><b class="num">${num(c.kwh, 0)} kWh</b></div>`)}</div>
        <p class="sub">Puissance appelée : ${num(energy.puissance_kw, 0)} kW · <a href="#/energie">Détail énergie →</a></p>` : html`<p class="empty">Chargement…</p>`}</div></div>`;
}

function ShiftDetail({ lines }) {
  const d = new Date(); d.setHours(6, 0, 0, 0); if (Date.now() < d.getTime()) d.setDate(d.getDate() - 1);
  const day = useApi(`/shifts?depuis=${encodeURIComponent(d.toISOString())}&jours=1`);
  const tmpl = [...new Set((day.data || []).map(s => s.starts_at))].sort();
  return html`<div class="row" style="margin-bottom:10px"><span class="sub">Postes de la journée par ligne : équipe, recette ou activité planifiée.</span><span class="spacer"></span><a class="btn primary" href="#/planning">Modifier le planning</a></div>
    <div class="table-wrap"><table><thead><tr><th>Ligne</th>${tmpl.map(t => html`<th>${(day.data || []).find(s => s.starts_at === t)?.label} · ${hm(t)}</th>`)}</tr></thead>
    <tbody>${lines.map(l => html`<tr><td><b>${l.name}</b></td>${tmpl.map(t => {
      const s = (day.data || []).find(x => x.line_id === l.id && x.starts_at === t);
      if (!s) return html`<td></td>`;
      const label = s.kind === "maintenance" ? "Maintenance" : s.kind === "arret" ? "Arrêt planifié" : s.recipe_label || "Production";
      return html`<td>${s.team ? html`<b>Équipe ${s.team}</b> · ` : ""}${label}<div class="sub num">${s.tonnage_t !== null && s.tonnage_t !== undefined ? `${num(s.tonnage_t, 0)} / ` : ""}${num(s.target_t, 0)} t</div></td>`;
    })}</tr>`)}</tbody></table></div>`;
}
