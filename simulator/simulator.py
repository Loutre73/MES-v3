"""Simulateur d'automate S7-1500 exposant un serveur OPC UA.

Reproduit l'arborescence d'un S7-1500 (PLC_1 / DataBlocksGlobal / <DB> / <instance> / <membre>)
et des NodeId au format Siemens : ns=3;s="DB"."Instance"."Membre".
Les comportements (moteurs, compteurs d'énergie, lignes, mesures de process) sont décrits
dans un fichier YAML, ce qui permet de simuler un site différent sans toucher au code.

Les règles (fréquence des arrêts, dérive des moteurs, bruit...) se modifient à chaud depuis la
console d'exploitation, et des scénarios peuvent être injectés (panne de ligne, coupure OPC UA...)
par le port de contrôle interne (8090, non publié). Les règles modifiées sont conservées dans
SIM_DATA (volume) et rechargées au démarrage.

Le temps simulé avance à la vitesse choisie (×1 à ×3600, 0 = pause) : arrêts, compteurs, dérives
et durées des scénarios suivent l'horloge simulée ; les horodatages OPC UA restent l'heure réelle.
Les scénarios (groupes de variables, règles à effets sur une frise) sont décrits dans scenarios.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import re
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from asyncua import Server, ua

from scenarios import Engine

log = logging.getLogger("simulator")

SIEMENS_NS = "http://www.siemens.com/simatic-s7-opcua"
GOOD = ua.StatusCode(ua.StatusCodes.Good)
BAD = ua.StatusCode(ua.StatusCodes.BadSensorFailure)   # effet « Qualité mauvaise »

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
    value: float | bool | int          # valeur publiée (après les effets des scénarios)
    raw: float | bool | int | None = None   # valeur calculée par le comportement de l'instance
    bad: bool = False                  # qualité OPC UA mauvaise (effet « Qualité »)

    def __post_init__(self):
        if self.raw is None:
            self.raw = self.value


# Règles réglables par DB (valeurs par défaut = comportement d'origine du simulateur)
DEFAULT_RULES: dict[str, dict[str, float]] = {
    "moteur": {"arrets_h": 1.8, "duree_arret_s": 50, "part_defaut": 0.3, "derive_ratio": 0.05, "bruit_pct": 1.0},
    "ligne": {"arrets_h": 3.6, "duree_arret_s": 100, "cadence_pct": 100, "bruit_pct": 2.0},
    "compteur_energie": {"amplitude_pct": 10, "bruit_pct": 2.0},
    "mesure": {"change_ratio": 0.3, "ecart": 0.5},
}
# bornes acceptées pour chaque règle
RULE_LIMITS = {"arrets_h": (0, 120), "duree_arret_s": (5, 3600), "part_defaut": (0, 1), "derive_ratio": (0, 1),
               "bruit_pct": (0, 20), "cadence_pct": (10, 150), "amplitude_pct": (0, 100), "change_ratio": (0, 1),
               "ecart": (0, 10)}
# vitesse : facteur du temps simulé (0 = pause) ; time_acceleration : vieillissement des moteurs
# (dérive, heures de marche) par heure simulée
GLOBAL_LIMITS = {"vitesse": (0, 3600), "time_acceleration": (1, 3600), "update_interval_s": (0.2, 10)}


@dataclass
class Instance:
    name: str
    struct: str
    db: str = ""
    vars: dict[str, Var] = field(default_factory=dict)
    state: dict = field(default_factory=dict)


class Plant:
    """Modèle de comportement : fait évoluer les valeurs à chaque cycle."""

    def __init__(self, cfg: dict, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.instances: list[Instance] = []
        self.changed: list[Var] = []
        self.recipes: list = []
        self.rules: dict[str, dict] = {}          # DB -> règles
        self.structs: dict[str, str] = {}         # DB -> struct
        self.counts: dict[str, int] = {}          # DB -> instances
        self.by_name: dict[str, Instance] = {}
        self.tag_index: list[tuple[str, Var]] = []
        self.clock = time.time()                  # horloge simulée (secondes epoch)
        self.clock0 = self.clock
        self.engine = Engine()
        self.coupure = False                      # coupure OPC UA demandée par un scénario
        self._touched: dict[int, Var] = {}
        self._overridden: dict[int, Var] = {}

    def meter_line(self, inst: Instance) -> Instance | None:
        """Ligne d'un compteur, même convention que la démo (mes_api.cli) : les compteurs sont
        répartis à tour de rôle sur les lignes, 4 par ligne ; les suivants sont des utilités."""
        if "line" not in inst.state:
            lines = sorted((i for i in self.instances if i.struct == "ligne"), key=lambda i: i.name)
            meters = sorted((i for i in self.instances if i.struct == "compteur_energie"), key=lambda i: i.name)
            k = meters.index(inst)
            inst.state["line"] = lines[k % len(lines)] if lines and k < 4 * len(lines) else None
        return inst.state["line"]

    # compteurs cumulés : un automate les garde à travers un redémarrage ; le simulateur les enregistre
    COUNTERS = {"compteur_energie": ("Energie_kWh",), "ligne": ("Tonnage_t",), "moteur": ("Heures",)}

    def counters(self) -> dict:
        return {f"{i.name}.{m}": i.vars[m].raw for i in self.instances for m in self.COUNTERS.get(i.struct, ()) if m in i.vars}

    def restore_counters(self, saved: dict) -> int:
        n = 0
        for i in self.instances:
            for m in self.COUNTERS.get(i.struct, ()):
                v = saved.get(f"{i.name}.{m}")
                if m in i.vars and isinstance(v, (int, float)):
                    var = i.vars[m]
                    var.raw = self.cast(var, v)
                    self._touched[id(var)] = var     # publié au premier cycle
                    n += 1
        return n

    def bind_engine(self, engine: Engine) -> None:
        self.engine = engine
        engine.bind(self.instances, {k: [m for m, _ in v] for k, v in STRUCTS.items()})

    @staticmethod
    def cast(var: Var, value):
        if var.vtype in (ua.VariantType.Float, ua.VariantType.Double):
            return float(value)
        if var.vtype == ua.VariantType.Boolean:
            return bool(value)
        return int(round(value))

    def set(self, inst: Instance, member: str, value):
        """Valeur brute calculée par le comportement ; publiée après les effets (finalize)."""
        var = inst.vars[member]
        var.raw = self.cast(var, value)
        self._touched[id(var)] = var

    def finalize(self, active) -> None:
        """Applique les effets des scénarios aux valeurs brutes et note ce qui change."""
        cands = {**self._touched, **self._overridden, **active.vars}
        for k, var in cands.items():
            effects = active.var.get(k)
            if effects:
                out, bad = self.engine.apply(var, var.raw, effects, self.rng)
                out = self.cast(var, out)
                if isinstance(out, float):
                    out = round(out, 3)
            else:
                out, bad = var.raw, False
            if out != var.value or bad != var.bad:
                var.value, var.bad = out, bad
                self.changed.append(var)
        self._touched = {}
        self._overridden = dict(active.vars)

    def init_state(self, inst: Instance, db: dict):
        r = self.rng
        if inst.struct == "moteur":
            inst.state["nominal"] = r.uniform(8, 90)
            # une petite part des moteurs dérive lentement, pour tester le suivi des dérives
            inst.state["u_drift"] = r.random()           # tirage fixe : la règle derive_ratio choisit les moteurs
            inst.state["drift_mag"] = r.uniform(0.02, 0.08)
            inst.vars["Marche"].value = inst.vars["Marche"].raw = True
        elif inst.struct == "compteur_energie":
            inst.state["base_kw"] = r.uniform(20, 400)
            inst.vars["Energie_kWh"].value = inst.vars["Energie_kWh"].raw = r.uniform(1e4, 1e6)
        elif inst.struct == "ligne":
            inst.state["debit"] = r.uniform(50, 300)
            inst.vars["Marche"].value = inst.vars["Marche"].raw = True
        elif inst.struct == "mesure":
            inst.vars["Valeur"].value = inst.vars["Valeur"].raw = round(r.uniform(0, 100), 2)

    def step(self, dt: float):
        """Un cycle : dt secondes réelles, soit dt × vitesse secondes simulées."""
        speed = float(self.cfg.get("vitesse", 1))
        if speed <= 0:            # pause : rien ne bouge, la coupure éventuelle reste en place
            return
        r = self.rng
        dt = dt * speed
        self.clock += dt
        accel = float(self.cfg.get("time_acceleration", 60))
        age_h = (self.clock - self.clock0) / 3600.0 * accel
        act = self.engine.compute(self.clock)
        self.coupure = act.coupure
        for inst in self.instances:
            s = inst.state
            v = inst.vars
            R = self.rules[inst.db]
            F = act.inst.get(inst.name) or {}
            if "aleas" in F:
                R = {**R, **F["aleas"]}
            forced = "defaut" in F or "arret" in F
            if inst.struct == "moteur":
                running = v["Marche"].raw
                if forced:
                    running = False
                    self.set(inst, "Defaut", "defaut" in F)
                elif s.pop("forced", False):
                    running = True    # fin du forçage : le moteur redémarre
                    self.set(inst, "Defaut", False)
                elif running and r.random() < R["arrets_h"] * dt / 3600.0:
                    running = False
                    self.set(inst, "Defaut", r.random() < R["part_defaut"])
                elif not running and r.random() < dt / max(R["duree_arret_s"], dt):
                    running = True
                    self.set(inst, "Defaut", False)
                if forced:
                    s["forced"] = True
                self.set(inst, "Marche", running)
                if running:
                    drift_per_h = s["drift_mag"] if s["u_drift"] < R["derive_ratio"] else 0.0
                    current = s["nominal"] * (1.0 + drift_per_h * age_h) * (1 + r.gauss(0, R["bruit_pct"] / 100.0))
                    self.set(inst, "Courant", round(current, 2))
                    self.set(inst, "Vitesse", round(1480 + r.gauss(0, 2), 1))
                else:
                    self.set(inst, "Courant", 0.0)
                    self.set(inst, "Vitesse", 0.0)
                if running:
                    # heures de marche (vieillissement accéléré) : tirage, ou ajout direct à grande vitesse
                    h = dt / 3600.0 * accel
                    inc = int(h) + (r.random() < h - int(h))
                    if inc:
                        self.set(inst, "Heures", v["Heures"].raw + inc)
            elif inst.struct == "compteur_energie":
                amp = R["amplitude_pct"] / 100.0
                kw = s["base_kw"] * (1 + amp * math.sin(self.clock / 600) + r.gauss(0, R["bruit_pct"] / 100.0))
                # compteur rattaché à une ligne : ligne à l'arrêt, il ne reste que la charge de veille
                ln = self.meter_line(inst)
                if ln is not None and not ln.vars["Marche"].raw:
                    kw *= 0.25
                self.set(inst, "Puissance_kW", round(kw, 1))
                # l'énergie intègre la puissance publiée (effets des scénarios compris)
                self.set(inst, "Energie_kWh", round(v["Energie_kWh"].raw + float(v["Puissance_kW"].value) * dt / 3600.0, 3))
            elif inst.struct == "ligne":
                running = v["Marche"].raw
                if forced:
                    running = False
                    p = F.get("defaut") or F.get("arret")
                    self.set(inst, "Code_arret", p.get("code", 1))
                elif s.pop("forced", False):
                    running = True    # fin du forçage : la ligne redémarre
                    self.set(inst, "Code_arret", 0)
                elif running and r.random() < R["arrets_h"] * dt / 3600.0:
                    running = False
                    self.set(inst, "Code_arret", r.randint(1, 12))
                elif not running and r.random() < dt / max(R["duree_arret_s"], dt):
                    running = True
                    self.set(inst, "Code_arret", 0)
                if forced:
                    s["forced"] = True
                self.set(inst, "Marche", running)
                debit = s["debit"] * R["cadence_pct"] / 100.0 * (1 + r.gauss(0, R["bruit_pct"] / 100.0)) if running else 0.0
                self.set(inst, "Debit_th", round(debit, 1))
                # le tonnage intègre le débit publié (effets des scénarios compris)
                self.set(inst, "Tonnage_t", round(v["Tonnage_t"].raw + float(v["Debit_th"].value) * dt / 3600.0, 3))
            elif inst.struct == "mesure":
                if r.random() < R["change_ratio"]:
                    self.set(inst, "Valeur", round(v["Valeur"].raw + r.gauss(0, R["ecart"]), 2))
        self.finalize(act)

    # --- contrôle depuis la console ------------------------------------------------------------
    def set_rules(self, body: dict) -> None:
        """Valide puis applique {"global": {...}, "dbs": {"DB_Moteurs": {"arrets_h": 3}}} ; tout ou rien."""
        glob = body.get("global") or {}
        dbs = body.get("dbs") or {}
        for k, v in glob.items():
            if k not in GLOBAL_LIMITS:
                raise ValueError(f"paramètre inconnu : {k}")
            lo, hi = GLOBAL_LIMITS[k]
            if not lo <= float(v) <= hi:
                raise ValueError(f"{k} hors bornes ({lo} à {hi})")
        for db, rules in dbs.items():
            if db not in self.rules:
                raise ValueError(f"DB inconnu : {db}")
            for k, v in rules.items():
                if k not in self.rules[db]:
                    raise ValueError(f"règle {k} inconnue pour {db}")
                lo, hi = RULE_LIMITS[k]
                if not lo <= float(v) <= hi:
                    raise ValueError(f"{db}.{k} hors bornes ({lo} à {hi})")
        for k, v in glob.items():
            self.cfg[k] = float(v)
        for db, rules in dbs.items():
            for k, v in rules.items():
                self.rules[db][k] = float(v)

    def state(self) -> dict:
        return {
            "global": {k: self.cfg.get(k, 1 if k == "vitesse" else None) for k in GLOBAL_LIMITS},
            "horloge": self.clock,
            "total_tags": len(self.tag_index),
            "dbs": [{"nom": db, "struct": self.structs[db], "instances": self.counts[db], "regles": self.rules[db],
                     "defauts": DEFAULT_RULES[self.structs[db]]} for db in self.rules],
            **self.engine.state(self.clock),
        }

    def search(self, q: str = "", names: list[str] | None = None, limit: int = 50) -> list[dict]:
        if names:
            wanted = set(names)
            hits = [(n, v) for n, v in self.tag_index if n in wanted]
        else:
            q = q.lower()
            hits = []
            for n, v in self.tag_index:
                if q in n.lower():
                    hits.append((n, v))
                    if len(hits) >= limit:
                        break
        return [{"nom": n, "valeur": v.value, **({"qualite": "mauvaise"} if v.bad else {})} for n, v in hits]


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
        base = db["name"]
        plant.structs[base] = db["struct"]
        plant.counts[base] = n
        rules = dict(DEFAULT_RULES[db["struct"]])
        for k in rules:   # valeurs du fichier de site (anciens noms acceptés)
            for key in (k, {"derive_ratio": "drift_ratio"}.get(k)):
                if key and key in db:
                    rules[k] = float(db[key])
        plant.rules[base] = rules
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
            inst = Instance(iname, db["struct"], base)
            for member, dtype in struct:
                nid = ua.NodeId(f'"{dbname}"."{iname}"."{member}"', ns)
                vtype = VARIANT[dtype]
                init = False if vtype == ua.VariantType.Boolean else 0
                node = await obj.add_variable(nid, member, ua.Variant(init, vtype))
                inst.vars[member] = Var(node.nodeid, vtype, init)
                plant.tag_index.append((f"{dbname}.{iname}.{member}", inst.vars[member]))
                count += 1
            plant.init_state(inst, db)
            plant.changed.extend(inst.vars.values())  # publie les valeurs initiales (Marche = vrai...)
            plant.instances.append(inst)
            plant.by_name[iname] = inst
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


class Control:
    """Port de contrôle interne (HTTP minimal, JSON) : état, règles, scénarios, valeurs des tags."""

    def __init__(self, plant: Plant, data_dir: str | None):
        self.plant = plant
        self.path = Path(data_dir) / "regles.json" if data_dir else None
        self.changes = 0
        self.rate = 0.0
        self._prev = (0, time.monotonic())
        self.opc_up = True

    def load(self) -> None:
        if self.path and self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                saved["dbs"] = {k: v for k, v in (saved.get("dbs") or {}).items() if k in self.plant.rules}
                self.plant.set_rules(saved)
                log.info("règles de simulation rechargées depuis %s", self.path)
            except (OSError, ValueError) as e:
                log.warning("règles enregistrées ignorées : %s", e)

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"global": {k: self.plant.cfg.get(k) for k in GLOBAL_LIMITS},
                                             "dbs": self.plant.rules}), encoding="utf-8")
        except OSError as e:
            log.warning("règles non enregistrées : %s", e)

    def load_counters(self) -> None:
        f = self.path.with_name("compteurs.json") if self.path else None
        if f and f.exists():
            try:
                n = self.plant.restore_counters(json.loads(f.read_text(encoding="utf-8")))
                log.info("%d compteurs repris là où ils en étaient", n)
            except (OSError, ValueError) as e:
                log.warning("compteurs enregistrés ignorés : %s", e)

    def save_counters(self) -> None:
        f = self.path.with_name("compteurs.json") if self.path else None
        if not f:
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.plant.counters()), encoding="utf-8")
            tmp.replace(f)
        except OSError as e:
            log.warning("compteurs non enregistrés : %s", e)

    def tick(self, n: int) -> None:
        self.changes += n
        now = time.monotonic()
        if now - self._prev[1] >= 5:
            self.rate = round((self.changes - self._prev[0]) / (now - self._prev[1]), 1)
            self._prev = (self.changes, now)

    async def handle(self, method: str, target: str, body: dict):
        from urllib.parse import parse_qs, urlsplit
        u = urlsplit(target)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = self.plant
        if method == "GET" and u.path == "/etat":
            return 200, {**p.state(), "changements_s": self.rate, "opcua": self.opc_up}
        if method == "GET" and u.path == "/tags":
            names = [n for n in q.get("noms", "").split(",") if n]
            return 200, p.search(q.get("q", ""), names, min(200, int(q.get("limite", "50"))))
        if method == "PUT" and u.path == "/regles":
            try:
                p.set_rules(body)
            except (ValueError, TypeError) as e:
                return 400, {"erreur": str(e)}
            self.save()
            return 200, p.state()
        eng = p.engine
        try:
            if method == "POST" and u.path == "/apercu":
                return 200, eng.preview(body)
            m = re.fullmatch(r"/groupes/([A-Za-z0-9_-]{1,40})", u.path)
            if m and method == "PUT":
                return 200, {"id": m.group(1), **eng.put_group(m.group(1), body)}
            if m and method == "DELETE":
                eng.delete_group(m.group(1))
                return 200, {"id": m.group(1), "supprime": True}
            m = re.fullmatch(r"/scenarios/([A-Za-z0-9_-]{1,40})", u.path)
            if m and method == "PUT":
                return 200, {"id": m.group(1), **eng.put_seq(m.group(1), body)}
            if m and method == "DELETE":
                eng.delete_seq(m.group(1))
                return 200, {"id": m.group(1), "supprime": True}
            m = re.fullmatch(r"/scenarios/([A-Za-z0-9_-]{1,40})/(lancer|arreter)", u.path)
            if m and method == "POST":
                sid, action = m.groups()
                if action == "lancer":
                    eng.start(sid, p.clock)
                else:
                    eng.stop(sid)
                return 200, {"id": sid, "nom": eng.seqs.get(sid, {}).get("nom", sid), "en_cours": sid in eng.running}
            if method == "POST" and u.path == "/scenarios-arreter":
                eng.stop()
                return 200, {"en_cours": False}
        except LookupError as e:
            return 404, {"erreur": str(e)}
        except (ValueError, TypeError) as e:
            return 400, {"erreur": str(e)}
        return 404, {"erreur": "introuvable"}


async def serve(port: int, handler):
    """Serveur HTTP minimal : une requête par connexion, corps JSON."""

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:] if h)}
            n = int(headers.get("content-length") or 0)
            if n > 100_000:
                raise ValueError("corps trop grand")
            raw = await asyncio.wait_for(reader.readexactly(n), 10) if n else b""
            status, payload = await handler(method, target, json.loads(raw) if raw else {})
        except (ValueError, json.JSONDecodeError) as e:
            status, payload = 400, {"erreur": str(e)}
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        except Exception as e:  # noqa: BLE001
            log.exception("requête de contrôle en erreur")
            status, payload = 500, {"erreur": str(e)}
        data = json.dumps(payload, ensure_ascii=False).encode()
        writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json; charset=utf-8\r\n"
                     f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
        try:
            await writer.drain()
        finally:
            writer.close()

    return await asyncio.start_server(client, "0.0.0.0", port)


SAVE_EVERY_S = float(os.environ.get("SIM_SAVE_EVERY_S", "30"))


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=os.environ.get("SIM_CONFIG", "/app/config/site-demo.yaml"))
    p.add_argument("--endpoint", default=os.environ.get("SIM_ENDPOINT", "opc.tcp://0.0.0.0:4840"))
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
    data_dir = os.environ.get("SIM_DATA", "/data")
    plant.bind_engine(Engine(data_dir))
    plant.engine.load()
    ctl = Control(plant, data_dir)
    ctl.load()
    ctl.load_counters()   # sinon les compteurs repartent en arrière et l'énergie est comptée deux fois
    await serve(int(os.environ.get("SIM_CONTROL_PORT", "8090")), ctl.handle)

    await server.start()
    log.info("Serveur OPC UA démarré sur %s", args.endpoint)
    # « docker stop » envoie SIGTERM : sans gestionnaire, le processus (PID 1) l'ignore et finit tué
    # sans avoir enregistré ses compteurs. On sort proprement de la boucle pour passer par `finally`.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    async def autosave():   # tâche à part : cadence indépendante de la durée d'un tour de boucle
        while True:
            await asyncio.sleep(SAVE_EVERY_S)
            ctl.save_counters()

    saver = asyncio.create_task(autosave())
    try:
        nxt = time.monotonic()
        while not stop.is_set():
            period = float(cfg.get("update_interval_s", 1.0))
            plant.step(period)   # le premier tour publie aussi les valeurs initiales
            # effet « Coupure OPC UA » : le serveur s'arrête puis redémarre (les clients se reconnectent)
            cut = plant.coupure
            if cut and ctl.opc_up:
                log.info("coupure OPC UA simulée")
                await server.stop()
                ctl.opc_up = False
            elif not cut and not ctl.opc_up:
                await server.start()
                ctl.opc_up = True
                log.info("serveur OPC UA rétabli")
            now = datetime.now(timezone.utc)
            for var in plant.changed:
                dv = ua.DataValue(ua.Variant(var.value, var.vtype), SourceTimestamp=now, ServerTimestamp=now,
                                  StatusCode=BAD if var.bad else GOOD)
                await server.write_attribute_value(var.nodeid, dv)
            if ctl.opc_up:
                for rdb in plant.recipes:
                    await recipe_step(rdb)
            ctl.tick(len(plant.changed))
            log.debug("%d valeurs modifiées", len(plant.changed))
            plant.changed.clear()
            nxt += period
            delay = nxt - time.monotonic()
            if delay < -5 * period:   # retard (coupure, changement de période) : on repart d'ici
                nxt = time.monotonic()
            try:
                await asyncio.wait_for(stop.wait(), max(0.0, delay))
            except asyncio.TimeoutError:
                pass
        log.info("arrêt demandé")
    finally:
        saver.cancel()
        ctl.save_counters()
        log.info("compteurs enregistrés")
        if ctl.opc_up:
            await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
