// Planning de la semaine : axe horizontal 24 h (6 h → 6 h), axe vertical les jours, les lignes empilées dans chaque jour.
// Un poste se crée avec « + » (ou en touchant un créneau vide), se modifie, se duplique ou se supprime depuis son menu.
// Les arrêts planifiés (arrêt ou maintenance) sont des plages à l'intérieur du poste. Une semaine se copie ou se vide
// d'un coup, et la copie automatique reprend la semaine en cours dans une semaine suivante restée vide.
import { html, useState, useEffect, useRef } from "./vendor/preact-htm.module.js";
import { api, useApi, Msg, Band, Dialog, useToast, can, hm } from "./lib.js";

const DAY_MS = 86400e3, MIN = 60e3, DAY_START_H = 6;
const T = v => new Date(v).getTime();
const TEAMS = ["A", "B", "C", "D"];
const DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const RECIPE_COLORS = [["#2F5D8A", "#FFFFFF"], ["#7FD3E3", "#062C33"], ["#82A1CD", "#000B44"], ["#4A6A8C", "#FFFFFF"], ["#B7C9E2", "#000B44"], ["#1B2F41", "#FFFFFF"]];
const STOP_LABEL = { arret: "Arrêt", maintenance: "Maintenance" };
const fDay = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric" });
const fDayLong = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "short" });
const fWeek = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });
const SHOWN_KEY = "mes-planning-lignes";

