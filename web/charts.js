// Graphiques de l'application : barres empilées, anneau (camembert) et Pareto.
// Dessinés à la taille réelle (textes non déformés), lisibles au doigt : un appui affiche la valeur.
import { html, useState, useRef, useLayoutEffect } from "./vendor/preact-htm.module.js";
import { num } from "./lib.js";

// couleurs des catégories (ordre fixe, contrôlé pour les daltonismes) ; la dernière couleur sert à « Autres »
export const CAT = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];
export const OTHER = "var(--muted)";

function useSize() {
  const ref = useRef(null);
  const [s, set] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current; if (!el) return;
    const ro = new ResizeObserver(() => set({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el); return () => ro.disconnect();
  }, []);
  return [ref, s];
}
const niceStep = r => { const p = 10 ** Math.floor(Math.log10(r || 1)), f = r / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; };
const ticks = (max, n = 4) => { const st = niceStep(max / n), top = Math.ceil(max / st) * st; const out = []; for (let v = 0; v <= top + st / 2; v += st) out.push(v); return out; };
// valeur courte pour les axes : 12 500 → 12,5 k
export const short = v => (Math.abs(v) >= 1e6 ? num(v / 1e6, 1) + " M" : Math.abs(v) >= 1e4 ? num(v / 1e3, 0) + " k" : Math.abs(v) >= 1e3 ? num(v / 1e3, 1) + " k" : num(v, 0));

function Tip({ x, w, children }) {
  return html`<div class="chart-tip wide" style=${{ left: Math.min(w - 170, Math.max(0, x - 85)) + "px" }}>${children}</div>`;
}

// Barres empilées : data [{ label, tip, parts: [v1, v2…] }], names / colors des parties
export function StackBars({ data, names, colors, unit = "", height = null, labelEvery = 1, empty = "Pas de données sur la période." }) {
  const [ref, size] = useSize();
  const [hi, setHi] = useState(null);
  const box = { position: "relative", width: "100%", minWidth: 0, ...(height ? { height: height + "px" } : { flex: 1, minHeight: "120px" }) };
  if (!data.length || !data.some(d => d.parts.some(v => v > 0))) return html`<div ref=${ref} class="chart-box" style=${box}><p class="empty">${empty}</p></div>`;
  const W = size.w || 600, H = size.h || height || 200, P = { l: 46, r: 8, t: 10, b: 22 };
  const max = Math.max(1, ...data.map(d => d.parts.reduce((a, v) => a + (v || 0), 0)));
  const yt = ticks(max), top = yt[yt.length - 1];
  const Y = v => P.t + (1 - v / top) * (H - P.t - P.b);
  const bw = (W - P.l - P.r) / data.length, gap = Math.max(Math.min(8, bw * 0.25), bw - 64);
  const pick = e => { const r = ref.current.getBoundingClientRect(); const i = Math.floor((e.clientX - r.left - P.l) / bw); setHi(i >= 0 && i < data.length ? i : null); };
  const d = hi !== null && data[hi];
  return html`<div ref=${ref} class="chart-box" style=${box} onPointerMove=${pick} onPointerDown=${pick} onPointerLeave=${() => setHi(null)}>
    <svg class="chart2" width=${W} height=${H} role="img" aria-label=${names.join(", ")}>
      ${yt.map(v => html`<line class="grid" x1=${P.l} x2=${W - P.r} y1=${Y(v)} y2=${Y(v)} /><text x=${P.l - 8} y=${Y(v) + 4} text-anchor="end">${short(v)}</text>`)}
      ${data.map((r, i) => {
        let acc = 0;
        const x = P.l + i * bw + gap / 2, w = Math.max(1, bw - gap);
        return html`<g opacity=${hi === null || hi === i ? 1 : 0.45}>${r.parts.map((v, j) => {
          if (!v) return null;
          const y0 = Y(acc), y1 = Y(acc + v); acc += v;
          const isTop = r.parts.slice(j + 1).every(x => !x);
          const h = Math.max(0, y0 - y1 - (j ? 2 : 0));
          return html`<rect x=${x} y=${y1} width=${w} height=${h} rx=${isTop ? Math.min(4, w / 2) : 0} style=${{ fill: colors[j] }} />`;
        })}</g>`;
      })}
      ${data.map((r, i) => i % labelEvery === 0 ? html`<text x=${P.l + (i + 0.5) * bw} y=${H - 5} text-anchor="middle">${r.label}</text>` : null)}
    </svg>
    ${d && html`<${Tip} x=${P.l + (hi + 0.5) * bw} w=${W}><span>${d.tip || d.label}</span>
      ${d.parts.map((v, j) => html`<span class="trow"><i style=${{ background: colors[j] }}></i>${names[j]}<b class="num">${num(v, 0)} ${unit}</b></span>`)}</${Tip}>`}
  </div>`;
}

