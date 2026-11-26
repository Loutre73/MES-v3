"""Simulateur d'automate S7-1500 exposant un serveur OPC UA.

Reproduit l'arborescence d'un S7-1500 (PLC_1 / DataBlocksGlobal / <DB> / <instance> / <membre>)
et des NodeId au format Siemens : ns=3;s="DB"."Instance"."Membre".
Les comportements (moteurs, compteurs d'énergie, lignes, mesures de process) sont décrits
dans un fichier YAML, ce qui permet de simuler un site différent sans toucher au code.

Une petite API HTTP de contrôle (port 8081, réseau interne Docker uniquement) permet à la console
d'exploitation de lire l'état, de régler les règles à chaud et d'injecter des événements
(défaut moteur, arrêt de ligne, micro-arrêts, dérive, pic d'énergie, coupure, rafale).
Les règles modifiées sont gardées dans SIM_STATE_DIR/regles.json et rechargées au démarrage.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml
from asyncua import Server, ua

log = logging.getLogger("simulator")

SIEMENS_NS = "http://www.siemens.com/simatic-s7-opcua"

VARIANT = {
    "Real": ua.VariantType.Float,
    "LReal": ua.VariantType.Double,
    "Bool": ua.VariantType.Boolean,
    "Int": ua.VariantType.Int16,
    "DInt": ua.VariantType.Int32,
}

# Structures (équivalent des UDT TIA Portal) et valeur initiale de chaque membre
STRUCTS: dict[str, list[tuple[str, str]]] = {
    "moteur": [("Courant", "Real"), ("Vitesse", "Real"), ("Marche", "Bool"), ("Defaut", "Bool"), ("Heures", "DInt")],
    "compteur_energie": [("Energie_kWh", "LReal"), ("Puissance_kW", "Real")],
    "ligne": [("Marche", "Bool"), ("Tonnage_t", "LReal"), ("Debit_th", "Real"), ("Code_arret", "Int")],
    "mesure": [("Valeur", "Real")],
}


@dataclass
class Var:
    nodeid: ua.NodeId
    vtype: ua.VariantType
    value: float | bool | int


@dataclass
class Instance:
    name: str
    struct: str
    db: str = ""
    vars: dict[str, Var] = field(default_factory=dict)
    state: dict = field(default_factory=dict)


# Règles réglables à chaud, par structure : (clé, libellé, unité, défaut, min, max)
RULES: dict[str, list[tuple[str, str, str, float, float, float]]] = {
    "moteur": [("arrets_par_h", "Arrêts aléatoires par moteur", "/h", 1.8, 0, 60),
               ("part_defaut_pct", "Part des arrêts sur défaut", "%", 30, 0, 100),
               ("drift_ratio_pct", "Moteurs qui dérivent", "%", 5, 0, 100)],
    "compteur_energie": [("bruit_pct", "Bruit de puissance", "%", 2, 0, 50)],
    "ligne": [("mtbf_min", "Temps moyen entre arrêts", "min", 16.7, 1, 1440),
              ("mttr_min", "Durée moyenne d'arrêt", "min", 1.7, 0.1, 240)],
    "mesure": [("change_pct", "Tags qui changent à chaque cycle", "%", 30, 0, 100)],
}
GLOBAL_RULES = [("update_interval_s", "Période de mise à jour", "s", 1.0, 0.2, 10),
                ("time_acceleration", "Accélération du temps (dérives)", "×", 60, 1, 3600)]

# Événements injectables : type -> (libellé, paramètres par défaut)
EVENTS = {
    "defaut_moteur": ("Défaut moteur", {"cible": "", "minutes": 5}),
    "arret_ligne": ("Arrêt de ligne", {"cible": "", "minutes": 20, "code": 12}),
    "micro_arrets": ("Micro-arrêts", {"cible": "", "nombre": 6, "secondes": 40, "minutes": 10}),
    "derive_moteur": ("Dérive moteur", {"cible": "", "pct": 15, "minutes": 60}),
    "pic_energie": ("Pic de consommation", {"pct": 45, "minutes": 10}),
    "coupure": ("Perte de communication", {"secondes": 60}),
    "rafale": ("Rafale de valeurs", {"facteur": 3, "minutes": 2}),
}


class Plant:
    """Modèle de comportement : fait évoluer les valeurs à chaque cycle."""

    def __init__(self, cfg: dict, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.instances: list[Instance] = []
        self.changed: list[Var] = []
        self.recipes: list = []
        self.rules: dict[str, dict[str, float]] = {}     # DB -> règle -> valeur
        self.glob: dict[str, float] = {k: float(cfg.get(k, d)) for k, _, _, d, _, _ in GLOBAL_RULES}
        self.dbs: dict[str, dict] = {}                   # DB -> {struct, instances, tags}
        self.events: dict[int, dict] = {}
        self.ids = itertools.count(1)
        self.rates: dict[str, float] = {}                # DB -> valeurs modifiées par seconde
        self.outage_until = 0.0
        self.started = time.time()

    # --- règles -------------------------------------------------------------------------
    def register_db(self, db: dict, n: int):
        name = db["name"]
        rules = {k: float(dflt) for k, _, _, dflt, _, _ in RULES[db["struct"]]}
        if db["struct"] == "moteur":
            rules["drift_ratio_pct"] = float(db.get("drift_ratio", 0.05)) * 100
        if db["struct"] == "mesure":
            rules["change_pct"] = float(db.get("change_ratio", 0.3)) * 100
        self.rules[name] = rules
        self.dbs[name] = {"struct": db["struct"], "instances": n, "tags": n * len(STRUCTS[db["struct"]])}

    def set_rules(self, glob: dict | None, dbs: dict | None):
        for k, _, _, _, lo, hi in GLOBAL_RULES:
            if glob and k in glob:
                self.glob[k] = min(hi, max(lo, float(glob[k])))
        for name, vals in (dbs or {}).items():
            if name not in self.rules:
                continue
            spec = {k: (lo, hi) for k, _, _, _, lo, hi in RULES[self.dbs[name]["struct"]]}
            for k, v in vals.items():
                if k in spec:
                    lo, hi = spec[k]
                    self.rules[name][k] = min(hi, max(lo, float(v)))
            if "drift_ratio_pct" in vals:
                for inst in self.instances:
                    if inst.db == name and inst.struct == "moteur":
                        drifting = self.rng.random() < self.rules[name]["drift_ratio_pct"] / 100
                        inst.state["drift_per_h"] = self.rng.uniform(0.02, 0.08) if drifting else 0.0
                        inst.state["t0"] = time.monotonic()

    def snapshot_rules(self) -> dict:
        return {"global": dict(self.glob), "db": {k: dict(v) for k, v in self.rules.items()}}

    # --- événements ---------------------------------------------------------------------
    def find(self, name: str, struct: str) -> Instance:
        for inst in self.instances:
            if inst.name == name and inst.struct == struct:
                return inst
        raise ValueError(f"instance {struct} « {name} » introuvable")

    def add_event(self, kind: str, params: dict) -> dict:
        if kind not in EVENTS:
            raise ValueError(f"événement inconnu : {kind}")
        p = {**EVENTS[kind][1], **(params or {})}
        now = time.time()
        ev = {"id": next(self.ids), "type": kind, "libelle": EVENTS[kind][0], "params": p, "debut": now}
        if kind == "defaut_moteur":
            self.find(p["cible"], "moteur")
            ev["fin"] = now + float(p["minutes"]) * 60
        elif kind == "arret_ligne":
            self.find(p["cible"], "ligne")
            ev["fin"] = now + float(p["minutes"]) * 60
        elif kind == "micro_arrets":
            self.find(p["cible"], "ligne")
            span = float(p["minutes"]) * 60
            n = max(1, int(p["nombre"]))
            ev["fenetres"] = sorted((now + i * span / n + self.rng.uniform(0, span / n / 2),) for i in range(n))
            ev["fenetres"] = [(s, s + float(p["secondes"])) for (s,) in ev["fenetres"]]
            ev["fin"] = now + span
        elif kind == "derive_moteur":
            self.find(p["cible"], "moteur")
            ev["fin"] = None          # garde la dérive atteinte jusqu'à l'arrêt de l'événement
        elif kind == "pic_energie":
            ev["fin"] = now + float(p["minutes"]) * 60
        elif kind == "coupure":
            ev["fin"] = now + float(p["secondes"])
            self.outage_until = ev["fin"]
        elif kind == "rafale":
            ev["fin"] = now + float(p["minutes"]) * 60
        self.events[ev["id"]] = ev
        log.info("événement injecté : %s %s", kind, p)
        return ev

    def stop_event(self, eid: int) -> bool:
        ev = self.events.pop(eid, None)
        if ev and ev["type"] == "coupure":
            self.outage_until = 0.0
        return ev is not None

    def _effects(self, now: float) -> dict:
        """Effets des événements en cours sur ce cycle."""
        fx = {"forced_motor": {}, "forced_line": {}, "drift": {}, "energy": 1.0, "burst": 1.0}
        for eid, ev in list(self.events.items()):
            if ev["fin"] is not None and now >= ev["fin"]:
                del self.events[eid]
                continue
            p = ev["params"]
            if ev["type"] == "defaut_moteur":
                fx["forced_motor"][p["cible"]] = True
            elif ev["type"] == "arret_ligne":
                fx["forced_line"][p["cible"]] = int(p.get("code", 12))
            elif ev["type"] == "micro_arrets":
                if any(s <= now < e for s, e in ev["fenetres"]):
                    fx["forced_line"][p["cible"]] = 99
            elif ev["type"] == "derive_moteur":
                ramp = min(1.0, (now - ev["debut"]) / max(1.0, float(p["minutes"]) * 60))
                fx["drift"][p["cible"]] = 1 + float(p["pct"]) / 100 * ramp
            elif ev["type"] == "pic_energie":
                fx["energy"] *= 1 + float(p["pct"]) / 100
            elif ev["type"] == "rafale":
                fx["burst"] *= float(p["facteur"])
        return fx

    def state(self) -> dict:
        motors = [i.name for i in self.instances if i.struct == "moteur"]
        lines = [i.name for i in self.instances if i.struct == "ligne"]
        return {
            "demarre": self.started,
            "outage": time.time() < self.outage_until,
            "regles": self.snapshot_rules(),
            "specs": {"global": [dict(zip(("cle", "libelle", "unite", "defaut", "min", "max"), r)) for r in GLOBAL_RULES],
                      "struct": {k: [dict(zip(("cle", "libelle", "unite", "defaut", "min", "max"), r)) for r in v]
                                 for k, v in RULES.items()}},
            "dbs": [{"nom": k, **v, "valeurs_s": round(self.rates.get(k, 0.0), 1)} for k, v in self.dbs.items()],
            "evenements": [{k: v for k, v in e.items() if k != "fenetres"} for e in self.events.values()],
            "types_evenements": {k: {"libelle": v[0], "params": v[1]} for k, v in EVENTS.items()},
            "cibles": {"moteur": motors, "ligne": lines},
        }

    def set(self, inst: Instance, member: str, value):
        var = inst.vars[member]
        if var.vtype in (ua.VariantType.Float, ua.VariantType.Double):
            value = float(value)
        elif var.vtype == ua.VariantType.Boolean:
            value = bool(value)
        else:
            value = int(value)
        if value != var.value:
            var.value = value
            self.changed.append(var)

    def init_state(self, inst: Instance, db: dict):  # noqa: C901
        r = self.rng
        if inst.struct == "moteur":
            inst.state["nominal"] = r.uniform(8, 90)
            # une petite part des moteurs dérive lentement, pour tester le suivi des dérives
            drifting = r.random() < db.get("drift_ratio", 0.05)
            inst.state["drift_per_h"] = r.uniform(0.02, 0.08) if drifting else 0.0
            inst.state["t0"] = time.monotonic()
            inst.vars["Marche"].value = True
        elif inst.struct == "compteur_energie":
            inst.state["base_kw"] = r.uniform(20, 400)
            inst.vars["Energie_kWh"].value = r.uniform(1e4, 1e6)
        elif inst.struct == "ligne":
            inst.state["debit"] = r.uniform(50, 300)
            inst.vars["Marche"].value = True
        elif inst.struct == "mesure":
            inst.vars["Valeur"].value = round(r.uniform(0, 100), 2)
            inst.state["p"] = db.get("change_ratio", 0.3)

    def step(self, dt: float):
        r = self.rng
        now = time.time()
        fx = self._effects(now)
        before = len(self.changed)
        counts: dict[str, int] = {}
        for inst in self.instances:
            s = inst.state
            v = inst.vars
            rules = self.rules.get(inst.db, {})
            n0 = len(self.changed)
            if inst.struct == "moteur":
                running = v["Marche"].value
                if inst.name in fx["forced_motor"]:
                    running = False
                    self.set(inst, "Defaut", True)
                    s["forced"] = True
                elif s.pop("forced", False):
                    running = True
                    self.set(inst, "Defaut", False)
                elif running and r.random() < rules.get("arrets_par_h", 1.8) * dt / 3600.0:
                    running = False
                    self.set(inst, "Defaut", r.random() < rules.get("part_defaut_pct", 30) / 100)
                elif not running and r.random() < 0.02:
                    running = True
                    self.set(inst, "Defaut", False)
                self.set(inst, "Marche", running)
                if running:
                    hours = (time.monotonic() - s["t0"]) / 3600.0 * self.glob["time_acceleration"]
                    drift = (1.0 + s["drift_per_h"] * hours) * fx["drift"].get(inst.name, 1.0)
                    current = s["nominal"] * drift * (1 + r.gauss(0, 0.01))
                    self.set(inst, "Courant", round(current, 2))
                    self.set(inst, "Vitesse", round(1480 + r.gauss(0, 2), 1))
                else:
                    self.set(inst, "Courant", 0.0)
                    self.set(inst, "Vitesse", 0.0)
                if running and r.random() < dt / 3600.0 * 60:
                    self.set(inst, "Heures", v["Heures"].value + 1)
            elif inst.struct == "compteur_energie":
                kw = s["base_kw"] * fx["energy"] * (1 + 0.1 * math.sin(now / 600) + r.gauss(0, rules.get("bruit_pct", 2) / 100))
                self.set(inst, "Puissance_kW", round(kw, 1))
                self.set(inst, "Energie_kWh", round(v["Energie_kWh"].value + kw * dt / 3600.0, 3))
            elif inst.struct == "ligne":
                running = v["Marche"].value
                forced = fx["forced_line"].get(inst.name)
                if forced is not None:
                    running = False
                    self.set(inst, "Code_arret", forced)
                    s["forced"] = True
                elif s.pop("forced", False):
                    running = True
                    self.set(inst, "Code_arret", 0)
                elif running and r.random() < dt / (rules.get("mtbf_min", 16.7) * 60):
                    running = False
                    self.set(inst, "Code_arret", r.randint(1, 12))
                elif not running and r.random() < dt / (rules.get("mttr_min", 1.7) * 60):
                    running = True
                    self.set(inst, "Code_arret", 0)
                self.set(inst, "Marche", running)
                debit = s["debit"] * (1 + r.gauss(0, 0.02)) if running else 0.0
                self.set(inst, "Debit_th", round(debit, 1))
                self.set(inst, "Tonnage_t", round(v["Tonnage_t"].value + debit * dt / 3600.0, 3))
            elif inst.struct == "mesure":
                if r.random() < min(1.0, rules.get("change_pct", s["p"] * 100) / 100 * fx["burst"]):
                    self.set(inst, "Valeur", round(v["Valeur"].value + r.gauss(0, 0.5), 2))
            counts[inst.db] = counts.get(inst.db, 0) + len(self.changed) - n0
        for db, c in counts.items():   # moyenne glissante des valeurs modifiées par seconde
            self.rates[db] = 0.9 * self.rates.get(db, c / dt) + 0.1 * c / dt
        return len(self.changed) - before


async def build(server: Server, cfg: dict, plant: Plant) -> int:
    # Les index de namespace d'un vrai S7-1500 placent les DB en ns=3 : on réserve ns=2.
    await server.register_namespace("urn:mes-v3:simulator")
    ns = await server.register_namespace(SIEMENS_NS)
    plc = await server.nodes.objects.add_object(ua.NodeId(cfg.get("plc_name", "PLC_1"), ns), cfg.get("plc_name", "PLC_1"))
    dbg = await plc.add_folder(ua.NodeId("DataBlocksGlobal", ns), "DataBlocksGlobal")

    total_target = cfg.get("total_tags")
    fixed = sum(db["count"] * len(STRUCTS[db["struct"]]) for db in cfg["dbs"] if not db.get("fill"))
    count = 0
    for db in cfg["dbs"]:
        struct = STRUCTS[db["struct"]]
        n = db["count"]
        if db.get("fill") and total_target:
            n = max(0, (total_target - fixed) // len(struct))
        # Un DB trop gros est découpé (DB_Process_01, _02...) comme on le ferait dans TIA Portal :
        # cela garde aussi la construction de l'espace d'adresses rapide.
        per_db = int(db.get("max_per_db", 500))
        chunks = max(1, math.ceil(n / per_db))
        db_nodes = {}
        for i in range(n):
            c = i // per_db
            dbname = db["name"] if chunks == 1 else f'{db["name"]}_{c + 1:02d}'
            if dbname not in db_nodes:
                db_nodes[dbname] = await dbg.add_object(ua.NodeId(f'"{dbname}"', ns), dbname)
            db_node = db_nodes[dbname]
            iname = f'{db.get("prefix", "I")}{db.get("start", 1) + i}'
            obj = await db_node.add_object(ua.NodeId(f'"{dbname}"."{iname}"', ns), iname)
            inst = Instance(iname, db["struct"], db["name"])
            for member, dtype in struct:
                nid = ua.NodeId(f'"{dbname}"."{iname}"."{member}"', ns)
                vtype = VARIANT[dtype]
                init = False if vtype == ua.VariantType.Boolean else 0
                node = await obj.add_variable(nid, member, ua.Variant(init, vtype))
                inst.vars[member] = Var(node.nodeid, vtype, init)
                count += 1
            plant.init_state(inst, db)
            plant.changed.extend(inst.vars.values())  # publie les valeurs initiales (Marche = vrai...)
            plant.instances.append(inst)
        plant.register_db(db, n)
    for rc in cfg.get("recettes", []):
        count += await build_recipe_db(dbg, ns, rc, plant)
    return count


class RecipeDB:
    """DB de recette avec échange contrôlé, comme le bloc FB_ChargementRecette fourni pour TIA Portal.

    Demande à 1 : l'automate contrôle le tampon puis copie Tampon -> Actif et répond 1, ou refuse
    (Reponse = 2, Code = motif). Le MES remet Demande à 0 après lecture de la réponse.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.buffer: dict[str, object] = {}
        self.active: dict[str, object] = {}
        self.types: dict[str, ua.VariantType] = {}
        self.request = self.answer = self.code = None
        self.handled = False


