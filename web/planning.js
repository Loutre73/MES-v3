// Planning de la semaine : axe horizontal 24 h (6 h → 6 h), axe vertical les jours, toutes les lignes empilées dans chaque jour.
// Chaque barre est un poste d'équipe ; glisser une barre sur une autre échange leur contenu, glisser un élément « À planifier »
// sur un poste le lui affecte. Sans souris : cliquer un élément puis cliquer le poste visé. Chaque action est enregistrée aussitôt.
import { html, useState } from "./vendor/preact-htm.module.js";
import { api, useApi, Msg, Band, Icon, ICON, useToast, can, hm, num, fmtDayShort, fmtDay } from "./lib.js";

const DAY_MS = 86400e3, HOUR = 3600e3, DAY_START_H = 6;
const T = v => new Date(v).getTime();
const TEAMS = ["A", "B", "C", "D"];
// couleurs des recettes, dans l'ordre des codes (texte lisible sur chaque fond)
const RECIPE_COLORS = [["#2F5D8A", "#FFFFFF"], ["#7FD3E3", "#062C33"], ["#82A1CD", "#000B44"], ["#4A6A8C", "#FFFFFF"], ["#B7C9E2", "#000B44"], ["#1B2F41", "#FFFFFF"]];
const KIND_STYLE = {
  maintenance: { background: "#FFD60A", color: "#000B44" },
  arret: { background: "repeating-linear-gradient(135deg, var(--muted) 0 6px, var(--fill) 6px 12px)", color: "var(--text)" },
};

function weekStart(offset) {
  const d = new Date();
  if (d.getHours() < DAY_START_H) d.setDate(d.getDate() - 1);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + 7 * offset);
  d.setHours(DAY_START_H, 0, 0, 0);
  return d;
}

