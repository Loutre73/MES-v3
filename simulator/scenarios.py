"""Scénarios du simulateur : groupes de variables, règles à effets et frises de règles.

Un *groupe* sélectionne des variables par type de structure (moteur, ligne...), par liste ou plage
d'instances (« M141-M160, M170 », « L* »), par morceau de nom et par membres (Courant, Marche...).
Une *règle* applique un effet à une cible (un groupe enregistré ou une sélection directe) pendant
une fenêtre de la frise, éventuellement répétée et tirée au sort. Un *scénario* est une frise de
règles, jouée une fois ou en boucle ; plusieurs scénarios peuvent tourner en même temps, et les
règles qui touchent la même variable s'empilent dans l'ordre de la frise.

Les durées sont en secondes de temps simulé : avec la vitesse ×60, une règle d'une heure dure
une minute réelle. Groupes et scénarios sont enregistrés dans SIM_DATA/scenarios.json.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("simulator.scenarios")

# Effets : libellé, portée, paramètres (défaut, min, max)
#   var      : modifie la valeur publiée de chaque variable ciblée
#   instance : agit sur le comportement de l'instance (arrêt, défaut, fréquence des arrêts)
#   global   : agit sur tout l'automate
EFFECTS: dict[str, tuple[str, str, dict[str, tuple[float, float, float]]]] = {
    "valeur": ("Valeur", "var", {"valeur": (0, -1e9, 1e9)}),
    "decalage": ("Décalage", "var", {"valeur": (5, -1e9, 1e9)}),
    "facteur": ("Facteur", "var", {"facteur": (1.2, 0, 100)}),
    "rampe": ("Rampe", "var", {"pct": (20, -100, 1000)}),
    "bruit": ("Bruit", "var", {"pct": (5, 0, 100)}),
    "sinus": ("Sinus", "var", {"pct": (10, 0, 100), "periode_s": (600, 2, 86400)}),
    "figer": ("Figer", "var", {}),
    "arret": ("Arrêt", "instance", {"code": (7, 1, 999)}),
    "defaut": ("Défaut", "instance", {"code": (1, 1, 999)}),
    "aleas": ("Aléas", "instance", {"arrets_h": (20, 0, 3600), "duree_arret_s": (30, 1, 3600)}),
    "qualite": ("Qualité mauvaise", "var", {}),
    "coupure": ("Coupure OPC UA", "global", {}),
}
NUMERIC_ONLY = {"decalage", "facteur", "rampe", "bruit", "sinus"}
STOPPABLE = {"moteur", "ligne"}          # structures qui ont une marche / un arrêt
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
MAX_DURATION = 30 * 24 * 3600            # 30 jours simulés
MAX_RULES = 50


def check_id(i: str) -> str:
    if not isinstance(i, str) or not ID_RE.fullmatch(i):
        raise ValueError("identifiant invalide (lettres, chiffres, - et _, 40 au plus)")
    return i


def _num(body: dict, key: str, default: float, lo: float, hi: float, label: str | None = None) -> float:
    v = body.get(key, default)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label or key} : nombre attendu") from None
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{label or key} hors bornes ({lo:g} à {hi:g})")
    return v


# ----------------------------------------------------------------------------- sélection de variables
def _instance_matcher(spec: str):
    """« M141-M160, M170 L* » -> fonction nom -> bool (vide : toutes)."""
    tokens = [t for t in re.split(r"[\s,;]+", spec or "") if t]
    if not tokens:
        return lambda name: True
    exact, ranges, globs = set(), [], []
    for t in tokens:
        m = re.fullmatch(r"([A-Za-z_]*)(\d+)-([A-Za-z_]*)(\d+)", t)
        if m and (not m.group(3) or m.group(3) == m.group(1)):
            lo, hi = sorted((int(m.group(2)), int(m.group(4))))
            ranges.append((m.group(1).lower(), lo, hi))
        elif any(c in t for c in "*?["):
            globs.append(t.lower())
        else:
            exact.add(t.lower())

    def match(name: str) -> bool:
        n = name.lower()
        if n in exact or any(fnmatch.fnmatchcase(n, g) for g in globs):
            return True
        m = re.fullmatch(r"([a-z_]*)(\d+)", n)
        return bool(m) and any(m.group(1) == p and lo <= int(m.group(2)) <= hi for p, lo, hi in ranges)

    return match


def normalize_selection(body: dict, structs: dict[str, list[str]]) -> dict:
    """Valide une sélection {type, dbs, instances, contient, membres}."""
    if not isinstance(body, dict):
        raise ValueError("sélection invalide")
    typ = body.get("type") or "tous"
    if typ != "tous" and typ not in structs:
        raise ValueError(f"type inconnu : {typ}")
    members = body.get("membres") or []
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise ValueError("membres : liste attendue")
    known = set(structs[typ]) if typ != "tous" else {m for ms in structs.values() for m in ms}
    bad = [m for m in members if m not in known]
    if bad:
        raise ValueError(f"membre inconnu : {bad[0]}")
    dbs = body.get("dbs") or []
    if not isinstance(dbs, list):
        raise ValueError("dbs : liste attendue")
    return {"type": typ, "dbs": [str(d) for d in dbs][:50], "instances": str(body.get("instances") or "")[:500],
            "contient": str(body.get("contient") or "")[:80], "membres": members}


def select(sel: dict, instances) -> list:
    """Instances (et membres) d'une sélection : [(instance, [membres])]."""
    match = _instance_matcher(sel.get("instances", ""))
    contains = (sel.get("contient") or "").lower()
    typ, dbs, members = sel.get("type", "tous"), set(sel.get("dbs") or []), sel.get("membres") or []
    out = []
    for inst in instances:
        if typ != "tous" and inst.struct != typ:
            continue
        if dbs and inst.db not in dbs:
            continue
        if not match(inst.name):
            continue
        if contains and contains not in f"{inst.db}.{inst.name}".lower():
            continue
        ms = [m for m in inst.vars if not members or m in members]
        if ms:
            out.append((inst, ms))
    return out