async def build_recipe_db(dbg, ns: int, rc: dict, plant: "Plant") -> int:
    name = rc["db"]
    rdb = RecipeDB(rc)
    db_node = await dbg.add_object(ua.NodeId(f'"{name}"', ns), name)
    buf = await db_node.add_object(ua.NodeId(f'"{name}"."Tampon"', ns), "Tampon")
    act = await db_node.add_object(ua.NodeId(f'"{name}"."Actif"', ns), "Actif")
    n = 0
    for prm in rc["parametres"]:
        vt = VARIANT[prm.get("datatype", "Real")]
        val = prm.get("defaut", 0)
        val = int(val) if vt not in (ua.VariantType.Float, ua.VariantType.Double) else float(val)
        b = await buf.add_variable(ua.NodeId(f'"{name}"."Tampon"."{prm["nom"]}"', ns), prm["nom"], ua.Variant(val, vt))
        await b.set_writable()
        a = await act.add_variable(ua.NodeId(f'"{name}"."Actif"."{prm["nom"]}"', ns), prm["nom"], ua.Variant(val, vt))
        rdb.buffer[prm["nom"]] = b
        rdb.active[prm["nom"]] = a
        rdb.types[prm["nom"]] = vt
        n += 2
    rdb.request = await db_node.add_variable(ua.NodeId(f'"{name}"."Demande"', ns), "Demande", ua.Variant(False, ua.VariantType.Boolean))
    rdb.answer = await db_node.add_variable(ua.NodeId(f'"{name}"."Reponse"', ns), "Reponse", ua.Variant(0, ua.VariantType.Int16))
    rdb.code = await db_node.add_variable(ua.NodeId(f'"{name}"."Code"', ns), "Code", ua.Variant(0, ua.VariantType.Int16))
    for v in (rdb.request, rdb.answer):
        await v.set_writable()
    plant.recipes.append(rdb)
    return n + 3