function weekStart(offset) {
  const d = new Date();
  if (d.getHours() < DAY_START_H) d.setDate(d.getDate() - 1);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + 7 * offset);
  d.setHours(DAY_START_H, 0, 0, 0);
  return d;
}
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };   // garde l'heure locale
const isoDate = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dayOf = t => { const d = new Date(t); if (d.getHours() < DAY_START_H) d.setDate(d.getDate() - 1); d.setHours(DAY_START_H, 0, 0, 0); return d; };
// heure « HH:MM » <-> instant, dans la journée de planning (6 h → 6 h) qui commence à `day`
const toTime = t => { const d = new Date(t); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
function atTime(day, v, isEnd = false) {
  const [H, M] = (v || "").split(":").map(Number);
  if (isNaN(H) || isNaN(M)) return null;
  let off = ((H * 60 + M) - DAY_START_H * 60 + 1440) % 1440;
  if (isEnd && off === 0) off = 1440;
  const d = new Date(day); d.setMinutes(d.getMinutes() + off);
  return d;
}
const hLabel = t => { const d = new Date(t); return d.getMinutes() ? `${d.getHours()} h ${String(d.getMinutes()).padStart(2, "0")}` : `${d.getHours()} h`; };
function readShown() { try { const v = JSON.parse(localStorage.getItem(SHOWN_KEY)); return Array.isArray(v) ? v : null; } catch (e) { return null; } }

export function Planning({ user }) {
  const [offset, setOffset] = useState(0);
  const start = weekStart(offset), end = addDays(start, 6);
  const shifts = useApi(`/shifts?depuis=${encodeURIComponent(start.toISOString())}&jours=7`, [offset], 60000);
  const recipes = useApi("/recettes?statut=validee");
  const dash = useApi("/tableau-de-bord");
  const auto = useApi("/planning/copie-auto");
  const [hidden, setHidden] = useState(() => readShown() || []);   // lignes masquées (id)
  const [sel, setSel] = useState(null);          // { id, rect } : menu d'un poste
  const [dlg, setDlg] = useState(null);          // fenêtre ouverte
  const [toast, showToast] = useToast();
  const edit = can(user, "chef_equipe");
  const now = Date.now();

  const list = shifts.data || [];
  const lines = (dash.data ? dash.data.lignes : [...new Map(list.map(s => [s.line_id, { id: s.line_id, name: s.line }])).values()])
    .map(l => ({ id: l.id, name: l.name }));
  const vis = lines.filter(l => !hidden.includes(l.id));
  const recipeList = recipes.data || [];
  const colorOf = rid => { const i = recipeList.findIndex(r => r.id === rid); return RECIPE_COLORS[(i < 0 ? 0 : i) % RECIPE_COLORS.length]; };
  const byId = Object.fromEntries(list.map(s => [s.id, s]));
  const reload = () => shifts.reload();
  const days = [...Array(7)].map((_, i) => addDays(start, i));
  const nowIn = now >= start.getTime() && now < addDays(start, 7).getTime();
  const todayIdx = days.findIndex(d => now >= d.getTime() && now < addDays(d, 1).getTime());
  const cfg = auto.data;
  // instant de la copie automatique vers cette semaine (pendant la semaine précédente)
  const autoAt = cfg && cfg.active && offset >= 1 ? (() => { const d = addDays(start, cfg.jour - 7 + (cfg.heure < DAY_START_H ? 1 : 0)); d.setHours(cfg.heure, 0, 0, 0); return d; })() : null;

  const toggleLine = id => {
    const h = hidden.includes(id) ? hidden.filter(x => x !== id) : [...hidden, id];
    if (h.length >= lines.length) return showToast("Au moins une ligne doit rester affichée.", true);
    setHidden(h); try { localStorage.setItem(SHOWN_KEY, JSON.stringify(h)); } catch (e) { /* stockage indisponible */ }
  };
  const showAll = () => { setHidden([]); try { localStorage.removeItem(SHOWN_KEY); } catch (e) { /* stockage indisponible */ } };

  // créneau vide touché : poste prérempli à l'heure touchée, jusqu'au poste suivant de la ligne (8 h au plus)
  const createAt = (day, line, frac) => {
    if (!edit) return;
    let s = new Date(day.getTime() + Math.floor(frac * 24) * 3600e3);
    if (s.getTime() <= now) s = new Date(Math.ceil(now / 3600e3) * 3600e3);
    const dayEnd = addDays(day, 1).getTime();
    const after = list.filter(x => x.line_id === line.id && T(x.ends_at) > s.getTime()).sort((a, b) => T(a.starts_at) - T(b.starts_at))[0];
    if (after && T(after.starts_at) <= s.getTime()) return showToast("Ce créneau est déjà occupé.", true);
    const e = Math.min(s.getTime() + 8 * 3600e3, dayEnd, after ? T(after.starts_at) : Infinity);
    if (e - s.getTime() < 15 * MIN) return showToast("Ce créneau est déjà commencé ou occupé.", true);
    setDlg({ type: "form", shift: null, preset: { line_ids: [line.id], day, starts_at: s, ends_at: new Date(e) } });
  };

  const selShift = sel && byId[sel.id];
  return html`<div class="view" onClick=${e => { if (sel && !e.target.closest(".pl-pop, .pl-bar")) setSel(null); }}>
    <${Band} over=${`${fWeek.format(start)} – ${fWeek.format(end)}`} title="Planning">
      <div class="seg" role="group" aria-label="Semaine">
        <button type="button" onClick=${() => { setOffset(offset - 1); setSel(null); }} aria-label="Semaine précédente">‹</button>
        <button type="button" class=${offset === 0 ? "on" : ""} onClick=${() => { setOffset(0); setSel(null); }}>Cette semaine</button>
        <button type="button" onClick=${() => { setOffset(offset + 1); setSel(null); }} aria-label="Semaine suivante">›</button>
      </div>
      ${edit && html`
        <button type="button" class="pl-ghost" disabled=${!list.length} onClick=${() => setDlg({ type: "copy" })}>⧉ Copier</button>
        <button type="button" class="pl-ghost" disabled=${!list.some(s => T(s.starts_at) > now)} onClick=${() => setDlg({ type: "clear" })}>🗑 Vider</button>
        <button type="button" class=${"pl-ghost" + (cfg && cfg.active ? " on" : "")} onClick=${() => setDlg({ type: "auto" })} title="Copie automatique d'une semaine vide">
          ↻ ${cfg ? (cfg.active ? `Auto ${DAYS[cfg.jour].slice(0, 3)}. ${cfg.heure} h` : "Auto off") : "Auto"}</button>
        <button type="button" class="pl-plus" aria-label="Créer un poste" title="Créer un poste"
          onClick=${() => setDlg({ type: "form", shift: null, preset: { day: days[Math.max(0, todayIdx)] } })}>+</button>`}
    </${Band}>
    <${Msg} error=${shifts.error} />
    <section class="tile pl-board" aria-label="Planning de la semaine">
      <div class="pl-filter" role="group" aria-label="Lignes affichées">
        ${lines.map(l => html`<button type="button" class=${"chip pl-sm" + (hidden.includes(l.id) ? " off" : " on")} aria-pressed=${!hidden.includes(l.id)} onClick=${() => toggleLine(l.id)}>${l.name}</button>`)}
        <button type="button" class="chip pl-sm" disabled=${!hidden.length} onClick=${showAll}>Toutes</button>
        <span class="spacer"></span>
        <span class="legend" style="font-size:12px"><span><i class="pl-st maintenance"></i>Maintenance</span><span><i class="pl-st arret"></i>Arrêt</span></span>
      </div>
      <div class="pl-cols pl-head"><span></span><span></span>
        <div class="pl-hours num">${[...Array(13)].map((_, i) => html`<span style=${{ left: (i / 12) * 100 + "%" }}>${(DAY_START_H + 2 * i) % 24} h</span>`)}</div>
      </div>
      <div class="pl-days">
        ${days.map((d, di) => {
          const ds = list.filter(s => T(s.starts_at) >= d.getTime() && T(s.starts_at) < addDays(d, 1).getTime());
          const today = di === todayIdx && nowIn;
          const rows = `repeat(${Math.max(1, vis.length)}, minmax(0, 1fr))`;
          return html`<div class=${"pl-day pl-cols" + (today ? " today" : "")}>
            <div class="pl-dname"><b>${fDay.format(d)}</b>${today && html`<small>Aujourd'hui</small>`}</div>
            <div class="pl-lnames" style=${{ gridTemplateRows: rows }}>${vis.map(l => html`<span>${l.name}</span>`)}</div>
            <div class="pl-tracks" style=${{ gridTemplateRows: rows }}>
              ${vis.map(l => html`<div class="pl-track" onClick=${e => { if (e.target !== e.currentTarget || sel) return; const r = e.currentTarget.getBoundingClientRect(); createAt(d, l, (e.clientX - r.left) / r.width); }}>
                ${ds.filter(s => s.line_id === l.id).map(s => html`<${Bar} s=${s} d=${d} colorOf=${colorOf} sel=${sel && sel.id === s.id} past=${T(s.starts_at) <= now}
                    onPick=${e => { e.stopPropagation(); setSel(sel && sel.id === s.id ? null : { id: s.id, rect: e.currentTarget.getBoundingClientRect() }); }} />`)}
              </div>`)}
              ${today && html`<div class="pl-now" style=${{ left: ((now - d.getTime()) / DAY_MS) * 100 + "%" }}></div>
                <span class="pl-nowlbl" style=${{ left: ((now - d.getTime()) / DAY_MS) * 100 + "%" }}>${hm(now)}</span>`}
            </div>
          </div>`;
        })}
      </div>
      ${!list.length && !shifts.loading && html`<div class="pl-empty"><div>
        <h3>Semaine vide</h3>
        ${autoAt && autoAt.getTime() > now && html`<p>↻ Copie auto ${fDayLong.format(autoAt)}, ${cfg.heure} h</p>`}
        ${edit && html`<div class="row" style="justify-content:center">
          <button type="button" class="primary" onClick=${async () => {
            try { const r = await api("/planning/copier-semaine", { method: "POST", body: { source: isoDate(addDays(start, -7)), destination: isoDate(start) } }); showToast(`${r.cree} postes copiés.`); reload(); }
            catch (e) { showToast(e.message, true); }
          }}>⧉ Copier la semaine précédente</button>
          <button type="button" onClick=${() => setDlg({ type: "form", shift: null, preset: { day: start } })}>+ Poste</button></div>`}
      </div></div>`}
    </section>
    ${selShift && html`<${ShiftMenu} s=${selShift} rect=${sel.rect} edit=${edit} now=${now} colorOf=${colorOf}
      onEdit=${() => { setDlg({ type: "form", shift: selShift }); setSel(null); }}
      onDup=${() => { setDlg({ type: "dup", shift: selShift }); setSel(null); }}
      onDelete=${async () => { try { await api(`/shifts/${selShift.id}`, { method: "DELETE" }); showToast("Poste supprimé."); setSel(null); reload(); } catch (e) { showToast(e.message, true); } }} />`}
    ${dlg && dlg.type === "form" && html`<${ShiftForm} shift=${dlg.shift} preset=${dlg.preset} lines=${lines} recipes=${recipeList} colorOf=${colorOf} days=${days} now=${now}
      onClose=${() => setDlg(null)} onDone=${t => { setDlg(null); showToast(t); reload(); }} />`}
    ${dlg && dlg.type === "dup" && html`<${DupDialog} s=${dlg.shift} lines=${lines} list=${list} start=${start} now=${now}
      onClose=${() => setDlg(null)} onDone=${t => { setDlg(null); showToast(t); reload(); }} />`}
    ${dlg && dlg.type === "copy" && html`<${CopyDialog} start=${start}
      onClose=${() => setDlg(null)} onDone=${(t, dst) => { setDlg(null); showToast(t); setOffset(offset + dst); }} />`}
    ${dlg && dlg.type === "clear" && html`<${ClearDialog} start=${start} lines=${lines} list=${list} now=${now}
      onClose=${() => setDlg(null)} onDone=${t => { setDlg(null); showToast(t); reload(); }} />`}
    ${dlg && dlg.type === "auto" && cfg && html`<${AutoDialog} cfg=${cfg} onClose=${() => setDlg(null)} onSaved=${() => auto.reload()} />`}
    ${toast}
  </div>`;
}

function Bar({ s, d, colorOf, sel, past, onPick }) {
  const a = T(s.starts_at), b = T(s.ends_at), dur = b - a;
  const left = ((a - d.getTime()) / DAY_MS) * 100, width = (dur / DAY_MS) * 100;
  const stops = s.stops || [];
  const full = stops.find(x => T(x.starts_at) <= a && T(x.ends_at) >= b);
  const [bg, fg] = s.recipe_id ? colorOf(s.recipe_id) : ["var(--soft)", "var(--text)"];
  const label = full ? (full.type === "maintenance" ? "Maintenance" : "Arrêt planifié") : s.recipe_code || "Production";
  const title = `${s.line} · ${hLabel(a)}–${hLabel(b)}${s.team ? " · équipe " + s.team : ""} · ${label}${s.target_t && !full ? " · " + Math.round(s.target_t) + " t" : ""}`
    + (stops.length && !full ? " · arrêts : " + stops.map(x => `${hLabel(x.starts_at)}–${hLabel(x.ends_at)}`).join(", ") : "");
  return html`<button type="button" class=${"pl-bar" + (full ? " pl-st " + full.type : "") + (past ? " past" : "") + (sel ? " sel" : "")}
      style=${{ left: `calc(${left}% + 1px)`, width: `calc(${width}% - 2px)`, ...(full ? {} : { background: bg, color: fg }) }}
      title=${title} aria-label=${title} aria-expanded=${sel} onClick=${onPick}>
    ${!full && stops.map(x => html`<span class=${"pl-st " + x.type} style=${{ left: ((T(x.starts_at) - a) / dur) * 100 + "%", width: ((T(x.ends_at) - T(x.starts_at)) / dur) * 100 + "%" }}></span>`)}
    ${s.team && html`<span class="tm">${s.team}</span>`}
    <span class="lb" style=${stops.length && !full ? { background: bg } : {}}>${label}</span>
    ${!full && s.target_t && dur >= 5 * 3600e3 ? html`<span class="t num" style=${{ background: bg }}>${Math.round(s.target_t)} t</span>` : ""}
  </button>`;
}

function ShiftMenu({ s, rect, edit, now, colorOf, onEdit, onDup, onDelete }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ left: -9999, top: 0 });
  const [confirm, setConfirm] = useState(false);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const w = el.offsetWidth, h = el.offsetHeight;
    let top = rect.bottom + 6; if (top + h > innerHeight - 8) top = rect.top - h - 6;
    setPos({ left: Math.max(8, Math.min(innerWidth - w - 8, rect.left + rect.width / 2 - w / 2)), top: Math.max(8, top) });
  }, [s.id, confirm]);
  const started = T(s.starts_at) <= now, ended = T(s.ends_at) <= now;
  const [bg, fg] = s.recipe_id ? colorOf(s.recipe_id) : ["var(--fill)", "var(--text)"];
  return html`<div class="pl-pop" ref=${ref} style=${pos} role="dialog" aria-label="Poste">
    <div><h4>${s.line} · ${hLabel(s.starts_at)} → ${hLabel(s.ends_at)}</h4>
      <div class="sub" style="text-transform:capitalize">${fDayLong.format(new Date(s.starts_at))}</div>
      <div class="row" style="gap:6px;margin-top:6px">
        <span class="badge" style=${{ background: bg, color: fg }}>${s.recipe_code || "Sans recette"}${s.target_t ? ` · ${Math.round(s.target_t)} t` : ""}</span>
        <span class="sub">${s.team ? "équipe " + s.team : "sans équipe"}</span></div>
      ${(s.stops || []).map(x => html`<div class="sub" style="margin-top:4px"><i class=${"pl-st pl-chip " + x.type}></i>${STOP_LABEL[x.type]} ${hLabel(x.starts_at)}–${hLabel(x.ends_at)}${x.reason ? " · " + x.reason : ""}</div>`)}
    </div>
    ${(ended || started) && html`<span class=${"badge " + (ended ? "idle" : "warn")}>${ended ? "Terminé" : "En cours"}</span>`}
    ${edit && (confirm
      ? html`<b>Supprimer ce poste ?</b><div class="row" style="gap:6px"><button type="button" style="flex:1" onClick=${() => setConfirm(false)}>Annuler</button>
          <button type="button" class="danger" style="flex:1" onClick=${onDelete}>Supprimer</button></div>`
      : html`<div class="pl-acts">
          <button type="button" onClick=${onEdit}>✎ Modifier</button>
          <button type="button" onClick=${onDup}>⧉ Dupliquer…</button>
          <button type="button" class="danger" disabled=${started} onClick=${() => setConfirm(true)}>🗑 Supprimer</button></div>`)}
  </div>`;
}