// Anneau (camembert) : items [{ label, v, color }] ; la légende donne valeur et part
export function Donut({ items, unit = "", center = null, centerSub = "", size = 180, empty = "Pas de données." }) {
  const [hi, setHi] = useState(null);
  const total = items.reduce((a, x) => a + (x.v || 0), 0);
  if (!total) return html`<p class="empty">${empty}</p>`;
  const r = 42, c = 2 * Math.PI * r;
  let acc = 0;
  const gap = items.length > 1 ? 0.6 : 0;
  const cur = hi !== null ? items[hi] : null;
  return html`<div class="donut">
    <svg viewBox="0 0 100 100" style=${{ width: size + "px", height: size + "px", flex: "none" }} role="img" aria-label="Répartition">
      <circle cx="50" cy="50" r=${r} fill="none" style=${{ stroke: "var(--fill)" }} stroke-width="14" />
      ${items.map((x, i) => {
        const len = (x.v / total) * c, off = acc; acc += len;
        return html`<circle cx="50" cy="50" r=${r} fill="none" style=${{ stroke: x.color, cursor: "pointer" }} stroke-width=${hi === i ? 17 : 14}
          stroke-dasharray=${`${Math.max(0, len - gap)} ${c}`} stroke-dashoffset=${-off} transform="rotate(-90 50 50)"
          onPointerEnter=${() => setHi(i)} onPointerDown=${() => setHi(i)} onPointerLeave=${() => setHi(null)}><title>${x.label}</title></circle>`;
      })}
      <text x="50" y=${cur ? 50 : 52} text-anchor="middle" font-size=${cur ? 13 : 15} font-weight="800" style=${{ fill: "var(--text)" }}>${cur ? Math.round((cur.v / total) * 100) + " %" : center}</text>
      <text x="50" y=${cur ? 62 : 64} text-anchor="middle" font-size="7.5" style=${{ fill: "var(--text2)" }}>${cur ? (cur.label.length > 16 ? cur.label.slice(0, 15) + "…" : cur.label) : centerSub}</text>
    </svg>
    <div class="dleg">${items.map((x, i) => html`<div class=${hi === i ? "on" : ""} onPointerEnter=${() => setHi(i)} onPointerLeave=${() => setHi(null)} onClick=${() => setHi(hi === i ? null : i)}>
      <i style=${{ background: x.color }}></i><span class="t">${x.label}</span><b class="num">${short(x.v)}${unit ? " " + unit : ""}</b><span class="num sub">${Math.round((x.v / total) * 100)} %</span></div>`)}</div>
  </div>`;
}

// Regroupe en N parts plus « Autres »
export function topN(rows, n, label = "Autres") {
  const s = rows.slice().sort((a, b) => b.v - a.v);
  // peu d'entités (lignes, usages) : couleur attachée à l'entité (ordre alphabétique), pas à son rang
  const names = rows.map(x => x.label).sort((a, b) => a.localeCompare(b, "fr", { numeric: true }));
  const col = (x, i) => x.color || CAT[(rows.length <= n ? names.indexOf(x.label) : i) % CAT.length];
  const head = s.slice(0, n).map((x, i) => ({ ...x, color: col(x, i) }));
  const rest = s.slice(n).reduce((a, x) => a + x.v, 0);
  return rest > 0 ? [...head, { label: `${label} (${s.length - n})`, v: rest, color: OTHER }] : head;
}