async def recipe_step(rdb: RecipeDB) -> None:
    req = await rdb.request.read_value()
    if not req:
        rdb.handled = False
        return
    if rdb.handled:
        return
    rdb.handled = True
    values = {k: await node.read_value() for k, node in rdb.buffer.items()}
    # Contrôle automate simulé : les bornes « automate » du fichier de configuration
    for prm in rdb.cfg["parametres"]:
        lim = prm.get("max_automate")
        if lim is not None and values[prm["nom"]] > lim:
            await rdb.code.write_value(ua.Variant(2, ua.VariantType.Int16))
            await rdb.answer.write_value(ua.Variant(2, ua.VariantType.Int16))
            log.info("recette refusée : %s = %s > %s", prm["nom"], values[prm["nom"]], lim)
            return
    for k, v in values.items():
        await rdb.active[k].write_value(ua.Variant(v, rdb.types[k]))
    await rdb.code.write_value(ua.Variant(0, ua.VariantType.Int16))
    await rdb.answer.write_value(ua.Variant(1, ua.VariantType.Int16))
    log.info("recette acceptée : %s", values)


# ----------------------------------------------------------------------------- API de contrôle
class Control:
    """API HTTP JSON minimale (bibliothèque standard) pour la console d'exploitation."""

    def __init__(self, plant: Plant, state_dir: str):
        self.plant = plant
        self.path = os.path.join(state_dir, "regles.json") if state_dir else ""

    def load(self):
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    saved = json.load(f)
                self.plant.set_rules(saved.get("global"), saved.get("db"))
                log.info("règles de simulation rechargées depuis %s", self.path)
            except (OSError, ValueError) as e:
                log.warning("règles enregistrées illisibles (%s) : valeurs du scénario", e)

    def save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.plant.snapshot_rules(), f, indent=2)
        os.replace(tmp, self.path)

    def route(self, method: str, path: str, body: dict) -> tuple[int, object]:
        p = self.plant
        if method == "GET" and path == "/etat":
            return 200, p.state()
        if method == "PUT" and path == "/regles":
            if body.get("defaut"):
                p.rules.clear()
                for name, info in list(p.dbs.items()):
                    db = next(d for d in p.cfg["dbs"] if d["name"] == name)
                    p.register_db(db, info["instances"])
                p.glob = {k: float(p.cfg.get(k, d)) for k, _, _, d, _, _ in GLOBAL_RULES}
                p.set_rules(None, {n: {"drift_ratio_pct": r["drift_ratio_pct"]} for n, r in p.rules.items()
                                   if "drift_ratio_pct" in r})
            else:
                p.set_rules(body.get("global"), body.get("db"))
            self.save()
            log.info("règles de simulation modifiées")
            return 200, p.snapshot_rules()
        if method == "POST" and path == "/evenements":
            try:
                return 201, {k: v for k, v in p.add_event(body.get("type", ""), body.get("params") or {}).items()
                             if k != "fenetres"}
            except (ValueError, TypeError, KeyError) as e:
                return 400, {"detail": str(e)}
        if method == "DELETE" and path.startswith("/evenements/"):
            try:
                eid = int(path.rsplit("/", 1)[1])
            except ValueError:
                return 400, {"detail": "identifiant invalide"}
            return (200, {"ok": True}) if p.stop_event(eid) else (404, {"detail": "événement inconnu"})
        return 404, {"detail": "route inconnue"}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        status, payload = 500, {"detail": "erreur interne"}
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, path, _ = lines[0].split(" ", 2)
            headers = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines[1:] if ":" in ln)}
            n = int(headers.get("content-length", "0") or 0)
            if n > 1_000_000:
                raise ValueError("requête trop grande")
            raw = await reader.readexactly(n) if n else b""
            body = json.loads(raw) if raw else {}
            status, payload = self.route(method, path.split("?", 1)[0], body if isinstance(body, dict) else {})
        except (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError) as e:
            status, payload = 400, {"detail": str(e)}
        except Exception as e:  # noqa: BLE001
            log.exception("API de contrôle : %s", e)
        data = json.dumps(payload).encode()
        reason = {200: "OK", 201: "Created", 400: "Bad Request", 404: "Not Found"}.get(status, "Error")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
        try:
            await writer.drain()
        finally:
            writer.close()


