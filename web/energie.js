// Énergie : en production / hors production, gros consommateurs (anneau), Pareto des compteurs, kWh/t par ligne.
import { html, useState } from "./vendor/preact-htm.module.js";
import { useApi, Msg, Band, num, hm, fmtDayShort, tr, N_, fmt } from "./lib.js";
import { StackBars, Donut, Pareto, topN, short } from "./charts.js";

const PERIODS = [[1, "24 h"], [7, N_("7 j")], [31, N_("31 j")]];
const GROUPS = [["compteur", N_("Compteur")], ["ligne", N_("Ligne")], ["usage", N_("Usage")]];
const kwh = v => (v >= 1e6 ? num(v / 1e6, 2) + " GWh" : v >= 1e3 ? num(v / 1e3, 1) + " MWh" : num(v, 0) + " kWh");
const PROD = "var(--green)", HORS = "var(--orange)";

export function Energie() {
  const [days, setDays] = useState(7);
  const [group, setGroup] = useState("compteur");
  const { data, error } = useApi(`/energie/analyse?jours=${days}`, [], 60000);
  const band = html`<${Band} over=${tr("MES · Outils")} title=${tr("Énergie")}>
    <div class="seg" role="group" aria-label=${tr("Période")}>${PERIODS.map(([v, l]) => html`<button type="button" class=${days === v ? "on" : ""} aria-pressed=${days === v} onClick=${() => setDays(v)}>${tr(l)}</button>`)}</div>
  </${Band}>`;
  if (!data) return html`<div class="view">${band}${error ? html`<${Msg} error=${error} />` : html`<p class="muted">${tr("Chargement…")}</p>`}</div>`;
  const t = data.total;
  const horsPct = t.kwh ? t.hors_kwh / t.kwh : null;
  const serie = data.serie.map(r => {
    const d = new Date(r.t);
    return { label: data.pas === "jour" ? fmtDayShort.format(d).replace(".", "") : hm(d).slice(0, 2) + "h",
      tip: data.pas === "jour" ? fmtDayShort.format(d) : hm(d), parts: [r.prod_kwh, r.hors_kwh] };
  });
  const by = {};
  data.compteurs.forEach(c => {
    const k = group === "compteur" ? c.compteur : group === "ligne" ? c.line || tr("Commun") : c.usage || tr("Non renseigné");
    by[k] = (by[k] || 0) + c.kwh;
  });
  const pie = topN(Object.entries(by).map(([label, v]) => ({ label, v })), 6);
  const pareto = data.compteurs.map(c => ({ label: c.compteur, v: c.kwh, sub: [c.line, c.usage].filter(Boolean).join(" · ") + ` · ${tr("hors prod.")} ${short(c.hors_kwh)} kWh` }));
  const maxR = Math.max(1, ...data.lignes.map(l => l.kwh_per_t || 0));
  const kpis = [
    [tr("Puissance"), num(data.puissance_kw, 0) + " kW", tr("instantanée")],
    [tr("Consommation"), kwh(t.kwh), tr("{n} t produites", { n: num(t.tonnage_t, 0) })],
    [tr("En production"), kwh(t.prod_kwh), horsPct === null ? "–" : num((1 - horsPct) * 100, 0) + " %"],
    [tr("Hors production"), kwh(t.hors_kwh), horsPct === null ? "–" : num(horsPct * 100, 0) + " %", horsPct > 0.2 ? "warn" : ""],
    [tr("Ratio"), num(t.kwh_per_t, 1) + " kWh/t", tr("site")],
  ];
  return html`<div class="view">${band}
    <section class="kstrip" style="grid-template-columns:repeat(5,minmax(0,1fr))" aria-label=${tr("Indicateurs énergie")}>
      ${kpis.map(([l, v, d, c]) => html`<div class="kcell"><span class="l">${l}</span><span class=${"v " + (c || "")}>${v}</span><span class="d">${d}</span></div>`)}
    </section>
    <section class="tiles" style="grid-template-columns:repeat(12,minmax(0,1fr));grid-template-rows:minmax(0,1fr) minmax(0,1fr)">
      <article class="tile" style="grid-column:span 7">
        <div class="th"><h2>${data.pas === "jour" ? tr("Consommation par jour") : tr("Consommation par heure")}</h2><span class="spacer"></span>
          <div class="legend"><span><i style=${{ background: PROD }}></i>${tr("En production")}</span><span><i style=${{ background: HORS }}></i>${tr("Hors production")}</span></div></div>
        <${StackBars} data=${serie} names=${[tr("En production"), tr("Hors production")]} colors=${[PROD, HORS]} unit="kWh"
          labelEvery=${serie.length > 16 ? Math.ceil(serie.length / 12) : 1} />
      </article>
      <article class="tile" style="grid-column:span 5">
        <div class="th"><h2>${tr("Consommateurs")}</h2><span class="spacer"></span>
          <div class="seg light" role="group" aria-label=${tr("Regrouper par")}>${GROUPS.map(([k, l]) => html`<button type="button" class=${group === k ? "on" : ""} onClick=${() => setGroup(k)}>${tr(l)}</button>`)}</div></div>
        <${Donut} items=${pie} unit="kWh" center=${short(t.kwh)} centerSub="kWh" size=${150} />
      </article>
      <article class="tile" style="grid-column:span 7">
        <div class="th"><h2>${tr("Pareto des compteurs")}</h2></div>
        <${Pareto} items=${pareto} unit="kWh" fmt=${short} />
      </article>
      <article class="tile" style="grid-column:span 5">
        <div class="th"><h2>${tr("Par ligne")}</h2><span class="spacer"></span><span class="sub">kWh/t</span></div>
        <div class="scroll" style="display:grid;gap:12px;align-content:start;flex:1">
          ${data.lignes.map(l => html`<div class="hbar"><span>${l.line}</span><span class="sub num"><b style="color:var(--text)">${num(l.kwh_per_t, 1)}</b> · ${short(l.kwh || 0)} kWh</span>
            <div class="track"><div style=${{ width: ((l.kwh_per_t || 0) / maxR) * 100 + "%" }}></div></div></div>`)}
          ${!data.lignes.length && html`<p class="empty">${tr("Aucun poste sur la période.")}</p>`}
        </div>
      </article>
    </section>
  </div>`;
}