// ----------------------------------------------------------------------------- créer ou modifier un poste
function ShiftForm({ shift, preset = {}, lines, recipes, colorOf, days, now, onClose, onDone }) {
  const day0 = shift ? dayOf(shift.starts_at) : preset.day || days[0];
  // « + » : premier créneau à venir de la journée, 8 h au plus
  const s0 = shift ? new Date(shift.starts_at) : preset.starts_at || new Date(Math.max(day0.getTime(), Math.ceil(now / 3600e3) * 3600e3));
  const e0 = shift ? new Date(shift.ends_at) : preset.ends_at || new Date(Math.min(s0.getTime() + 8 * 3600e3, addDays(day0, 1).getTime()));
  const [f, setF] = useState(() => ({
    line_ids: shift ? [shift.line_id] : preset.line_ids || (lines[0] ? [lines[0].id] : []),
    day: day0,
    start: toTime(s0),
    end: toTime(e0),
    recipe_id: shift ? shift.recipe_id : recipes[0] ? recipes[0].id : null,
    target_t: shift ? shift.target_t ?? "" : "",
    team: shift ? shift.team : null,
    stops: shift ? (shift.stops || []).map(x => ({ type: x.type, start: toTime(x.starts_at), end: toTime(x.ends_at), reason: x.reason || "" })) : [],
  }));
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const started = shift && T(shift.starts_at) <= now;
  const set = p => { setF({ ...f, ...p }); setErr(null); };
  const sAt = atTime(f.day, f.start), eAt = atTime(f.day, f.end, true);
  const prodH = sAt && eAt && eAt > sAt ? (eAt - sAt - f.stops.reduce((a, x) => {
    const a1 = atTime(f.day, x.start), b1 = atTime(f.day, x.end, true); return a + (a1 && b1 && b1 > a1 ? b1 - a1 : 0);
  }, 0)) / 3600e3 : null;
  const setStop = (i, p) => set({ stops: f.stops.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  const save = async () => {
    if (!sAt || !eAt || eAt <= sAt) return setErr("La fin du poste doit suivre son début.");
    if (!f.line_ids.length) return setErr("Choisissez au moins une ligne.");
    const body = {
      starts_at: sAt.toISOString(), ends_at: eAt.toISOString(), team: f.team, recipe_id: f.recipe_id,
      target_t: f.target_t === "" || f.target_t === null ? null : +f.target_t, comment: shift ? shift.comment : null,
      stops: f.stops.map(x => ({ type: x.type, starts_at: atTime(f.day, x.start).toISOString(), ends_at: atTime(f.day, x.end, true).toISOString(), reason: x.reason || null })),
    };
    setBusy(true);
    try {
      if (shift) { await api(`/shifts/${shift.id}`, { method: "PUT", body: { ...body, line_id: f.line_ids[0] } }); onDone("Poste enregistré."); }
      else { const r = await api("/shifts", { method: "POST", body: { ...body, line_ids: f.line_ids } }); onDone(r.ids.length > 1 ? `${r.ids.length} postes créés.` : "Poste créé."); }
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const lock = !!started;
  return html`<${Dialog} title=${shift ? "Modifier le poste" : "Nouveau poste"} onClose=${onClose}>
    <div class="pl-form">
      <div class="f"><span class="lbl">${shift ? "Ligne" : "Lignes"}</span><div class="chips">
        ${lines.map(l => html`<button type="button" class=${"chip" + (f.line_ids.includes(l.id) ? " on" : "")} disabled=${lock}
          onClick=${() => set({ line_ids: shift ? [l.id] : f.line_ids.includes(l.id) ? (f.line_ids.length > 1 ? f.line_ids.filter(x => x !== l.id) : f.line_ids) : [...f.line_ids, l.id] })}>${l.name}</button>`)}
        ${!shift && html`<button type="button" class="chip" onClick=${() => set({ line_ids: lines.map(l => l.id) })}>Toutes</button>`}</div></div>
      <div class="f"><span class="lbl">Jour</span><div class="chips">
        ${days.map(d => html`<button type="button" class=${"chip" + (d.getTime() === f.day.getTime() ? " on" : "")} style="text-transform:capitalize"
          disabled=${lock || addDays(d, 1).getTime() <= now} onClick=${() => set({ day: d })}>${fDay.format(d)}</button>`)}</div></div>
      <div class="pl-2">
        <label class="f">Début<input type="time" step="900" value=${f.start} disabled=${lock} onInput=${e => set({ start: e.target.value })} /></label>
        <label class="f">Fin<input type="time" step="900" value=${f.end} disabled=${lock} onInput=${e => set({ end: e.target.value })} /></label></div>
      <div class="pl-enc">
        <div class="f"><span class="lbl">Recette</span><div class="chips">
          ${recipes.map(r => { const [bg, fg] = colorOf(r.id); return html`<button type="button" class=${"chip" + (f.recipe_id === r.id ? " pl-on" : "")} disabled=${lock} title=${r.label}
            style=${{ background: bg, color: fg, borderColor: bg }} onClick=${() => set({ recipe_id: r.id })}>${r.code}</button>`; })}
          ${!recipes.length && html`<span class="sub">Aucune recette validée.</span>`}</div></div>
        <label class="f" style="max-width:160px">Objectif (t)<input type="number" min="0" step="10" value=${f.target_t} onInput=${e => set({ target_t: e.target.value })} /></label>
        <div class="pl-stops">
          <span class="lbl">Arrêts planifiés${prodH !== null && f.stops.length ? html` <span class="sub">· production ${Math.round(prodH * 10) / 10} h</span>` : ""}</span>
          ${f.stops.map((x, i) => html`<div class="pl-srow">
            <select value=${x.type} disabled=${lock} onChange=${e => setStop(i, { type: e.target.value })} aria-label="Type d'arrêt"><option value="arret">Arrêt</option><option value="maintenance">Maintenance</option></select>
            <input type="time" step="900" value=${x.start} disabled=${lock} aria-label="Début de l'arrêt" onInput=${e => setStop(i, { start: e.target.value })} />
            <input type="time" step="900" value=${x.end} disabled=${lock} aria-label="Fin de l'arrêt" onInput=${e => setStop(i, { end: e.target.value })} />
            <input type="text" value=${x.reason} disabled=${lock} placeholder="Motif" aria-label="Motif de l'arrêt" onInput=${e => setStop(i, { reason: e.target.value })} />
            <button type="button" class="xb" disabled=${lock} aria-label="Retirer l'arrêt" onClick=${() => set({ stops: f.stops.filter((_, j) => j !== i) })}>✕</button></div>`)}
          ${!lock && html`<div class="chips">
            <button type="button" class="chip" onClick=${() => {
              const last = f.stops.length ? f.stops[f.stops.length - 1].end : null;
              const a = last || (sAt && eAt ? toTime(new Date(sAt.getTime() + Math.floor((eAt - sAt) / 7200e3) * 3600e3)) : f.start);
              const b = atTime(f.day, a) ? toTime(new Date(Math.min(atTime(f.day, a).getTime() + 3600e3, eAt ? eAt.getTime() : Infinity))) : a;
              set({ stops: [...f.stops, { type: "arret", start: a, end: b, reason: "" }] });
            }}>+ Arrêt</button>
            <button type="button" class="chip" onClick=${() => set({ stops: [{ type: "arret", start: f.start, end: f.end, reason: "" }] })}>Tout le poste</button></div>`}
        </div>
      </div>
      <div class="f"><span class="lbl">Équipe</span><div class="chips">
        ${TEAMS.map(t => html`<button type="button" class=${"chip" + (f.team === t ? " on" : "")} onClick=${() => set({ team: t })}>${t}</button>`)}
        <button type="button" class=${"chip" + (!f.team ? " on" : "")} onClick=${() => set({ team: null })}>Aucune</button></div></div>
      <${Msg} error=${err} />
      <div class="row"><span class="spacer"></span><button type="button" onClick=${onClose}>Annuler</button>
        <button type="button" class="primary" disabled=${busy} onClick=${save}>${shift ? "Enregistrer" : f.line_ids.length > 1 ? `Créer ${f.line_ids.length} postes` : "Créer le poste"}</button></div>
    </div>
  </${Dialog}>`;
}

// ----------------------------------------------------------------------------- dupliquer un poste
function DupDialog({ s, lines, list, start, now, onClose, onDone }) {
  const [mode, setMode] = useState("jours");
  const [sel, setSel] = useState([]);                 // jours choisis (index 0..13 depuis le lundi de la semaine du poste)
  const [lns, setLns] = useState([s.line_id]);
  const w0 = dayOf(s.starts_at); w0.setDate(w0.getDate() - ((w0.getDay() + 6) % 7));
  const srcDay = Math.round((dayOf(s.starts_at) - w0) / DAY_MS);
  const [at, setAt] = useState({ day: Math.min(13, srcDay + 7), time: toTime(s.starts_at), line: s.line_id });
  const [replace, setReplace] = useState(false);
  const [err, setErr] = useState(null);
  const off = T(s.starts_at) - dayOf(s.starts_at).getTime(), dur = T(s.ends_at) - T(s.starts_at);
  const startOn = i => new Date(addDays(w0, i).getTime() + off);
  const busyOn = (i, l) => { const a = startOn(i).getTime(); return list.some(x => x.line_id === l && x.id !== s.id && T(x.starts_at) < a + dur && T(x.ends_at) > a); };
  const cibles = mode === "jours"
    ? sel.flatMap(i => lns.map(l => ({ line_id: l, starts_at: startOn(i).toISOString() })))
    : (() => { const d = atTime(addDays(w0, at.day), at.time); return d ? [{ line_id: at.line, starts_at: d.toISOString() }] : []; })();
  const go = async () => {
    try {
      const r = await api(`/shifts/${s.id}/dupliquer`, { method: "POST", body: { cibles, remplacer: replace } });
      const skipped = r.occupe + r.passe + r.hors_journee;
      onDone(`${r.cree} poste(s) créé(s)${skipped ? ` · ${skipped} ignoré(s)` : ""}.`);
    } catch (e) { setErr(e.message); }
  };
  return html`<${Dialog} title="Dupliquer le poste" onClose=${onClose}>
    <div class="pl-form">
      <p class="sub" style="margin:0"><b>${s.line} · ${s.recipe_code || "Sans recette"}</b> · ${hLabel(s.starts_at)}–${hLabel(s.ends_at)}</p>
      <div class="seg light"><button type="button" class=${mode === "jours" ? "on" : ""} onClick=${() => setMode("jours")}>Plusieurs jours</button>
        <button type="button" class=${mode === "date" ? "on" : ""} onClick=${() => setMode("date")}>Date et heure</button></div>
      ${mode === "jours" ? html`
        <div class="pl-cal"><span></span>${["lun", "mar", "mer", "jeu", "ven", "sam", "dim"].map(x => html`<span class="dh">${x}</span>`)}
          ${[0, 1].map(w => html`<span class="wk">${fWeek.format(addDays(w0, 7 * w))}</span>${[...Array(7)].map((_, k) => {
            const i = 7 * w + k, src = i === srcDay && lns.length === 1 && lns[0] === s.line_id, past = startOn(i).getTime() <= now;
            const busy = lns.some(l => busyOn(i, l));
            return html`<button type="button" class=${(sel.includes(i) ? "on " : "") + (src ? "src " : "") + (busy ? "busy" : "")} disabled=${src || past}
              title=${src ? "Poste d'origine" : busy ? "Créneau occupé" : ""} onClick=${() => setSel(sel.includes(i) ? sel.filter(x => x !== i) : [...sel, i])}>${addDays(w0, i).getDate()}</button>`;
          })}`)}</div>
        <div class="f"><span class="lbl">Lignes</span><div class="chips">${lines.map(l => html`<button type="button" class=${"chip" + (lns.includes(l.id) ? " on" : "")}
          onClick=${() => setLns(lns.includes(l.id) ? (lns.length > 1 ? lns.filter(x => x !== l.id) : lns) : [...lns, l.id])}>${l.name}</button>`)}</div></div>`
      : html`<div class="pl-2">
          <label class="f">Date<select value=${at.day} onChange=${e => setAt({ ...at, day: +e.target.value })}>
            ${[...Array(14)].map((_, i) => html`<option value=${i}>${fDayLong.format(addDays(w0, i))}</option>`)}</select></label>
          <label class="f">Début<input type="time" step="900" value=${at.time} onInput=${e => setAt({ ...at, time: e.target.value })} /></label></div>
        <div class="f"><span class="lbl">Ligne</span><div class="chips">${lines.map(l => html`<button type="button" class=${"chip" + (at.line === l.id ? " on" : "")} onClick=${() => setAt({ ...at, line: l.id })}>${l.name}</button>`)}</div></div>`}
      <label class="pl-opt"><input type="checkbox" checked=${replace} onChange=${e => setReplace(e.target.checked)} /> Remplacer les postes à venir déjà placés</label>
      <${Msg} error=${err} />
      <div class="row"><span class="sub">${cibles.length} poste(s)</span><span class="spacer"></span><button type="button" onClick=${onClose}>Annuler</button>
        <button type="button" class="primary" disabled=${!cibles.length} onClick=${go}>Dupliquer</button></div>
    </div>
  </${Dialog}>`;
}

// ----------------------------------------------------------------------------- copier, vider une semaine, copie automatique
function CopyDialog({ start, onClose, onDone }) {
  const [dst, setDst] = useState(1);
  const [o, setO] = useState({ equipes: true, objectifs: true, arrets: true, remplacer: false });
  const target = addDays(start, 7 * dst);
  const existing = useApi(`/shifts?depuis=${encodeURIComponent(target.toISOString())}&jours=7`, [dst]);
  const n = (existing.data || []).length;
  const [err, setErr] = useState(null);
  const go = async () => {
    try {
      const r = await api("/planning/copier-semaine", { method: "POST", body: { source: isoDate(start), destination: isoDate(target), ...o, remplacer: n > 0 && o.remplacer } });
      onDone(`${r.cree} postes copiés${r.occupe ? ` · ${r.occupe} ignoré(s)` : ""}.`, dst);
    } catch (e) { setErr(e.message); }
  };
  const box = (k, l) => html`<label class="pl-opt"><input type="checkbox" checked=${o[k]} onChange=${e => setO({ ...o, [k]: e.target.checked })} /> ${l}</label>`;
  return html`<${Dialog} title=${`Copier la semaine du ${fWeek.format(start)}`} onClose=${onClose}>
    <div class="pl-form">
      <label class="f">Vers<select value=${dst} onChange=${e => setDst(+e.target.value)}>
        ${[1, 2, 3, 4].map(k => html`<option value=${k}>Semaine du ${fWeek.format(addDays(start, 7 * k))}</option>`)}</select></label>
      <div class="f">${box("equipes", "Équipes")}${box("objectifs", "Recettes et objectifs")}${box("arrets", "Arrêts planifiés")}</div>
      ${n > 0 && html`<div class="f"><span class="lbl">${n} postes déjà planifiés</span>
        <label class="pl-opt"><input type="radio" name="pl-mode" checked=${!o.remplacer} onChange=${() => setO({ ...o, remplacer: false })} /> Compléter</label>
        <label class="pl-opt"><input type="radio" name="pl-mode" checked=${o.remplacer} onChange=${() => setO({ ...o, remplacer: true })} /> Remplacer</label></div>`}
      <${Msg} error=${err} />
      <div class="row"><span class="spacer"></span><button type="button" onClick=${onClose}>Annuler</button><button type="button" class="primary" onClick=${go}>Copier</button></div>
    </div>
  </${Dialog}>`;
}

function ClearDialog({ start, lines, list, now, onClose, onDone }) {
  const [lns, setLns] = useState(lines.map(l => l.id));
  const [err, setErr] = useState(null);
  const n = list.filter(s => T(s.starts_at) > now && lns.includes(s.line_id)).length;
  const go = async () => {
    try { const r = await api("/planning/vider-semaine", { method: "POST", body: { semaine: isoDate(start), line_ids: lns } }); onDone(`${r.supprimes} poste(s) supprimé(s).`); }
    catch (e) { setErr(e.message); }
  };
  return html`<${Dialog} title=${`Vider la semaine du ${fWeek.format(start)}`} onClose=${onClose}>
    <div class="pl-form">
      <div class="f"><span class="lbl">Lignes</span><div class="chips">${lines.map(l => html`<button type="button" class=${"chip" + (lns.includes(l.id) ? " on" : "")}
        onClick=${() => setLns(lns.includes(l.id) ? lns.filter(x => x !== l.id) : [...lns, l.id])}>${l.name}</button>`)}</div></div>
      <div class="warn" style="padding:8px 12px;border-radius:10px">${n} poste(s) supprimé(s), sans retour possible.</div>
      <${Msg} error=${err} />
      <div class="row"><span class="spacer"></span><button type="button" onClick=${onClose}>Annuler</button>
        <button type="button" class="danger" disabled=${!n} onClick=${go}>Supprimer ${n} poste(s)</button></div>
    </div>
  </${Dialog}>`;
}

function AutoDialog({ cfg, onClose, onSaved }) {
  const [c, setC] = useState(cfg);
  const [err, setErr] = useState(null);
  const save = async p => {
    const v = { ...c, ...p }; setC(v);
    try { await api("/planning/copie-auto", { method: "PUT", body: v }); setErr(null); onSaved(); } catch (e) { setErr(e.message); }
  };
  return html`<${Dialog} title="Copie automatique" onClose=${onClose}>
    <div class="pl-form">
      <label class="pl-opt"><input type="checkbox" checked=${c.active} onChange=${e => save({ active: e.target.checked })} /> <b>${c.active ? "Activée" : "Désactivée"}</b></label>
      <p class="sub" style="margin:0">Une semaine suivante vide reçoit une copie de la semaine en cours.</p>
      <div class="pl-2">
        <label class="f">Jour<select value=${c.jour} onChange=${e => save({ jour: +e.target.value })}>${DAYS.map((d, i) => html`<option value=${i}>${d}</option>`)}</select></label>
        <label class="f">Heure<select value=${c.heure} onChange=${e => save({ heure: +e.target.value })}>${[...Array(24)].map((_, h) => html`<option value=${h}>${h} h</option>`)}</select></label></div>
      <${Msg} error=${err} />
      <div class="row"><span class="spacer"></span><button type="button" onClick=${onClose}>Fermer</button></div>
    </div>
  </${Dialog}>`;
}