async def publish_quality(server: Server, plant: Plant, bad: bool) -> None:
    """Coupure simulée : toutes les valeurs passent en qualité mauvaise, puis sont republiées."""
    now = datetime.now(timezone.utc)
    status = ua.StatusCode(ua.StatusCodes.BadCommunicationError if bad else ua.StatusCodes.Good)
    for inst in plant.instances:
        for var in inst.vars.values():
            dv = ua.DataValue(ua.Variant(var.value, var.vtype), StatusCode=status, SourceTimestamp=now, ServerTimestamp=now)
            await server.write_attribute_value(var.nodeid, dv)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=os.environ.get("SIM_CONFIG", "/app/config/site-demo.yaml"))
    p.add_argument("--endpoint", default=os.environ.get("SIM_ENDPOINT", "opc.tcp://0.0.0.0:4840"))
    p.add_argument("--control-port", type=int, default=int(os.environ.get("SIM_CONTROL_PORT", "8081")))
    args = p.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("SIM_TOTAL_TAGS"):
        cfg["total_tags"] = int(os.environ["SIM_TOTAL_TAGS"])
    if os.environ.get("SIM_CHANGE_RATIO"):
        for db in cfg["dbs"]:
            if db["struct"] == "mesure":
                db["change_ratio"] = float(os.environ["SIM_CHANGE_RATIO"])

    server = Server()
    await server.init()
    server.set_endpoint(args.endpoint)
    server.set_server_name("MES v3 - simulateur S7-1500")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    plant = Plant(cfg, random.Random(cfg.get("seed", 42)))
    n = await build(server, cfg, plant)
    log.info("%d variables créées", n)

    control = Control(plant, os.environ.get("SIM_STATE_DIR", "/data"))
    control.load()
    http = await asyncio.start_server(control.handle, "0.0.0.0", args.control_port)
    log.info("API de contrôle sur le port %d", args.control_port)

    async with server, http:
        log.info("Serveur OPC UA démarré sur %s", args.endpoint)
        nxt = time.monotonic()
        in_outage = False
        while True:
            period = plant.glob["update_interval_s"]
            plant.step(period)   # le premier tour publie aussi les valeurs initiales
            now = datetime.now(timezone.utc)
            outage = time.time() < plant.outage_until
            if outage and not in_outage:
                await publish_quality(server, plant, bad=True)
            elif in_outage and not outage:
                await publish_quality(server, plant, bad=False)
            in_outage = outage
            for var in ([] if outage else plant.changed):
                dv = ua.DataValue(ua.Variant(var.value, var.vtype), SourceTimestamp=now, ServerTimestamp=now)
                await server.write_attribute_value(var.nodeid, dv)
            for rdb in plant.recipes:
                await recipe_step(rdb)
            log.debug("%d valeurs modifiées", len(plant.changed))
            plant.changed.clear()
            nxt += period
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))


if __name__ == "__main__":
    asyncio.run(main())
