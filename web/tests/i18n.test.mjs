// Contrôle des traductions : chaque texte marqué tr("…") ou N_("…") dans l'interface doit avoir sa traduction
// dans chaque dictionnaire (i18n-en.js…), avec les mêmes {variables}. Lancement : node web/tests/i18n.test.mjs
// Option --manquants : affiche les textes à traduire au format du dictionnaire.
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
const DICTS = { en: "i18n-en.js" };
const SKIP = new Set(Object.values(DICTS).concat(["i18n.js"]));

// tr("a"), N_("a"), tr(cond ? "a" : "b")
const STR = String.raw`"((?:[^"\\]|\\.)*)"`;
const CALL = new RegExp(String.raw`\b(?:tr|N_)\(\s*(?:[^"()]*?\?\s*)?` + STR + String.raw`(?:\s*:\s*` + STR + ")?", "g");

export function keys() {
  const out = new Map();
  for (const f of readdirSync(WEB).filter(f => f.endsWith(".js") && !SKIP.has(f))) {
    const src = readFileSync(join(WEB, f), "utf8");
    for (const m of src.matchAll(CALL)) for (const k of [m[1], m[2]]) if (k !== undefined) {
      const key = JSON.parse(`"${k}"`);
      if (!out.has(key)) out.set(key, f);
    }
  }
  return out;
}

const vars = s => [...s.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort().join(",");

const all = keys();
let bad = 0;
for (const [code, file] of Object.entries(DICTS)) {
  const dict = (await import(pathToFileURL(join(WEB, file)))).default;
  const missing = [...all.keys()].filter(k => !(k in dict));
  const wrongVars = [...all.keys()].filter(k => k in dict && vars(k) !== vars(dict[k]));
  if (process.argv.includes("--manquants")) missing.forEach(k => console.log(`  ${JSON.stringify(k)}: "",  // ${all.get(k)}`));
  else missing.forEach(k => console.log(`[${code}] traduction manquante (${all.get(k)}) : ${k}`));
  wrongVars.forEach(k => console.log(`[${code}] variables différentes : ${k} → ${dict[k]}`));
  bad += missing.length + wrongVars.length;
  console.log(`[${code}] ${all.size} textes, ${missing.length} manquants, ${wrongVars.length} erreurs de variables`);
}
process.exit(bad ? 1 : 0);
