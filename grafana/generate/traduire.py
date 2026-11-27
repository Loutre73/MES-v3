"""Génère les tableaux Grafana traduits (grafana/dashboards-<langue>/) à partir des tableaux français.

Les tableaux français restent la source ; chaque langue a son dictionnaire grafana/i18n/<langue>.json
(texte français → traduction). Sont traduits : titres, descriptions, légendes, noms affichés, libellés
de variables, textes des correspondances de valeurs, renommages de colonnes, alias SQL (AS "…") et les
littéraux SQL affichés listés dans la section "sql" du dictionnaire. Un texte absent fait échouer le script
(liste des manques affichée), pour qu'aucun tableau ne reste à moitié traduit.

    python3 grafana/generate/traduire.py          # régénère toutes les langues
    python3 grafana/generate/traduire.py --verifier   # échoue si un fichier généré n'est pas à jour
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "dashboards"
KEYS = {"title", "description", "label", "displayName", "legendFormat", "text", "content", "noValue", "placeholder", "xField"}
UIDS = sorted(json.loads(p.read_text())["uid"] for p in SRC.glob("*.json"))


class Traducteur:
    def __init__(self, lang: str, d: dict):
        self.lang, self.t, self.sql, self.manques = lang, d["textes"], d.get("sql", {}), set()

    def mot(self, s: str) -> str:
        if not s or not re.search(r"[A-Za-zÀ-ÿ]", s) or s == "__auto" or re.fullmatch(r"\{\{\w+\}\}", s):
            return s
        if s in self.t:
            return self.t[s]
        self.manques.add(s)
        return s

    def motif(self, s: str) -> str:
        """Option d'un « matcher » : nom exact de champ ou expression régulière ^mot.*"""
        m = re.fullmatch(r"\^(.+)\.\*", s)
        if m and re.search(r"[A-Za-zÀ-ÿ]{2,}", m.group(1)):
            return "^" + re.escape(self.mot(m.group(1))).replace("\\ ", " ") + ".*"
        return self.mot(s) if re.search(r"[A-Za-zÀ-ÿ]{2,}", s) and not s.startswith("^") else s

    def requete(self, q: str) -> str:
        q = re.sub(r'(?i)\bAS\s+"([^"]+)"', lambda m: f'AS "{self.mot(m.group(1))}"', q)
        for fr, tr in self.sql.items():
            q = q.replace(f"'{fr}'", "'" + tr.replace("'", "''") + "'")
        return q

    def tableau(self, o, cle: str | None = None):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k == "renameByName" and isinstance(v, dict):
                    out[k] = {a: self.mot(b) for a, b in v.items()}
                elif k == "matcher" and isinstance(v, dict) and isinstance(v.get("options"), str):
                    out[k] = {**v, "options": self.motif(v["options"])}
                elif k in ("rawSql",) and isinstance(v, str):
                    out[k] = self.requete(v)
                elif k in KEYS and isinstance(v, str):
                    out[k] = self.mot(v)
                elif k == "url" and isinstance(v, str):
                    out[k] = re.sub(r"/d/([\w-]+)", lambda m: f"/d/{m.group(1)}-{self.lang}" if m.group(1) in UIDS else m.group(0), v)
                elif k == "uid" and cle is None and isinstance(v, str) and v in UIDS:
                    out[k] = f"{v}-{self.lang}"
                else:
                    out[k] = self.tableau(v, k)
            # propriété « displayName » dans les surcharges : {"id": "displayName", "value": "…"}
            if out.get("id") == "displayName" and isinstance(out.get("value"), str):
                out["value"] = self.mot(out["value"])
            return out
        if isinstance(o, list):
            return [self.tableau(x, cle) for x in o]
        return o


def _panneaux(ps):
    for p in ps:
        yield p
        yield from _panneaux(p.get("panels", []))


def _noms(p) -> list[str]:
    """Noms de champs qu'un panneau peut produire : légendes, alias SQL, renommages, noms affichés."""
    n = []
    for t in p.get("targets", []):
        n.append(t.get("legendFormat") or "")
        n += re.findall(r'(?i)\bAS\s+"([^"]+)"', t.get("rawSql") or "")
    for tr in p.get("transformations", []):
        n += list((tr.get("options") or {}).get("renameByName", {}).values())
    return [x for x in n if x]


def _correspond(m: dict, noms: list[str]) -> bool | None:
    o = m.get("options")
    if m.get("id") == "byRegexp":
        return any(re.match(o, x) for x in noms)
    if m.get("id") == "byName":
        return o in noms
    return None


def coherence(fr: dict, tr: dict) -> list[str]:
    """Une surcharge qui s'applique à un champ en français doit s'appliquer au même champ traduit."""
    err = []
    for a, b in zip(_panneaux(fr.get("panels", [])), _panneaux(tr.get("panels", []))):
        na, nb = _noms(a), _noms(b)
        oa = a.get("fieldConfig", {}).get("overrides", [])
        ob = b.get("fieldConfig", {}).get("overrides", [])
        for x, y in zip(oa, ob):
            ca, cb = _correspond(x["matcher"], na), _correspond(y["matcher"], nb)
            if ca and not cb:
                err.append(f"{fr['uid']} / {a.get('title')} : la surcharge {x['matcher'].get('options')!r} "
                           f"ne trouve plus son champ ({y['matcher'].get('options')!r})")
    return err


def generer(verifier: bool = False) -> int:
    erreurs = 0
    for dico in sorted((ROOT / "i18n").glob("*.json")):
        lang = dico.stem
        tr = Traducteur(lang, json.loads(dico.read_text()))
        dst = ROOT / f"dashboards-{lang}"
        dst.mkdir(exist_ok=True)
        for src in sorted(SRC.glob("*.json")):
            fr = json.loads(src.read_text())
            d = tr.tableau(fr)
            for e in coherence(fr, d):
                print(f"[{lang}] {e}")
                erreurs += 1
            d["tags"] = sorted(set(d.get("tags", [])) | {lang})
            texte = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
            cible = dst / src.name
            if verifier:
                if not cible.exists() or cible.read_text() != texte:
                    print(f"{cible.relative_to(ROOT.parent)} n'est pas à jour : python3 grafana/generate/traduire.py")
                    erreurs += 1
            else:
                cible.write_text(texte)
        for m in sorted(tr.manques):
            print(f"[{lang}] traduction manquante : {json.dumps(m, ensure_ascii=False)}")
        erreurs += len(tr.manques)
    return erreurs


if __name__ == "__main__":
    sys.exit(1 if generer("--verifier" in sys.argv) else 0)
