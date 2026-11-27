// Recettes : liste et fiche côte à côte, sur une hauteur d'écran, pensées pour le tactile.
import { html, useState, useEffect } from "./vendor/preact-htm.module.js";
import { api, useApi, Dialog, Band, Icon, Pill, dt, num, can, useToast, tr, N_, fmt } from "./lib.js";

const I = {
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  filter: "M3 5h18l-7 8v6l-4 2v-8z",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  archive: "M3 4h18v4H3zM5 8v12h14V8M10 12h4",
  trash: "M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14",
  check: "M5 12l5 5L20 7",
  play: "M7 4l13 8-13 8z",
  cal: "M3 5h18v16H3zM3 10h18M8 3v4M16 3v4",
  plc: "M4 4h16v12H4zM8 20h8M12 16v4",
  compare: "M8 3v18M16 3v18M3 8h5M16 16h5",
  close: "M18 6 6 18M6 6l12 12",
};
const STATUS = { validee: ["good", N_("Validée")], brouillon: ["warn", N_("Brouillon")] };
const LOAD = {
  en_cours: ["warn", N_("Chargement…")], acceptee: ["good", N_("Acceptée")], refusee: ["bad", N_("Refusée")],
  delai_depasse: ["bad", N_("Sans réponse")], erreur: ["bad", N_("Erreur")],
};
const fmtShift = fmt({ weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const isInt = p => /^(U?Int|Byte)/.test(p.datatype);
const outOfRange = (p, v) => v !== "" && v !== undefined && v !== null &&
  (isNaN(+v) || (p.min !== undefined && p.min !== null && +v < p.min) || (p.max !== undefined && p.max !== null && +v > p.max));

function StatusPill({ r }) {
  if (r.archived) return html`<${Pill} kind="idle">${tr("Archivée")}<//>`;
  const [k, l] = STATUS[r.status] || ["idle", r.status];
  return html`<${Pill} kind=${k}>${tr(l)}<//>`;
}

function IconBtn({ d, label, onClick, cls = "", disabled }) {
  return html`<button type="button" class=${"rc-ib " + cls} title=${label} aria-label=${label} disabled=${disabled} onClick=${onClick}><${Icon} d=${d} size=${18} /></button>`;
}

export function Recettes({ user, route }) {
  const sel = route[1] && route[1] !== "nouvelle" ? +route[1] : null;
  const chef = can(user, "chef_equipe");
  const [f, setF] = useState({ q: "", vue: "toutes", param: "", op: ">", valeur: "" });
  const [adv, setAdv] = useState(false);
  const [dlg, setDlg] = useState(route[1] === "nouvelle" ? { type: "new" } : null);
  const [toast, showToast] = useToast();
  const types = useApi("/recettes/types");
  const qs = new URLSearchParams({ q: f.q, archivees: f.vue === "archivees" });
  if (f.vue === "validee" || f.vue === "brouillon") qs.set("statut", f.vue);
  if (f.param && f.valeur !== "") { qs.set("param", f.param); qs.set("op", f.op); qs.set("valeur", f.valeur); }
  const list = useApi(`/recettes?${qs}`);
  const rows = (list.data || []).filter(r => f.vue !== "archivees" || r.archived);
  const params = (types.data || []).flatMap(t => t.definition.params.map(p => ({ ...p, type: t.label })));
  const go = id => { location.hash = id ? `#/recettes/${id}` : "#/recettes"; };
  const done = (text, id) => { showToast(text); list.reload(); if (id !== undefined) go(id); };

  return html`<div class="view">
    <${Band} over=${tr("Outils")} title=${tr("Recettes")}>
      ${chef && html`<button type="button" class="pl-plus" aria-label=${tr("Nouvelle recette")} title=${tr("Nouvelle recette")} onClick=${() => setDlg({ type: "new" })}>+</button>`}
    </${Band}>
    <div class="rc-grid">
      <aside class="tile rc-side" aria-label=${tr("Liste des recettes")}>
        <div class="rc-search">
          <${Icon} d=${I.search} />
          <input type="search" placeholder=${tr("Rechercher")} aria-label=${tr("Rechercher une recette")} value=${f.q} onInput=${e => setF({ ...f, q: e.target.value })} />
          <button type="button" class=${"rc-ib sm" + (adv || f.param ? " on" : "")} title=${tr("Filtrer sur un paramètre")} aria-label=${tr("Filtrer sur un paramètre")} onClick=${() => setAdv(!adv)}><${Icon} d=${I.filter} /></button>
        </div>
        ${adv && html`<div class="rc-adv">
          <select value=${f.param} onChange=${e => setF({ ...f, param: e.target.value })} aria-label=${tr("Paramètre")}>
            <option value="">${tr("Paramètre…")}</option>${params.map(p => html`<option value=${p.name}>${p.label}</option>`)}</select>
          <select value=${f.op} onChange=${e => setF({ ...f, op: e.target.value })} aria-label=${tr("Comparaison")}>${[">", ">=", "=", "<=", "<"].map(o => html`<option>${o}</option>`)}</select>
          <input type="number" step="any" value=${f.valeur} aria-label=${tr("Valeur")} onInput=${e => setF({ ...f, valeur: e.target.value })} />
          ${f.param && html`<button type="button" class="rc-ib sm" aria-label=${tr("Effacer le filtre")} title=${tr("Effacer")} onClick=${() => setF({ ...f, param: "", valeur: "" })}><${Icon} d=${I.close} /></button>`}
        </div>`}
        <div class="seg light rc-seg" role="group" aria-label=${tr("Statut")}>
          ${[["toutes", N_("Toutes")], ["validee", N_("Validées")], ["brouillon", N_("Brouillons")], ["archivees", N_("Archivées")]].map(([k, l]) =>
            html`<button type="button" class=${f.vue === k ? "on" : ""} aria-pressed=${f.vue === k} onClick=${() => setF({ ...f, vue: k })}>${tr(l)}</button>`)}
        </div>
        <div class="rc-items scroll">
          ${rows.map(r => html`<button type="button" class=${"rc-item" + (r.id === sel ? " on" : "")} onClick=${() => go(r.id)}>
            <span class="rc-l1"><b>${r.code}</b><span class="spacer"></span><${StatusPill} r=${r} /></span>
            <span class="rc-l2"><span class="rc-ell">${r.label}</span><span class="spacer"></span>
              ${r.postes_a_venir > 0 && html`<span class="rc-meta" title=${tr("{n} poste(s) à venir", { n: r.postes_a_venir })}><${Icon} d=${I.cal} size=${13} />${r.postes_a_venir}</span>`}
              <span class="rc-meta">v${r.version}</span></span>
          </button>`)}
          ${list.data && !rows.length && html`<p class="empty">${tr("Aucune recette")}</p>`}
          ${list.error && html`<p class="err">${list.error}</p>`}
        </div>
      </aside>
      ${sel
        ? html`<${Fiche} key=${sel} id=${sel} chef=${chef} toast=${showToast} onChanged=${() => list.reload()}
            onDup=${rec => setDlg({ type: "dup", rec })} onMeta=${rec => setDlg({ type: "meta", rec })} onDelete=${rec => setDlg({ type: "del", rec })} />`
        : html`<section class="tile rc-main rc-none"><div><${Icon} d=${I.plc} size=${40} /><p>${tr("Choisir une recette")}</p>
            ${chef && html`<button type="button" class="primary" onClick=${() => setDlg({ type: "new" })}>${tr("+ Nouvelle recette")}</button>`}</div></section>`}
    </div>
    ${dlg && dlg.type === "new" && html`<${NewDialog} types=${types.data || []} onClose=${() => setDlg(null)} onDone=${id => { setDlg(null); done(tr("Recette créée"), id); }} />`}
    ${dlg && dlg.type === "dup" && html`<${DupDialog} rec=${dlg.rec} onClose=${() => setDlg(null)} onDone=${id => { setDlg(null); done(tr("Recette dupliquée"), id); }} />`}
    ${dlg && dlg.type === "meta" && html`<${MetaDialog} rec=${dlg.rec} onClose=${() => setDlg(null)} onDone=${() => { setDlg(null); done(tr("Recette modifiée"), dlg.rec.id); }} />`}
    ${dlg && dlg.type === "del" && html`<${DeleteDialog} rec=${dlg.rec} onClose=${() => setDlg(null)}
      onDone=${t => { setDlg(null); done(t, null); }} />`}
    ${toast}
  </div>`;
}

// ----------------------------------------------------------------------------- fiche
function Fiche({ id, chef, toast, onChanged, onDup, onMeta, onDelete }) {
  const r = useApi(`/recettes/${id}`);
  const [vid, setVid] = useState(null);
  const [edit, setEdit] = useState(null), [comment, setComment] = useState("");
  const [cmpWith, setCmpWith] = useState(""), [cmp, setCmp] = useState(null);
  const [load, setLoad] = useState(null);
  const [panel, setPanel] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (r.data && !r.data.versions.some(v => v.id === vid)) setVid(r.data.versions[0].id); }, [r.data]);
  useEffect(() => {  // suivi du chargement automate
    if (!load || load.result !== "en_cours") return;
    const t = setTimeout(() => api(`/recettes/chargements/${load.id}`).then(x => {
      setLoad(x);
      if (x.result !== "en_cours") { r.reload(); toast(`${tr("Automate")} : ${tr(LOAD[x.result][1]).toLowerCase()}${x.details && x.details.motif ? " · " + x.details.motif : ""}`, x.result !== "acceptee"); }
    }), 700);
    return () => clearTimeout(t);
  }, [load]);
  const run = async (fn, okText) => {
    setBusy(true);
    try { const x = await fn(); if (okText) toast(okText); return x ?? true; } catch (e) { toast(e.message, true); return null; } finally { setBusy(false); }
  };
  const compareWith = async w => {
    setCmpWith(w); setCmp(null);
    if (w) setCmp(await run(() => api(`/recettes/versions/${v.id}/comparer?avec=${w}`)));
  };
  if (r.error) return html`<section class="tile rc-main rc-none"><div><${Icon} d=${I.plc} size=${40} /><p>${r.error}</p></div></section>`;
  if (!r.data || !vid) return html`<section class="tile rc-main"><p class="empty">${tr("Chargement…")}</p></section>`;
  const rec = r.data, tdef = rec.type.definition, u = rec.usages;
  const v = rec.versions.find(x => x.id === vid) || rec.versions[0];
  const latest = rec.versions[0];
  const inPlace = v.status === "brouillon" && !v.chargee;
  const badEdit = edit && tdef.params.some(p => edit[p.name] === "" || outOfRange(p, edit[p.name]));
  const pick = x => { setVid(x); setEdit(null); setCmp(null); setCmpWith(""); };
  const save = async () => {
    const values = Object.fromEntries(Object.entries(edit).map(([k, x]) => [k, x === "" ? null : +x]));
    const x = inPlace
      ? await run(() => api(`/recettes/versions/${v.id}`, { method: "PUT", body: { values, comment } }), tr("v{n} enregistrée", { n: v.version }))
      : await run(() => api(`/recettes/${id}/versions`, { method: "POST", body: { values, comment } }));
    if (!x) return;
    if (!inPlace) toast(tr("v{n} créée (brouillon)", { n: x.version }));
    setEdit(null); setComment(""); setVid(x.id); r.reload(); onChanged();
  };
  const charger = async () => {
    const x = await run(() => api(`/recettes/versions/${v.id}/charger`, { method: "POST" }));
    if (x) setLoad({ id: x.id, result: "en_cours" });
  };
  const loading = load && load.result === "en_cours";

  return html`<section class="tile rc-main" aria-label=${tr("Recette") + " " + rec.code}>
    <div class="rc-head">
      <div class="rc-title">
        <h2>${rec.code}</h2><${StatusPill} r=${{ ...v, archived: rec.archived }} />
        <p class="sub">${rec.label}${rec.product ? ` · ${rec.product}` : ""} · ${rec.type.label}</p>
      </div>
      <span class="spacer"></span>
      ${chef && !edit && html`<div class="rc-acts">
        <${IconBtn} d=${I.edit} label=${tr("Renommer")} onClick=${() => onMeta(rec)} />
        <${IconBtn} d=${I.copy} label=${tr("Dupliquer")} onClick=${() => onDup(rec)} />
        <${IconBtn} d=${I.archive} label=${rec.archived ? tr("Désarchiver") : tr("Archiver")} cls=${rec.archived ? "on" : ""}
          onClick=${async () => { if (await run(() => api(`/recettes/${id}/archiver`, { method: "POST" }), rec.archived ? tr("Recette réactivée") : tr("Recette archivée"))) { r.reload(); onChanged(); } }} />
        <${IconBtn} d=${I.trash} label=${tr("Supprimer")} cls="danger" onClick=${() => onDelete(rec)} />
      </div>`}
    </div>

    <div class="rc-bar">
      <div class="chips" role="group" aria-label=${tr("Versions")}>
        ${rec.versions.map(x => html`<button type="button" class=${"chip pl-sm rc-v" + (x.id === v.id ? " on" : "")} aria-pressed=${x.id === v.id}
          title=${`${STATUS[x.status] ? tr(STATUS[x.status][1]) : x.status} · ${x.created_by || ""} · ${dt(x.created_at)}${x.comment ? " · " + x.comment : ""}`}
          disabled=${!!edit} onClick=${() => pick(x.id)}><i class=${"rc-dot " + (STATUS[x.status] || ["idle"])[0]}></i>v${x.version}</button>`)}
      </div>
      <span class="spacer"></span>
      ${!edit && html`
        <label class="rc-cmp" title=${tr("Comparer")}><${Icon} d=${I.compare} />
          <select value=${cmpWith} aria-label=${tr("Comparer avec")} onChange=${e => compareWith(e.target.value)}>
            <option value="">${tr("Comparer…")}</option><option value="production">${tr("Automate")}</option>
            ${rec.versions.filter(x => x.id !== v.id).map(x => html`<option value=${x.id}>v${x.version}</option>`)}</select></label>
        ${cmp && html`<${Pill} kind=${cmp.ecarts ? "warn" : "good"}>${cmp.ecarts ? tr(cmp.ecarts > 1 ? "{n} écarts" : "{n} écart", { n: cmp.ecarts }) : tr("Identique")}<//>`}
        ${chef && html`<button type="button" onClick=${() => setEdit(Object.fromEntries(tdef.params.map(p => [p.name, v.values[p.name] ?? ""])))}><${Icon} d=${I.edit} />${tr("Modifier")}</button>`}
        ${chef && v.status === "brouillon" && html`<button type="button" disabled=${busy}
          onClick=${async () => { if (await run(() => api(`/recettes/versions/${v.id}/valider`, { method: "POST" }), tr("v{n} validée", { n: v.version }))) { r.reload(); onChanged(); } }}><${Icon} d=${I.check} />${tr("Valider")}</button>`}
        ${chef && inPlace && rec.versions.length > 1 && html`<${IconBtn} d=${I.trash} label=${tr("Supprimer ce brouillon")} cls="danger"
          onClick=${async () => { if (await run(() => api(`/recettes/versions/${v.id}`, { method: "DELETE" }), tr("Brouillon v{n} supprimé", { n: v.version }))) { setVid(null); r.reload(); onChanged(); } }} />`}
        ${chef && v.status === "validee" && !rec.archived && html`<button type="button" class="primary" disabled=${busy || loading} onClick=${charger}>
          <${Icon} d=${I.play} />${loading ? tr("Chargement…") : tr("Charger")}</button>`}`}
    </div>

    <div class="rc-scroll scroll">
      <table class="rc-params"><thead><tr><th>${tr("Paramètre")}</th><th class="num">${tr("Valeur")}</th><th class="num rc-range">${tr("Plage")}</th>
        ${cmp && html`<th class="num">${cmp.comparee === "Production en cours" ? tr("Automate") : cmp.comparee.replace("Version ", "v")}</th>`}</tr></thead>
        <tbody>${tdef.params.map(p => {
          const c = cmp && cmp.lignes.find(l => l.param === p.name);
          const val = edit ? edit[p.name] : v.values[p.name];
          return html`<tr class=${c && c.ecart ? "diff" : ""}>
            <td>${p.label}</td>
            <td class="num">${edit
              ? html`<input class=${"rc-in" + (outOfRange(p, val) ? " bad" : "")} type="number" inputmode="decimal" step="any" aria-label=${p.label}
                  value=${val} onInput=${e => setEdit({ ...edit, [p.name]: e.target.value })} />`
              : html`<b>${num(val, isInt(p) ? 0 : 2)}</b>`} <span class="rc-unit">${p.unit || ""}</span></td>
            <td class="num rc-range">${p.min ?? "–"} … ${p.max ?? "–"}</td>
            ${cmp && html`<td class="num">${c ? num(c.b, isInt(p) ? 0 : 2) : ""}</td>`}
          </tr>`;
        })}</tbody></table>
    </div>

    ${edit
      ? html`<div class="rc-foot">
          <input class="rc-why" placeholder=${tr("Motif")} aria-label=${tr("Motif de la modification")} value=${comment} onInput=${e => setComment(e.target.value)} />
          <button type="button" onClick=${() => { setEdit(null); setComment(""); }}>${tr("Annuler")}</button>
          <button type="button" class="primary" disabled=${busy || badEdit} onClick=${save}>${inPlace ? tr("Enregistrer v{n}", { n: v.version }) : tr("Créer v{n}", { n: latest.version + 1 })}</button>
        </div>`
      : html`<div class="rc-foot">
          <button type="button" class=${"chip pl-sm" + (panel === "postes" ? " on" : "")} onClick=${() => setPanel(panel === "postes" ? null : "postes")}>
            <${Icon} d=${I.cal} size=${14} />${tr(u.postes.length > 1 ? "{n} postes à venir" : "{n} poste à venir", { n: u.postes.length })}</button>
          <button type="button" class=${"chip pl-sm" + (panel === "charg" ? " on" : "")} onClick=${() => setPanel(panel === "charg" ? null : "charg")}>
            <${Icon} d=${I.plc} size=${14} />${tr(rec.chargements.length > 1 ? "{n} chargements" : "{n} chargement", { n: rec.chargements.length })}</button>
          <span class="spacer"></span>
          <span class="sub">${v.created_by || ""} · ${dt(v.created_at)}</span>
        </div>`}
    ${panel && !edit && html`<div class="rc-panel">
      ${panel === "postes" && (u.postes.length
        ? u.postes.map(s => html`<div class="rc-row"><b>${s.line}</b><span>${fmtShift.format(new Date(s.starts_at))}</span>${s.team && html`<span class="sub">${tr("Équipe")} ${s.team}</span>`}
            ${s.en_cours && html`<${Pill} kind="good">${tr("En cours")}<//>`}</div>`)
        : html`<p class="empty">${tr("Aucun poste planifié")}</p>`)}
      ${panel === "charg" && (rec.chargements.length
        ? rec.chargements.map(l => html`<div class="rc-row"><span>${dt(l.requested_at)}</span><b>v${l.version}</b><span class="sub">${l.requested_by}</span><span class="spacer"></span>
            <${Pill} kind=${(LOAD[l.result] || ["idle"])[0]}>${tr((LOAD[l.result] || [0, l.result])[1])}<//>
            ${(l.details || {}).motif && html`<span class="sub">${l.details.motif}</span>`}</div>`)
        : html`<p class="empty">${tr("Jamais chargée")}</p>`)}
    </div>`}
  </section>`;
}

// ----------------------------------------------------------------------------- dialogues
function useSubmit(onDone) {
  const [err, setErr] = useState(null), [busy, setBusy] = useState(false);
  const submit = async fn => {
    setBusy(true); setErr(null);
    try { onDone(await fn()); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return { err, busy, submit };
}

function Field({ label, children }) {
  return html`<label class="f">${label}${children}</label>`;
}

function NewDialog({ types, onClose, onDone }) {
  const [f, setF] = useState({ type_id: types.length === 1 ? String(types[0].id) : "", code: "", label: "", product: "" });
  const s = useSubmit(x => onDone(x.id));
  return html`<${Dialog} title=${tr("Nouvelle recette")} onClose=${onClose}>
    <div class="rc-form">
      ${types.length > 1 && html`<${Field} label=${tr("Type")}><select value=${f.type_id} onChange=${e => setF({ ...f, type_id: e.target.value })}>
        <option value="">${tr("Choisir…")}</option>${types.map(t => html`<option value=${t.id}>${t.label}</option>`)}</select><//>`}
      <${Field} label=${tr("Code")}><input value=${f.code} onInput=${e => setF({ ...f, code: e.target.value })} /><//>
      <${Field} label=${tr("Libellé")}><input value=${f.label} onInput=${e => setF({ ...f, label: e.target.value })} /><//>
      <${Field} label=${tr("Produit")}><input value=${f.product} onInput=${e => setF({ ...f, product: e.target.value })} /><//>
      ${s.err && html`<p class="err">${s.err}</p>`}
      <div class="rc-end"><button type="button" onClick=${onClose}>${tr("Annuler")}</button>
        <button type="button" class="primary" disabled=${s.busy || !f.type_id || !f.code.trim() || !f.label.trim()}
          onClick=${() => s.submit(() => api("/recettes", { method: "POST", body: { ...f, type_id: +f.type_id } }))}>${tr("Créer")}</button></div>
    </div><//>`;
}

function DupDialog({ rec, onClose, onDone }) {
  const [f, setF] = useState({ code: rec.code + "-2", label: rec.label + " (" + tr("copie") + ")" });
  const s = useSubmit(x => onDone(x.id));
  return html`<${Dialog} title=${tr("Dupliquer {x}", { x: rec.code })} onClose=${onClose}>
    <div class="rc-form">
      <${Field} label=${tr("Code")}><input value=${f.code} onInput=${e => setF({ ...f, code: e.target.value })} /><//>
      <${Field} label=${tr("Libellé")}><input value=${f.label} onInput=${e => setF({ ...f, label: e.target.value })} /><//>
      ${s.err && html`<p class="err">${s.err}</p>`}
      <div class="rc-end"><button type="button" onClick=${onClose}>${tr("Annuler")}</button>
        <button type="button" class="primary" disabled=${s.busy || !f.code.trim()}
          onClick=${() => s.submit(() => api(`/recettes/${rec.id}/dupliquer`, { method: "POST", body: { code: f.code, label: f.label || null } }))}>${tr("Dupliquer")}</button></div>
    </div><//>`;
}

function MetaDialog({ rec, onClose, onDone }) {
  const [f, setF] = useState({ code: rec.code, label: rec.label, product: rec.product || "" });
  const s = useSubmit(onDone);
  return html`<${Dialog} title=${tr("Renommer")} onClose=${onClose}>
    <div class="rc-form">
      <${Field} label=${tr("Code")}><input value=${f.code} onInput=${e => setF({ ...f, code: e.target.value })} /><//>
      <${Field} label=${tr("Libellé")}><input value=${f.label} onInput=${e => setF({ ...f, label: e.target.value })} /><//>
      <${Field} label=${tr("Produit")}><input value=${f.product} onInput=${e => setF({ ...f, product: e.target.value })} /><//>
      ${s.err && html`<p class="err">${s.err}</p>`}
      <div class="rc-end"><button type="button" onClick=${onClose}>${tr("Annuler")}</button>
        <button type="button" class="primary" disabled=${s.busy || !f.code.trim() || !f.label.trim()}
          onClick=${() => s.submit(() => api(`/recettes/${rec.id}`, { method: "PATCH", body: f }))}>${tr("Enregistrer")}</button></div>
    </div><//>`;
}

function DeleteDialog({ rec, onClose, onDone }) {
  const u = rec.usages;
  const running = u.postes.find(p => p.en_cours);
  const [detach, setDetach] = useState(false);
  const s = useSubmit(x => onDone(x.postes_retires ? tr(x.postes_retires > 1 ? "Recette supprimée · retirée de {n} postes" : "Recette supprimée · retirée de {n} poste", { n: x.postes_retires }) : tr("Recette supprimée")));
  const history = u.postes_passes + u.chargements;
  return html`<${Dialog} title=${tr("Supprimer {x} ?", { x: rec.code })} onClose=${onClose}>
    <div class="rc-form">
      ${running && html`<p class="err">${tr("En production sur {l} : attendre la fin du poste.", { l: running.line })}</p>`}
      ${!running && u.postes.length > 0 && html`
        <div class="rc-panel">${u.postes.slice(0, 6).map(p => html`<div class="rc-row"><b>${p.line}</b><span>${fmtShift.format(new Date(p.starts_at))}</span></div>`)}
          ${u.postes.length > 6 && html`<div class="rc-row sub">+ ${tr("{n} autres", { n: u.postes.length - 6 })}</div>`}</div>
        <label class="rc-check"><input type="checkbox" checked=${detach} onChange=${e => setDetach(e.target.checked)} />
          ${tr(u.postes.length > 1 ? "Retirer des {n} postes à venir" : "Retirer du poste à venir", { n: u.postes.length })}</label>`}
      ${history > 0 && html`<p class="sub" style="margin:0">${tr("Historique conservé")}</p>`}
      ${s.err && html`<p class="err">${s.err}</p>`}
      <div class="rc-end"><button type="button" onClick=${onClose}>${tr("Annuler")}</button>
        <button type="button" class="rc-danger" disabled=${s.busy || !!running || (u.postes.length > 0 && !detach)}
          onClick=${() => s.submit(() => api(`/recettes/${rec.id}?retirer_postes=${detach}`, { method: "DELETE" }))}>${tr("Supprimer")}</button></div>
    </div><//>`;
}