export function Planning({ user }) {
  const [offset, setOffset] = useState(0);
  const start = weekStart(offset);
  const shifts = useApi(`/shifts?depuis=${encodeURIComponent(start.toISOString())}&jours=7`, [offset], 60000);
  const recipes = useApi("/recettes?statut=validee");
  const [sel, setSel] = useState(null);       // poste sélectionné (id)
  const [armed, setArmed] = useState(null);   // élément prêt à être posé par clic : {type, ...}
  const [dragging, setDragging] = useState(null);
  const [toast, showToast] = useToast();
  const edit = can(user, "chef_equipe");
  const now = Date.now();

  const list = shifts.data || [];
  const lines = [...new Map(list.map(s => [s.line_id, s.line])).entries()].sort((a, b) => a[1].localeCompare(b[1], "fr", { numeric: true }));
  const recipeList = recipes.data || [];
  const colorOf = rid => { const i = recipeList.findIndex(r => r.id === rid); return RECIPE_COLORS[(i < 0 ? 0 : i) % RECIPE_COLORS.length]; };
  const byId = Object.fromEntries(list.map(s => [s.id, s]));
  const selShift = sel && byId[sel];
  const locked = s => T(s.starts_at) <= now;
  const ended = s => T(s.ends_at) <= now;

  const save = async (fn, ok) => {
    try { await fn(); if (ok) showToast(ok); shifts.reload(); }
    catch (e) { showToast(e.message, true); }
  };
  const patch = (s, body, ok) => save(() => api(`/shifts/${s.id}`, { method: "PATCH", body }), ok);
  const contentOf = item => item.type === "recipe" ? { kind: "production", recipe_id: item.id }
    : item.type === "kind" ? { kind: item.kind, recipe_id: null } : { team: item.team };

  // pose d'un élément (barre d'un autre poste, recette, activité, équipe) sur un poste
  const dropOn = target => {
    const item = dragging || armed;
    setDragging(null); setArmed(null);
    if (!item || !edit) return;
    if (item.type === "shift") {
      if (item.id === target.id) return;
      const src = byId[item.id];
      if (locked(target) || locked(src)) return showToast("Un poste commencé ou passé ne peut plus être déplacé.", true);
      return save(() => api("/shifts/echanger", { method: "POST", body: { a: item.id, b: target.id } }), "Postes échangés.").then(() => setSel(target.id));
    }
    if (item.type !== "team" && ended(target)) return showToast("Ce poste est terminé : son contenu ne peut plus être modifié.", true);
    setSel(target.id);
    patch(target, contentOf(item), "Poste mis à jour.");
  };

  const days = [...Array(7)].map((_, i) => new Date(start.getTime() + i * DAY_MS));
  const hours = [...Array(13)].map((_, i) => (DAY_START_H + 2 * i) % 24);
  const nowIn = now >= start.getTime() && now < start.getTime() + 7 * DAY_MS;
  const nowFrac = ((now - start.getTime()) % DAY_MS) / DAY_MS;
  const todayIdx = Math.floor((now - start.getTime()) / DAY_MS);

  const stats = { t: 0, prod: 0, maint: 0, arret: 0, noTeam: 0, noRecipe: 0 };
  list.forEach(s => {
    if (s.kind === "maintenance") stats.maint++; else if (s.kind === "arret") stats.arret++;
    else { stats.prod++; stats.t += s.target_t || 0; if (!s.recipe_id && !locked(s)) stats.noRecipe++; }
    if (!s.team && !locked(s) && s.kind === "production") stats.noTeam++;
  });

  return html`<div class="view">
    <${Band} over=${`Semaine du ${fmtDay.format(start)} · ${edit ? "chaque modification est enregistrée aussitôt" : "lecture seule"}`} title="Planning de la" accent="semaine"
      back=${html`<a class="backlink" href="#/" aria-label="Retour à la synthèse"><${Icon} d=${ICON.back} /></a>`}>
      <div class="seg" role="group" aria-label="Semaine">
        <button type="button" onClick=${() => setOffset(offset - 1)} aria-label="Semaine précédente"><${Icon} d=${ICON.back} size="14" /></button>
        <button type="button" class=${offset === 0 ? "on" : ""} onClick=${() => setOffset(0)}>Cette semaine</button>
        <button type="button" onClick=${() => setOffset(offset + 1)} aria-label="Semaine suivante"><${Icon} d=${ICON.next} size="14" /></button>
      </div>
    </${Band}>
    <${Msg} error=${shifts.error} />
    <div style="flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:12px">
      <article class="tile" style="padding:10px 12px;gap:4px">
        <div style="display:grid;grid-template-columns:96px 44px minmax(0,1fr) 84px;gap:0 6px;font-size:11px;color:var(--text2);font-weight:600;flex:none">
          <span>Jour</span><span>Ligne</span>
          <div style="position:relative;height:14px">${hours.map((h, i) => html`<span style=${{ position: "absolute", left: (i / 12) * 100 + "%", transform: i === 0 ? "none" : i === 12 ? "translateX(-100%)" : "translateX(-50%)", whiteSpace: "nowrap" }} class="num">${h} h</span>`)}</div>
          <span style="text-align:right">Objectif</span>
        </div>
        <div style="flex:1;min-height:0;display:grid;grid-template-rows:repeat(7,minmax(0,1fr));gap:3px">
          ${days.map((d, di) => {
            const dayShifts = list.filter(s => T(s.starts_at) >= d.getTime() && T(s.starts_at) < d.getTime() + DAY_MS);
            const target = dayShifts.filter(s => s.kind === "production").reduce((a, s) => a + (s.target_t || 0), 0);
            return html`<div style=${{ display: "grid", gridTemplateColumns: "96px 44px minmax(0,1fr) 84px", gap: "0 6px", borderTop: "1px solid var(--line)", paddingTop: "2px", minHeight: 0, background: di === todayIdx && nowIn ? "var(--fill2)" : "transparent", borderRadius: "6px" }}>
              <div style="display:flex;flex-direction:column;justify-content:center;min-width:0"><b style="font-size:13px;text-transform:capitalize">${fmtDayShort.format(d)}</b>
                ${di === todayIdx && nowIn ? html`<span style="font-size:11px;color:var(--green-fg);font-weight:700">Aujourd'hui</span>` : ""}</div>
              <div style=${{ display: "grid", gridTemplateRows: `repeat(${Math.max(1, lines.length)}, minmax(0,1fr))`, fontSize: "11px", color: "var(--text2)", fontWeight: 600, minHeight: 0 }}>
                ${lines.map(([, name]) => html`<span style="display:flex;align-items:center;overflow:hidden;white-space:nowrap">${name}</span>`)}</div>
              <div style=${{ position: "relative", display: "grid", gridTemplateRows: `repeat(${Math.max(1, lines.length)}, minmax(0,1fr))`, gap: "2px", minHeight: 0 }}>
                ${lines.map(([lid]) => html`<div style="position:relative;min-height:0;background:var(--fill);border-radius:5px">
                  ${dayShifts.filter(s => s.line_id === lid).map(s => html`<${Bar} s=${s} d=${d} colorOf=${colorOf} sel=${sel === s.id} locked=${locked(s)} edit=${edit}
                      target=${!!(armed || dragging)} onPick=${() => (armed ? dropOn(s) : setSel(s.id))}
                      onDragStart=${() => setDragging({ type: "shift", id: s.id })} onDragEnd=${() => setDragging(null)} onDrop=${() => dropOn(s)} />`)}
                </div>`)}
                ${di === todayIdx && nowIn && html`<div aria-hidden="true" style=${{ position: "absolute", top: "-2px", bottom: 0, left: nowFrac * 100 + "%", width: "2px", background: "var(--red)", zIndex: 2, pointerEvents: "none" }}></div>
                  <span style=${{ position: "absolute", top: "-12px", left: nowFrac * 100 + "%", transform: "translateX(-50%)", fontSize: "10px", fontWeight: 800, color: "#fff", background: "var(--red)", padding: "0 5px", borderRadius: "4px", zIndex: 3 }}>${hm(now)}</span>`}
              </div>
              <div style="display:flex;align-items:center;justify-content:flex-end"><b class="num" style="font-size:13px">${num(target, 0)} t</b></div>
            </div>`;
          })}
        </div>
        ${!list.length && !shifts.loading && html`<p class="empty">Aucun poste généré pour cette semaine. Les postes sont créés automatiquement ${"jusqu'à"} 7 jours à l'avance.</p>`}
      </article>

      <aside style="display:flex;flex-direction:column;gap:12px;min-height:0">
        <article class="tile" style="flex:none">
          <div class="th"><h2>À planifier</h2></div>
          <span class="sub">${edit ? "Glissez sur un poste, ou cliquez puis cliquez le poste." : "Réservé aux chefs d'équipe."}</span>
          <div class="chips">
            ${recipeList.map(r => { const [bg, fg] = colorOf(r.id); return html`<${Item} item=${{ type: "recipe", id: r.id }} armed=${armed} setArmed=${setArmed} setDragging=${setDragging} edit=${edit}
              style=${{ background: bg, color: fg }} title=${r.label}>${r.code}</${Item}>`; })}
            <${Item} item=${{ type: "kind", kind: "maintenance" }} armed=${armed} setArmed=${setArmed} setDragging=${setDragging} edit=${edit} style=${KIND_STYLE.maintenance}>Maintenance</${Item}>
            <${Item} item=${{ type: "kind", kind: "arret" }} armed=${armed} setArmed=${setArmed} setDragging=${setDragging} edit=${edit} style=${KIND_STYLE.arret}>Arrêt planifié</${Item}>
          </div>
          <div class="chips">${TEAMS.map(t => html`<${Item} item=${{ type: "team", team: t }} armed=${armed} setArmed=${setArmed} setDragging=${setDragging} edit=${edit}
            style=${{ background: "var(--card)", color: "var(--text)", border: "1px solid var(--line)" }}>Équipe ${t}</${Item}>`)}</div>
          ${!recipeList.length && html`<span class="sub">Aucune recette validée : créez-en dans Recettes.</span>`}
          ${armed && html`<div class="row" style="gap:6px"><span class="sub" style="flex:1">Cliquez le poste visé.</span><button type="button" onClick=${() => setArmed(null)}>Annuler</button></div>`}
        </article>

        <article class="tile" style="flex:none">
          <div class="th"><h2>Poste sélectionné</h2></div>
          ${selShift ? html`<${SelPanel} s=${selShift} edit=${edit} locked=${locked(selShift)} ended=${ended(selShift)} recipes=${recipeList} colorOf=${colorOf}
              onPatch=${(body, ok) => patch(selShift, body, ok)} onSwap=${() => setArmed({ type: "shift", id: selShift.id })} />`
            : html`<span class="sub">Cliquez une barre du planning.</span>`}
        </article>

        <article class="tile" style="flex:1">
          <div class="th"><h2>Semaine</h2></div>
          <div class="lst">
            <div><span class="t">Objectif</span><span class="spacer"></span><b class="num">${num(stats.t, 0)} t</b></div>
            <div><span class="t">Postes en production</span><span class="spacer"></span><b class="num">${stats.prod}</b></div>
            <div><span class="t">Maintenance · arrêts</span><span class="spacer"></span><b class="num">${stats.maint} · ${stats.arret}</b></div>
            <div><span class="t">À venir sans équipe</span><span class="spacer"></span><b class="num" style=${{ color: stats.noTeam ? "var(--orange-fg)" : "inherit" }}>${stats.noTeam}</b></div>
            <div><span class="t">À venir sans recette</span><span class="spacer"></span><b class="num" style=${{ color: stats.noRecipe ? "var(--orange-fg)" : "inherit" }}>${stats.noRecipe}</b></div>
          </div>
          <div class="legend" style="font-size:12px;margin-top:auto"><span><i style="background:#FFD60A"></i>Maintenance</span>
            <span><i style="background:repeating-linear-gradient(135deg,var(--muted) 0 3px,var(--fill) 3px 6px)"></i>Arrêt planifié</span>
            <span><i style="background:var(--red)"></i>Maintenant</span><span>Barres pâles : postes passés</span></div>
        </article>
      </aside>
    </div>
    ${toast}
  </div>`;
}

function Bar({ s, d, colorOf, sel, locked, edit, target, onPick, onDragStart, onDragEnd, onDrop }) {
  const left = ((T(s.starts_at) - d.getTime()) / DAY_MS) * 100, width = ((T(s.ends_at) - T(s.starts_at)) / DAY_MS) * 100;
  const empty = s.kind === "production" && !s.recipe_id && !s.team;
  const [bg, fg] = s.recipe_id ? colorOf(s.recipe_id) : ["var(--soft)", "var(--text)"];
  const style = KIND_STYLE[s.kind] || { background: empty ? "transparent" : bg, color: fg };
  const label = s.kind === "maintenance" ? "Maintenance" : s.kind === "arret" ? "Arrêt planifié" : s.recipe_code || (empty ? "" : "Production");
  const title = `${s.line} · ${s.label || "poste"} ${hm(s.starts_at)}–${hm(s.ends_at)}${s.team ? " · équipe " + s.team : ""}${label ? " · " + label : ""}${s.target_t ? " · " + num(s.target_t, 0) + " t" : ""}`;
  const drag = edit && !locked;
  return html`<div style=${{ position: "absolute", top: "1px", bottom: "1px", left: `calc(${left}% + 1px)`, width: `calc(${width}% - 2px)`, display: "flex" }}
      onDragOver=${e => { if (edit) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; } }} onDrop=${e => { e.preventDefault(); onDrop(); }}>
    <button type="button" draggable=${drag} title=${title} aria-label=${title} aria-pressed=${sel}
      onDragStart=${e => { e.dataTransfer.setData("text/plain", String(s.id)); e.dataTransfer.effectAllowed = "move"; onDragStart(); }} onDragEnd=${onDragEnd}
      onClick=${onPick}
      style=${{ font: "inherit", margin: 0, textAlign: "left", boxSizing: "border-box", flex: 1, display: "flex", alignItems: "center", gap: "5px", padding: "0 6px", borderRadius: "5px", minWidth: 0,
        fontSize: "11px", lineHeight: 1, cursor: drag ? "grab" : "pointer", ...style,
        border: empty ? "1px dashed var(--muted)" : "0", opacity: locked ? 0.55 : 1,
        boxShadow: sel ? "0 0 0 2px var(--text)" : target && !locked ? "0 0 0 1px var(--green) inset" : "none" }}>
      ${s.team && html`<span style="flex:none;font-weight:800;font-size:10px;padding:1px 4px;border-radius:4px;background:rgba(255,255,255,.85);color:#000B44">${s.team}</span>`}
      <span style="font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${label}</span>
      ${s.kind === "production" && s.target_t ? html`<span class="num" style="margin-left:auto;white-space:nowrap;opacity:.85">${num(s.target_t, 0)} t</span>` : ""}
    </button>
  </div>`;
}

function Item({ item, armed, setArmed, setDragging, edit, style, title, children }) {
  const on = armed && JSON.stringify(armed) === JSON.stringify(item);
  return html`<button type="button" class="chip" draggable=${edit} disabled=${!edit} title=${title} aria-pressed=${on}
    onDragStart=${e => { e.dataTransfer.setData("text/plain", "x"); e.dataTransfer.effectAllowed = "copyMove"; setDragging(item); }} onDragEnd=${() => setDragging(null)}
    onClick=${() => setArmed(on ? null : item)}
    style=${{ ...style, padding: "5px 10px", fontSize: "12px", fontWeight: 700, cursor: edit ? "grab" : "default", boxShadow: on ? "0 0 0 2px var(--text)" : "none" }}>${children}</button>`;
}

function SelPanel({ s, edit, locked, ended, recipes, colorOf, onPatch, onSwap }) {
  const [target, setTarget] = useState(null);
  const ro = !edit;
  return html`<div style="display:flex;flex-direction:column;gap:8px" key=${s.id}>
    <div><b>${s.line} · ${s.label || "Poste"}</b><div class="sub" style="text-transform:capitalize">${fmtDay.format(new Date(s.starts_at))}, ${hm(s.starts_at)}–${hm(s.ends_at)}${ended ? " · terminé" : locked ? " · en cours" : ""}</div></div>
    <span class="sub" style="font-weight:600">Équipe</span>
    <div class="chips">${TEAMS.map(t => html`<button type="button" class=${"chip" + (s.team === t ? " on" : "")} disabled=${ro} onClick=${() => onPatch({ team: s.team === t ? null : t }, `Équipe ${t} ${s.team === t ? "retirée" : "affectée"}.`)}>${t}</button>`)}</div>
    <span class="sub" style="font-weight:600">Contenu</span>
    <div class="chips">
      ${recipes.map(r => { const [bg, fg] = colorOf(r.id); const on = s.kind === "production" && s.recipe_id === r.id;
        return html`<button type="button" class="chip" disabled=${ro || locked} title=${r.label} onClick=${() => onPatch({ kind: "production", recipe_id: r.id }, `Recette ${r.code} planifiée.`)}
          style=${{ background: bg, color: fg, borderColor: bg, boxShadow: on ? "0 0 0 2px var(--text)" : "none", padding: "4px 9px", fontSize: "12px" }}>${r.code}</button>`; })}
      <button type="button" class="chip" disabled=${ro || ended} onClick=${() => onPatch({ kind: "maintenance", recipe_id: null }, "Maintenance planifiée.")}
        style=${{ ...KIND_STYLE.maintenance, boxShadow: s.kind === "maintenance" ? "0 0 0 2px var(--text)" : "none", padding: "4px 9px", fontSize: "12px" }}>Maintenance</button>
      <button type="button" class="chip" disabled=${ro || ended} onClick=${() => onPatch({ kind: "arret", recipe_id: null }, "Arrêt planifié.")}
        style=${{ ...KIND_STYLE.arret, boxShadow: s.kind === "arret" ? "0 0 0 2px var(--text)" : "none", padding: "4px 9px", fontSize: "12px" }}>Arrêt</button>
    </div>
    <label class="f">Objectif (t)<div class="row" style="gap:6px;flex-wrap:nowrap">
      <input type="number" min="0" step="10" value=${target ?? s.target_t ?? ""} disabled=${ro} onInput=${e => setTarget(e.target.value)} style="width:100px" />
      <button type="button" disabled=${ro || target === null || target === ""} onClick=${() => { onPatch({ target_t: +target }, "Objectif enregistré."); setTarget(null); }}>Enregistrer</button></div></label>
    ${edit && html`<div class="row" style="gap:6px">
      <button type="button" disabled=${locked} onClick=${onSwap}>Échanger avec…</button>
      <button type="button" class="danger" disabled=${ended} onClick=${() => onPatch(locked ? { team: null } : { team: null, recipe_id: null, kind: "production", comment: null }, "Poste vidé.")}>Vider ce poste</button></div>`}
  </div>`;
}
