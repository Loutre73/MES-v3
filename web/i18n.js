// Traduction de l'interface : le français est la langue source, chaque autre langue est un dictionnaire
// « texte français → traduction » (i18n-en.js…). Un texte absent du dictionnaire reste en français.
// La langue est propre à chaque utilisateur (enregistrée côté serveur) ; ce navigateur s'en souvient
// pour l'écran de connexion.
import { useState, useEffect } from "./vendor/preact-htm.module.js";
import EN from "./i18n-en.js";

// code → [nom affiché dans sa langue, locale des dates et nombres, dictionnaire]
export const LANGS = {
  fr: ["Français", "fr-FR", null],
  en: ["English", "en-GB", EN],
};
const KEY = "mes-langue";

function initialLang() {
  try { const l = localStorage.getItem(KEY); if (LANGS[l]) return l; } catch (e) { /* stockage indisponible */ }
  const nav = (navigator.language || "fr").slice(0, 2).toLowerCase();
  return LANGS[nav] ? nav : "fr";
}

let lang = initialLang();
const listeners = new Set();
document.documentElement.lang = lang;

export const getLang = () => lang;
export const locale = () => LANGS[lang][1];

export function setLang(l) {
  if (!LANGS[l] || l === lang) return;
  lang = l;
  document.documentElement.lang = l;
  try { localStorage.setItem(KEY, l); } catch (e) { /* stockage indisponible */ }
  listeners.forEach(f => f(l));
}

// abonnement hors Preact (clavier tactile…)
export function onLang(f) { listeners.add(f); return () => listeners.delete(f); }

export function useLang() {
  const [l, set] = useState(lang);
  useEffect(() => { listeners.add(set); return () => listeners.delete(set); }, []);
  return l;
}

// tr("Texte {n}", { n: 3 }) : traduction dans la langue courante, puis remplacement des {variables}
export function tr(fr, vars) {
  const dict = LANGS[lang][2];
  let s = (dict && dict[fr]) || fr;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] === undefined || vars[k] === null ? m : vars[k]));
  return s;
}
// N_("Texte") marque un texte à traduire défini hors rendu (tableaux de libellés) ; tr() le traduit à l'affichage
export const N_ = s => s;

// formats de dates suivant la langue : fmt({ ...options Intl }).format(date)
const cache = {};
export function fmt(opts) {
  const k = JSON.stringify(opts);
  return {
    format: d => {
      const c = locale() + k;
      return (cache[c] = cache[c] || new Intl.DateTimeFormat(locale(), opts)).format(d);
    },
  };
}