# ----------------------------------------------------------------------------- frise
@dataclass
class Active:
    """Effets en cours pour un cycle du simulateur."""
    var: dict[int, list] = field(default_factory=dict)        # id(var) -> [(effet, params, avancement, t, clé)]
    vars: dict[int, object] = field(default_factory=dict)     # id(var) -> var
    inst: dict[str, dict] = field(default_factory=dict)       # nom d'instance -> forçages
    coupure: bool = False


def normalize_rule(r: dict, groups: dict, structs: dict[str, list[str]], seq_dur: float) -> dict:
    if not isinstance(r, dict):
        raise ValueError("règle invalide")
    eff = r.get("effet")
    if eff not in EFFECTS:
        raise ValueError(f"effet inconnu : {eff}")
    label, scope, spec = EFFECTS[eff]
    raw = r.get("params") or {}
    if not isinstance(raw, dict):
        raise ValueError("paramètres invalides")
    params = {k: _num(raw, k, d, lo, hi, f"{label} · {k}") for k, (d, lo, hi) in spec.items()}
    cible = r.get("cible")
    if scope == "global":
        cible = None
    elif isinstance(cible, str):
        if cible not in groups:
            raise ValueError(f"groupe inconnu : {cible}")
    else:
        cible = normalize_selection(cible or {}, structs)
    debut = _num(r, "debut_s", 0, 0, MAX_DURATION, "début")
    duree = _num(r, "duree_s", 60, 1, MAX_DURATION, "durée")
    rep = _num(r, "repeter_s", 0, 0, MAX_DURATION, "répétition")
    if rep and rep < duree:
        raise ValueError("la répétition doit être plus longue que la durée de la règle")
    if debut >= seq_dur:
        raise ValueError("une règle commence après la fin du scénario")
    return {"effet": eff, "cible": cible, "params": params, "debut_s": debut, "duree_s": duree,
            "repeter_s": rep, "proba": _num(r, "proba", 1, 0, 1, "probabilité"),
            "note": str(r.get("note") or "")[:80]}


