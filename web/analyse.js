// Analyse de performance : disponibilité en tête, TRS, pertes et Pareto des arrêts, sur 7 ou 31 jours, par ligne.
import { html, useState } from "./vendor/preact-htm.module.js";
import { useApi, Msg, Band, Icon, ICON, num, pct, dur, dhms, fmtDayShort, oeeKind, tr, N_, fmt } from "./lib.js";
import { Pareto, Columns } from "./charts.js";
import { aggregate } from "./synthese.js";

const PERIODS = [[7, N_("7 j")], [31, N_("31 j")]];
const KIND = { good: "var(--green)", warn: "var(--orange)", bad: "var(--red)", idle: "var(--muted)" };
// seuils de disponibilité (usuels) : 90 % bon, 80 % à surveiller
const dispoKind = v => (v === null || v === undefined ? "idle" : v >= 0.9 ? "good" : v >= 0.8 ? "warn" : "bad");
const prodDay = iso => { const d = new Date(iso); if (d.getHours() < 6) d.setDate(d.getDate() - 1); d.setHours(0, 0, 0, 0); return d.getTime(); };

export function Analyse() {
  const [days, setDays] = useState(7);
  const [line, setLine] = useState(null);
  const from = new Date(); from.setHours(6, 0, 0, 0); from.setDate(from.getDate() - days + (Date.now() < from.getTime() ? 0 : 1));
  const shifts = useApi(`/shifts?depuis=${encodeURIComponent(from.toISOString())}&jours=${days}`, [days], 60000);
  const pareto = useApi(`/arrets/pareto?depuis=${encodeURIComponent(from.toISOString())}${line ? "&line_id=" + line : ""}`, [days, line], 60000);
  const all = (shifts.data || []).filter(s => new Date(s.starts_at) <= new Date());
  const lines = [...new Map(all.map(s => [s.line_id, s.line])).entries()].sort((a, b) => a[1].localeCompare(b[1]));
  const sel = line ? all.filter(s => s.line_id === line) : all;
  const k = aggregate(sel);
  const runS = sel.reduce((a, s) => a + (s.run_s || 0), 0);
  const nStops = sel.reduce((a, s) => a + (s.stop_count || 0), 0);
  // objectif au prorata du temps écoulé pour le poste en cours
  const now = Date.now();
  const target = sel.reduce((a, s) => {
    if (s.tonnage_t === null || s.tonnage_t === undefined || !s.target_t) return a;
    const t0 = new Date(s.starts_at).getTime(), t1 = new Date(s.ends_at).getTime();
    return a + s.target_t * Math.min(1, Math.max(0, (now - t0) / Math.max(1, t1 - t0)));
  }, 0);

  const byDay = {};
  sel.forEach(s => { (byDay[prodDay(s.starts_at)] = byDay[prodDay(s.starts_at)] || []).push(s); });
  const dayBars = Object.keys(byDay).sort().filter(t => aggregate(byDay[t]).availability !== null).map(t => {
    const a = aggregate(byDay[t]);
    return { label: fmtDayShort.format(new Date(+t)).replace(".", ""), v: a.availability === null ? 0 : a.availability * 100, color: KIND[dispoKind(a.availability)] };
  });
  const perLine = lines.map(([id, name]) => ({ id, name, ...aggregate(all.filter(s => s.line_id === id)) }));

  const band = html`<${Band} over=${tr("MES · Outils")} title=${tr("Analyse")}>
    <div class="seg" role="group" aria-label=${tr("Ligne")}>
      <button type="button" class=${line === null ? "on" : ""} onClick=${() => setLine(null)}>${tr("Usine")}</button>
      ${lines.map(([id, name]) => html`<button type="button" class=${line === id ? "on" : ""} onClick=${() => setLine(id)}>${name}</button>`)}</div>
    <div class="seg" role="group" aria-label=${tr("Période")}>${PERIODS.map(([v, l]) => html`<button type="button" class=${days === v ? "on" : ""} aria-pressed=${days === v} onClick=${() => setDays(v)}>${tr(l)}</button>`)}</div>
    <a class="seg" style="padding:7px 12px;font-size:13px;font-weight:600;color:var(--seg-tx);text-decoration:none;display:flex;gap:6px;align-items:center" href="/grafana/" target="_blank" rel="noopener" title=${tr("Tendances et tableaux Grafana")}>Grafana <${Icon} d=${ICON.link} size="14" /></a>
  </${Band}>`;
  if (!shifts.data) return html`<div class="view">${band}${shifts.error ? html`<${Msg} error=${shifts.error} />` : html`<p class="muted">${tr("Chargement…")}</p>`}</div>`;

  const kpis = [
    [tr("Disponibilité"), pct(k.availability), tr("temps de marche / ouverture"), KIND[dispoKind(k.availability)]],
    [tr("TRS"), pct(k.oee), `${tr("Perf")} ${pct(k.performance)}`, KIND[oeeKind(k.oee)]],
    [tr("Production"), num(k.tonnage_t, 0) + " t", target ? tr("{p} de {n} t", { p: pct(k.tonnage_t / target), n: num(target, 0) }) : "–"],
    [tr("Arrêts"), String(nStops), dhms(k.unplanned_stop_s) + " " + tr("non planifiés")],
    ["MTBF", nStops ? dur(runS / nStops) : "–", nStops ? "MTTR " + dur(k.unplanned_stop_s / nStops) : "–"],
  ];
  return html`<div class="view">${band}
    <section class="kstrip" style="grid-template-columns:1.3fr repeat(4,minmax(0,1fr))" aria-label=${tr("Indicateurs")}>
      ${kpis.map(([l, v, d, c], i) => html`<div class="kcell" style=${i === 0 ? { boxShadow: `inset 4px 0 0 ${c}` } : {}}><span class="l">${l}</span>
        <span class="v" style=${{ ...(c ? { color: c } : {}), ...(i === 0 ? { fontSize: "30px" } : {}) }}>${v}</span><span class="d">${d}</span></div>`)}
    </section>
    <section class="tiles" style="grid-template-columns:repeat(12,minmax(0,1fr));grid-template-rows:minmax(0,1fr) minmax(0,1fr)">
      <article class="tile" style="grid-column:span 8">
        <div class="th"><h2>${tr("Disponibilité par jour")}</h2><span class="spacer"></span>
          <div class="legend"><span><i style="background:var(--green)"></i>≥ 90 %</span><span><i style="background:var(--orange)"></i>≥ 80 %</span><span><i style="background:var(--red)"></i>${"< 80 %"}</span></div></div>
        <${Columns} data=${dayBars} unit="%" max=${100} fmt=${v => num(v, 0) + " %"} labelEvery=${dayBars.length > 14 ? 3 : 1} target=${90} targetLabel=${tr("Objectif {n} %", { n: 90 })} />
      </article>
      <article class="tile" style="grid-column:span 4">
        <div class="th"><h2>${tr("Par ligne")}</h2><span class="spacer"></span><span class="sub">${tr("Dispo · TRS")}</span></div>
        <div class="scroll" style="display:grid;gap:12px;align-content:start;flex:1">
          ${perLine.map(l => html`<button type="button" class="hbar" style="all:unset;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;font-size:13px;cursor:pointer;opacity:${line && line !== l.id ? 0.5 : 1}"
              onClick=${() => setLine(line === l.id ? null : l.id)}>
            <span><b>${l.name}</b></span><span class="num"><b style=${{ color: KIND[dispoKind(l.availability)] }}>${pct(l.availability)}</b> <span class="sub">· ${pct(l.oee)}</span></span>
            <div class="track"><div style=${{ width: (l.availability || 0) * 100 + "%", background: KIND[dispoKind(l.availability)] }}></div></div></button>`)}
          ${!perLine.length && html`<p class="empty">${tr("Aucun poste sur la période.")}</p>`}
        </div>
      </article>
      <article class="tile" style="grid-column:span 12">
        <div class="th"><h2>${tr("Pareto des arrêts")}</h2><span class="spacer"></span><span class="sub">${tr("non planifiés · jj:hh:mm:ss")}</span></div>
        <${Pareto} items=${(pareto.data || []).map(r => ({ label: r.cause, v: r.minutes * 60, sub: tr("{n} arrêts", { n: r.nombre }), color: r.category === "non_qualifie" ? "var(--orange)" : null }))}
          fmt=${dhms} empty=${tr("Aucun arrêt sur la période.")} />
      </article>
    </section>
  </div>`;
}