// Pareto : barres triées et cumul en % (points étiquetés, repère 80 %) ; une seule échelle verticale (les barres)
export function Pareto({ items, unit = "", fmt = v => num(v, 0), height = null, max = 12, empty = "Aucune donnée sur la période." }) {
  const [ref, size] = useSize();
  const [hi, setHi] = useState(null);
  const box = { position: "relative", width: "100%", minWidth: 0, ...(height ? { height: height + "px" } : { flex: 1, minHeight: "140px" }) };
  const all = items.filter(x => x.v > 0).sort((a, b) => b.v - a.v);
  if (!all.length) return html`<div ref=${ref} class="chart-box" style=${box}><p class="empty">${empty}</p></div>`;
  const rows = all.length > max ? [...all.slice(0, max - 1), { label: `Autres (${all.length - max + 1})`, v: all.slice(max - 1).reduce((a, x) => a + x.v, 0), other: true }] : all;
  const total = rows.reduce((a, x) => a + x.v, 0);
  let cum = 0;
  const pts = rows.map(x => (cum += x.v) / total);
  const W = size.w || 600, H = size.h || height || 220, P = { l: 8, r: 8, t: 22, b: 40 };
  const bw = (W - P.l - P.r) / rows.length, gap = Math.max(Math.min(10, bw * 0.28), bw - 90);
  const top = Math.max(...rows.map(x => x.v)) * 1.05;
  const Y = v => P.t + (1 - v / top) * (H - P.t - P.b);
  const Yc = f => P.t + (1 - f) * (H - P.t - P.b);
  const cx = i => P.l + (i + 0.5) * bw;
  const i80 = pts.findIndex(f => f >= 0.8);
  const maxChars = Math.max(4, Math.floor(bw / 6.5));
  const cut = s => (s.length > maxChars ? s.slice(0, maxChars - 1) + "…" : s);
  const pick = e => { const r = ref.current.getBoundingClientRect(); const i = Math.floor((e.clientX - r.left - P.l) / bw); setHi(i >= 0 && i < rows.length ? i : null); };
  const d = hi !== null && rows[hi];
  return html`<div ref=${ref} class="chart-box" style=${box} onPointerMove=${pick} onPointerDown=${pick} onPointerLeave=${() => setHi(null)}>
    <svg class="chart2" width=${W} height=${H} role="img" aria-label="Pareto">
      ${rows.map((x, i) => html`<rect x=${P.l + i * bw + gap / 2} y=${Y(x.v)} width=${Math.max(1, bw - gap)} height=${Math.max(0, Y(0) - Y(x.v))} rx=${Math.min(4, (bw - gap) / 2)}
        style=${{ fill: x.other ? OTHER : x.color || (i80 >= 0 && i <= i80 ? "var(--primary)" : "var(--sky)") }} opacity=${hi === null || hi === i ? 1 : 0.5} />`)}
      <line x1=${P.l} x2=${W - P.r} y1=${Yc(0.8)} y2=${Yc(0.8)} style=${{ stroke: "var(--text2)" }} stroke-dasharray="4 4" />
      <text x=${P.l + 2} y=${Yc(0.8) - 5} class="bl halo">80 %</text>
      <polyline fill="none" style=${{ stroke: "var(--text)" }} stroke-width="2" stroke-linejoin="round" points=${pts.map((f, i) => `${cx(i)},${Yc(f)}`).join(" ")} />
      ${pts.map((f, i) => html`<circle cx=${cx(i)} cy=${Yc(f)} r="4" style=${{ fill: "var(--text)", stroke: "var(--card)" }} stroke-width="2" />`)}
      ${pts.map((f, i) => (i === 0 || i === i80) && bw > 34 ? html`<text x=${cx(i)} y=${Yc(f) - 11} text-anchor="middle" class="bl halo">${Math.round(f * 100)} %</text>` : null)}
      ${rows.map((x, i) => html`<text x=${cx(i)} y=${H - P.b + 15} text-anchor="middle">${cut(x.label)}</text>
        ${bw > 44 ? html`<text x=${cx(i)} y=${H - P.b + 30} text-anchor="middle" style=${{ fill: "var(--text)", fontWeight: 700 }}>${fmt(x.v)}</text>` : null}`)}
    </svg>
    ${d && html`<${Tip} x=${cx(hi)} w=${W}><b>${d.label}</b><span class="num">${fmt(d.v)} ${unit} · ${Math.round((d.v / total) * 100)} %</span>
      ${d.sub ? html`<span>${d.sub}</span>` : null}<span class="num">Cumul ${Math.round(pts[hi] * 100)} %</span></${Tip}>`}
  </div>`;
}