class Engine:
    def __init__(self, data_dir: str | None = None):
        self.path = Path(data_dir) / "scenarios.json" if data_dir else None
        self.groups: dict[str, dict] = {}
        self.seqs: dict[str, dict] = {}
        self.running: dict[str, float] = {}        # scénario -> horloge simulée au lancement
        self._cache: dict[str, list] = {}
        self._frozen: dict[tuple, object] = {}
        self.instances: list = []
        self.structs: dict[str, list[str]] = {}

    def bind(self, instances, structs: dict[str, list[str]]) -> None:
        self.instances = instances
        self.structs = structs
        self._cache.clear()

    # -- persistance
    def load(self) -> None:
        data = None
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.warning("scénarios enregistrés ignorés : %s", e)
        if data is None:
            data = seed(self.instances)
        for gid, g in (data.get("groupes") or {}).items():
            try:
                self._put_group(gid, g)
            except ValueError as e:
                log.warning("groupe %s ignoré : %s", gid, e)
        for sid, s in (data.get("scenarios") or {}).items():
            try:
                self._put_seq(sid, s)
            except ValueError as e:
                log.warning("scénario %s ignoré : %s", sid, e)

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"groupes": self.groups, "scenarios": self.seqs}, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("scénarios non enregistrés : %s", e)

    # -- groupes
    def _put_group(self, gid: str, body: dict) -> dict:
        check_id(gid)
        nom = str(body.get("nom") or "").strip()[:60]
        if not nom:
            raise ValueError("nom du groupe obligatoire")
        g = {"nom": nom, **normalize_selection(body, self.structs)}
        self.groups[gid] = g
        self._cache.pop(gid, None)
        return g

    def put_group(self, gid: str, body: dict) -> dict:
        g = self._put_group(gid, body)
        self.save()
        return g

    def delete_group(self, gid: str) -> None:
        if gid not in self.groups:
            raise LookupError("groupe introuvable")
        users = [s["nom"] for s in self.seqs.values() if any(r["cible"] == gid for r in s["regles"])]
        if users:
            raise ValueError(f"groupe utilisé par : {', '.join(users[:3])}")
        del self.groups[gid]
        self._cache.pop(gid, None)
        self.save()

    def resolve(self, cible) -> list:
        if cible is None:
            return []
        if isinstance(cible, str):
            if cible not in self._cache:
                self._cache[cible] = select(self.groups.get(cible) or {"type": "__aucun__"}, self.instances)
            return self._cache[cible]
        key = json.dumps(cible, sort_keys=True)
        if key not in self._cache:
            if len(self._cache) > 500:
                self._cache = {k: v for k, v in self._cache.items() if k in self.groups}
            self._cache[key] = select(cible, self.instances)
        return self._cache[key]

    def preview(self, body: dict, limit: int = 12) -> dict:
        sel = normalize_selection(body, self.structs)
        hits = select(sel, self.instances)
        tags = [f"{i.db}.{i.name}.{m}" for i, ms in hits for m in ms]
        return {"instances": len(hits), "tags": len(tags), "exemples": tags[:limit]}

    def count(self, cible) -> dict:
        hits = self.resolve(cible)
        return {"instances": len(hits), "tags": sum(len(ms) for _, ms in hits)}

    # -- scénarios
    def _put_seq(self, sid: str, body: dict) -> dict:
        check_id(sid)
        nom = str(body.get("nom") or "").strip()[:60]
        if not nom:
            raise ValueError("nom du scénario obligatoire")
        dur = _num(body, "duree_s", 3600, 10, MAX_DURATION, "durée du scénario")
        regles = body.get("regles") or []
        if not isinstance(regles, list) or len(regles) > MAX_RULES:
            raise ValueError(f"{MAX_RULES} règles au plus")
        rules = [normalize_rule(r, self.groups, self.structs, dur) for r in regles]
        s = {"nom": nom, "duree_s": dur, "boucle": bool(body.get("boucle")), "regles": rules}
        self.seqs[sid] = s
        return s

    def put_seq(self, sid: str, body: dict) -> dict:
        s = self._put_seq(sid, body)
        self.save()
        return s

    def delete_seq(self, sid: str) -> None:
        if sid not in self.seqs:
            raise LookupError("scénario introuvable")
        del self.seqs[sid]
        self.running.pop(sid, None)
        self.save()

    def start(self, sid: str, clock: float) -> None:
        if sid not in self.seqs:
            raise LookupError("scénario introuvable")
        self.running[sid] = clock
        self._frozen = {k: v for k, v in self._frozen.items() if k[0] != sid}
        log.info("scénario lancé : %s", self.seqs[sid]["nom"])

    def stop(self, sid: str | None = None) -> None:
        for s in [sid] if sid else list(self.running):
            if self.running.pop(s, None) is not None:
                log.info("scénario arrêté : %s", self.seqs.get(s, {}).get("nom", s))
        self._frozen = {k: v for k, v in self._frozen.items() if k[0] in self.running}

    # -- calcul des effets à un instant
    def occurrences(self, sid: str, clock: float):
        """Règles actives d'un scénario en cours : [(n° règle, règle, avancement 0..1, t dans la règle, clé)]."""
        s = self.seqs[sid]
        e = clock - self.running[sid]
        cycle = 0
        if s["boucle"]:
            cycle, e = divmod(e, s["duree_s"])
        elif e >= s["duree_s"]:
            return None                           # terminé
        out = []
        for i, r in enumerate(s["regles"]):
            rel = e - r["debut_s"]
            if rel < 0:
                continue
            n = 0
            if r["repeter_s"]:
                n, rel = divmod(rel, r["repeter_s"])
            if rel >= r["duree_s"]:
                continue
            key = (sid, int(cycle), i, int(n))
            if r["proba"] < 1 and random.Random(f"{key}:{self.running[sid]}").random() >= r["proba"]:
                continue
            out.append((i, r, rel / r["duree_s"], rel, key))
        return out

    def compute(self, clock: float) -> Active:
        act = Active()
        for sid in list(self.running):
            occ = self.occurrences(sid, clock)
            if occ is None:
                self.stop(sid)
                continue
            for _, r, prog, t, key in occ:
                eff = r["effet"]
                scope = EFFECTS[eff][1]
                if scope == "global":
                    act.coupure = True
                    continue
                for inst, members in self.resolve(r["cible"]):
                    if scope == "instance":
                        if inst.struct in STOPPABLE:
                            act.inst.setdefault(inst.name, {})[eff] = r["params"]
                        continue
                    for m in members:
                        var = inst.vars[m]
                        act.var.setdefault(id(var), []).append((eff, r["params"], prog, t, key))
                        act.vars[id(var)] = var
        return act

    def apply(self, var, raw, effects: list, rng: random.Random):
        """Valeur publiée d'une variable à partir de sa valeur brute ; renvoie (valeur, qualité mauvaise)."""
        out, bad = raw, False
        is_bool = isinstance(raw, bool)
        for eff, p, prog, t, key in effects:
            if eff == "qualite":
                bad = True
            elif eff == "valeur":
                out = bool(p["valeur"]) if is_bool else p["valeur"]
            elif eff == "figer":
                out = self._frozen.setdefault(key + (id(var),), out)
            elif is_bool:
                continue
            elif eff == "decalage":
                out += p["valeur"]
            elif eff == "facteur":
                out *= p["facteur"]
            elif eff == "rampe":
                out *= 1 + p["pct"] / 100.0 * prog
            elif eff == "bruit":
                out *= 1 + rng.gauss(0, p["pct"] / 100.0)
            elif eff == "sinus":
                out *= 1 + p["pct"] / 100.0 * math.sin(2 * math.pi * t / p["periode_s"])
        return out, bad

    # -- état pour la console
    def state(self, clock: float) -> dict:
        groups = [{"id": gid, **g, **self.count(gid)} for gid, g in self.groups.items()]
        seqs = []
        for sid, s in self.seqs.items():
            run = None
            if sid in self.running:
                occ = self.occurrences(sid, clock)
                if occ is None:
                    self.stop(sid)
                else:
                    e = clock - self.running[sid]
                    run = {"ecoule_s": e, "position_s": e % s["duree_s"] if s["boucle"] else e,
                           "actives": [i for i, *_ in occ]}
            rules = [{**r, **self.count(r["cible"])} if r["cible"] is not None else r for r in s["regles"]]
            seqs.append({"id": sid, **s, "regles": rules, "en_cours": run})
        return {
            "groupes": groups,
            "scenarios": seqs,
            "effets": [{"id": k, "label": lab, "portee": sc, "params": {p: list(v) for p, v in spec.items()}}
                       for k, (lab, sc, spec) in EFFECTS.items()],
            "types": self.structs,
        }


