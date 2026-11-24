"""Simulateur d'automate S7-1500 exposant un serveur OPC UA.

Reproduit l'arborescence d'un S7-1500 (PLC_1 / DataBlocksGlobal / <DB> / <instance> / <membre>)
et des NodeId au format Siemens : ns=3;s="DB"."Instance"."Membre".
Les comportements (moteurs, compteurs d'énergie, lignes, mesures de process) sont décrits
dans un fichier YAML, ce qui permet de simuler un site différent sans toucher au code.
"""
from __future__ import annotations

import argparse
import asyncio
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
    vars: dict[str, Var] = field(default_factory=dict)
    state: dict = field(default_factory=dict)


class Plant:
    """Modèle de comportement : fait évoluer les valeurs à chaque cycle."""

    def __init__(self, cfg: dict, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.instances: list[Instance] = []
        self.changed: list[Var] = []

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

    def init_state(self, inst: Instance, db: dict):
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
        for inst in self.instances:
            s = inst.state
            v = inst.vars
            if inst.struct == "moteur":
                running = v["Marche"].value
                if running and r.random() < 0.0005:
                    running = False
                    self.set(inst, "Defaut", r.random() < 0.3)  # 30 % des arrêts sont sur défaut
                elif not running and r.random() < 0.02:
                    running = True
                    self.set(inst, "Defaut", False)
                self.set(inst, "Marche", running)
                if running:
                    hours = (time.monotonic() - s["t0"]) / 3600.0 * self.cfg.get("time_acceleration", 60)
                    drift = 1.0 + s["drift_per_h"] * hours
                    current = s["nominal"] * drift * (1 + r.gauss(0, 0.01))
                    self.set(inst, "Courant", round(current, 2))
                    self.set(inst, "Vitesse", round(1480 + r.gauss(0, 2), 1))
                else:
                    self.set(inst, "Courant", 0.0)
                    self.set(inst, "Vitesse", 0.0)
                if running and r.random() < dt / 3600.0 * 60:
                    self.set(inst, "Heures", v["Heures"].value + 1)
            elif inst.struct == "compteur_energie":
                kw = s["base_kw"] * (1 + 0.1 * math.sin(time.time() / 600) + r.gauss(0, 0.02))
                self.set(inst, "Puissance_kW", round(kw, 1))
                self.set(inst, "Energie_kWh", round(v["Energie_kWh"].value + kw * dt / 3600.0, 3))
            elif inst.struct == "ligne":
                running = v["Marche"].value
                if running and r.random() < 0.001:
                    running = False
                    self.set(inst, "Code_arret", r.randint(1, 12))
                elif not running and r.random() < 0.01:
                    running = True
                    self.set(inst, "Code_arret", 0)
                self.set(inst, "Marche", running)
                debit = s["debit"] * (1 + r.gauss(0, 0.02)) if running else 0.0
                self.set(inst, "Debit_th", round(debit, 1))
                self.set(inst, "Tonnage_t", round(v["Tonnage_t"].value + debit * dt / 3600.0, 3))
            elif inst.struct == "mesure":
                if r.random() < s["p"]:
                    self.set(inst, "Valeur", round(v["Valeur"].value + r.gauss(0, 0.5), 2))


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
            inst = Instance(iname, db["struct"])
            for member, dtype in struct:
                nid = ua.NodeId(f'"{dbname}"."{iname}"."{member}"', ns)
                vtype = VARIANT[dtype]
                init = False if vtype == ua.VariantType.Boolean else 0
                node = await obj.add_variable(nid, member, ua.Variant(init, vtype))
                inst.vars[member] = Var(node.nodeid, vtype, init)
                count += 1
            plant.init_state(inst, db)
            plant.instances.append(inst)
    return count


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

    period = float(cfg.get("update_interval_s", 1.0))
    async with server:
        log.info("Serveur OPC UA démarré sur %s", args.endpoint)
        nxt = time.monotonic()
        while True:
            plant.changed.clear()
            plant.step(period)
            now = datetime.now(timezone.utc)
            for var in plant.changed:
                dv = ua.DataValue(ua.Variant(var.value, var.vtype), SourceTimestamp=now, ServerTimestamp=now)
                await server.write_attribute_value(var.nodeid, dv)
            log.debug("%d valeurs modifiées", len(plant.changed))
            nxt += period
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))


if __name__ == "__main__":
    asyncio.run(main())