// Colonnes avec axe, valeur au-dessus de chaque colonne et ligne d'objectif : data [{ label, v, color, tip }]
export function Columns({ data, unit = "", fmt = v => num(v, 0), max = null, target = null, targetLabel = "", height = null, labelEvery = 1, empty = "Pas de données sur la période." }) {
  const [ref, size] = useSize();
  const [hi, setHi] = useState(null);
  const box = { position: "relative", width: "100%", minWidth: 0, ...(height ? { height: height + "px" } : { flex: 1, minHeight: "120px" }) };
  if (!data.length) return html`<div ref=${ref} class="chart-box" style=${box}><p class="empty">${empty}</p></div>`;
  const W = size.w || 600, H = size.h || height || 200, P = { l: 40, r: 8, t: 18, b: 22 };
  const yt = ticks(max ?? Math.max(1, target || 0, ...data.map(d => d.v || 0))), top = max ?? yt[yt.length - 1];
  const Y = v => P.t + (1 - Math.min(v, top) / top) * (H - P.t - P.b);
  const bw = (W - P.l - P.r) / data.length, gap = Math.max(Math.min(10, bw * 0.3), bw - 72);
  const pick = e => { const r = ref.current.getBoundingClientRect(); const i = Math.floor((e.clientX - r.left - P.l) / bw); setHi(i >= 0 && i < data.length ? i : null); };
  const d = hi !== null && data[hi];
  return html`<div ref=${ref} class="chart-box" style=${box} onPointerMove=${pick} onPointerDown=${pick} onPointerLeave=${() => setHi(null)}>
    <svg class="chart2" width=${W} height=${H} role="img" aria-label=${unit}>
      ${(max ? [0, 0.25, 0.5, 0.75, 1].map(f => f * max) : yt).map(v => html`<line class="grid" x1=${P.l} x2=${W - P.r} y1=${Y(v)} y2=${Y(v)} /><text x=${P.l - 8} y=${Y(v) + 4} text-anchor="end">${short(v)}</text>`)}
      ${data.map((r, i) => html`<rect x=${P.l + i * bw + gap / 2} y=${Y(r.v || 0)} width=${Math.max(1, bw - gap)} height=${Math.max(0, Y(0) - Y(r.v || 0))}
        rx=${Math.min(4, (bw - gap) / 2)} style=${{ fill: r.color || "var(--primary)" }} opacity=${hi === null || hi === i ? 1 : 0.5} />
        ${bw > 30 ? html`<text x=${P.l + (i + 0.5) * bw} y=${Y(r.v || 0) - 5} text-anchor="middle" class="bl halo">${fmt(r.v)}</text>` : null}`)}
      ${target !== null ? html`<line x1=${P.l} x2=${W - P.r} y1=${Y(target)} y2=${Y(target)} style=${{ stroke: "var(--text2)" }} stroke-dasharray="5 4" />
        <text x=${W - P.r} y=${Y(target) - 5} text-anchor="end" class="bl halo">${targetLabel}</text>` : null}
      ${data.map((r, i) => i % labelEvery === 0 ? html`<text x=${P.l + (i + 0.5) * bw} y=${H - 5} text-anchor="middle">${r.label}</text>` : null)}
    </svg>
    ${d && html`<${Tip} x=${P.l + (hi + 0.5) * bw} w=${W}><b>${d.tip || d.label}</b><span class="num">${fmt(d.v)} ${unit}</span></${Tip}>`}
  </div>`;
}