def seed(instances) -> dict:
    """Groupes et scénarios d'exemple, construits sur les instances réelles du site simulé."""
    first = {}
    for i in instances:
        first.setdefault(i.struct, i.name)
    L, M = first.get("ligne", "L1"), first.get("moteur", "M1")
    groups = {
        "courants": {"nom": "Courant des moteurs", "type": "moteur", "membres": ["Courant"]},
        "lignes": {"nom": "Toutes les lignes", "type": "ligne"},
        "ligne-1": {"nom": f"Ligne {L}", "type": "ligne", "instances": L},
        "moteur-1": {"nom": f"Moteur {M}", "type": "moteur", "instances": M},
        "puissances": {"nom": "Puissance des compteurs", "type": "compteur_energie", "membres": ["Puissance_kW"]},
        "mesures": {"nom": "Mesures de process", "type": "mesure"},
    }
    courant_m = {"type": "moteur", "instances": M, "membres": ["Courant"]}
    scen = {
        "panne-ligne": {"nom": "Panne de ligne", "duree_s": 720, "regles": [
            {"effet": "defaut", "cible": "ligne-1", "params": {"code": 1}, "debut_s": 0, "duree_s": 720}]},
        "micro-arrets": {"nom": "Micro-arrêts", "duree_s": 900, "regles": [
            {"effet": "arret", "cible": "ligne-1", "params": {"code": 2}, "debut_s": 0, "duree_s": 20, "repeter_s": 120}]},
        "defaut-moteur": {"nom": "Défaut moteur", "duree_s": 600, "regles": [
            {"effet": "defaut", "cible": "moteur-1", "debut_s": 0, "duree_s": 600}]},
        "derive-moteur": {"nom": "Dérive moteur", "duree_s": 3600, "regles": [
            {"effet": "rampe", "cible": courant_m, "params": {"pct": 15}, "debut_s": 0, "duree_s": 1800},
            {"effet": "facteur", "cible": courant_m, "params": {"facteur": 1.15}, "debut_s": 1800, "duree_s": 1800}]},
        "pic-conso": {"nom": "Pic de consommation", "duree_s": 600, "regles": [
            {"effet": "facteur", "cible": "puissances", "params": {"facteur": 1.45}, "debut_s": 0, "duree_s": 600}]},
        "coupure-opcua": {"nom": "Coupure OPC UA", "duree_s": 60, "regles": [
            {"effet": "coupure", "debut_s": 0, "duree_s": 60}]},
        "cascade": {"nom": "Panne en cascade", "duree_s": 3600, "regles": [
            {"effet": "rampe", "cible": courant_m, "params": {"pct": 20}, "debut_s": 0, "duree_s": 900},
            {"effet": "defaut", "cible": "moteur-1", "debut_s": 900, "duree_s": 600},
            {"effet": "arret", "cible": "ligne-1", "params": {"code": 7}, "debut_s": 900, "duree_s": 1020},
            {"effet": "facteur", "cible": "puissances", "params": {"facteur": 0.6}, "debut_s": 900, "duree_s": 1020},
            {"effet": "bruit", "cible": "courants", "params": {"pct": 5}, "debut_s": 0, "duree_s": 480, "repeter_s": 1200},
            {"effet": "coupure", "debut_s": 2400, "duree_s": 120}]},
        "journee": {"nom": "Journée agitée", "duree_s": 86400, "boucle": True, "regles": [
            {"effet": "aleas", "cible": "lignes", "params": {"arrets_h": 8, "duree_arret_s": 240},
             "debut_s": 6 * 3600, "duree_s": 3600, "repeter_s": 8 * 3600},
            {"effet": "sinus", "cible": "puissances", "params": {"pct": 25, "periode_s": 86400}, "debut_s": 0, "duree_s": 86400},
            {"effet": "qualite", "cible": {"type": "mesure", "instances": "P1-P20"}, "debut_s": 13 * 3600, "duree_s": 900}]},
    }
    return {"groupes": groups, "scenarios": scen}
