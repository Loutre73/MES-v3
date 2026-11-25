"""Commandes d'exploitation : python -m mes_api.cli <commande>.

  decouvrir [--demo]   découverte automatique et ajout au catalogue de toutes les instances complètes
                       (--demo : attributs et recettes d'exemple pour le simulateur)
"""
from __future__ import annotations

import asyncio
import sys

from . import auth, db, discovery, opc, typedefs


async def demo_setup(conn) -> None:
    """Attributs que la découverte ne peut pas deviner : cadence des lignes, rattachement des compteurs."""
    lines = await conn.fetch(
        """SELECT m.id, m.name, m.base_node FROM catalog.machine m JOIN catalog.machine_type t ON t.id = m.type_id
           WHERE t.code = 'ligne' ORDER BY m.name""")
    nodes = [ln["base_node"] + '."Debit_th"' for ln in lines]
    try:
        async with opc.connect() as client:
            rates = await opc.read_values(client, nodes)
    except Exception:  # noqa: BLE001
        rates = [None] * len(lines)
    for ln, rate in zip(lines, rates):
        cadence = round((rate or 200) * 1.1, 0) if rate else 250.0
        await conn.execute("UPDATE catalog.machine SET attributes = attributes || $2 WHERE id = $1",
                           ln["id"], {"cadence_nominale_th": cadence})
    meters = await conn.fetch(
        """SELECT m.id FROM catalog.machine m JOIN catalog.machine_type t ON t.id = m.type_id
           WHERE t.code = 'compteur_energie' ORDER BY m.name""")
    usages = ["broyage", "convoyage", "ventilation", "pompage"]
    for i, m in enumerate(meters):
        attrs = {"usage": usages[i % len(usages)]}
        if lines and i < 4 * len(lines):
            attrs["ligne"] = lines[i % len(lines)]["name"]
        else:
            attrs["usage"] = "utilités"
        await conn.execute("UPDATE catalog.machine SET attributes = attributes || $2 WHERE id = $1", m["id"], attrs)
    # courant nominal des moteurs : la mesure actuelle (dans un vrai site, la plaque moteur)
    motors = await conn.fetch(
        """SELECT m.id, m.base_node FROM catalog.machine m JOIN catalog.machine_type t ON t.id = m.type_id
           WHERE t.code = 'moteur' AND NOT m.attributes ? 'courant_nominal' ORDER BY m.name""")
    try:
        async with opc.connect() as client:
            currents = await opc.read_values(client, [m["base_node"] + '."Courant"' for m in motors])
    except Exception:  # noqa: BLE001
        currents = [None] * len(motors)
    for m, c in zip(motors, currents):
        if not c:
            continue
        await conn.execute("UPDATE catalog.machine SET attributes = attributes || $2 WHERE id = $1",
                           m["id"], {"courant_nominal": round(float(c), 1)})
        await conn.execute(
            """INSERT INTO mes.motor_reference (machine_id, ref_current, method, set_by) VALUES ($1, $2, 'nominal', 'demo')
               ON CONFLICT (machine_id) DO NOTHING""", m["id"], round(float(c), 1))
    # recettes d'exemple
    rt = await conn.fetchrow("SELECT id, definition FROM mes.recipe_type WHERE code = 'ligne'")
    if rt and not await conn.fetchval("SELECT 1 FROM mes.recipe LIMIT 1"):
        base = {p["name"]: p["default"] for p in rt["definition"]["params"]}
        samples = [("GR-0-4", "Granulat 0/4", "0/4", {}), ("GR-4-10", "Granulat 4/10", "4/10", {"Vitesse_convoyeur": 70, "Debit_cible": 180}),
                   ("GR-10-20", "Granulat 10/20", "10/20", {"Vitesse_convoyeur": 75, "Temps_malaxage": 90, "Ouverture_trappe": 65})]
        for code, label, product, over in samples:
            rid = await conn.fetchval(
                "INSERT INTO mes.recipe (type_id, code, label, product, created_by) VALUES ($1, $2, $3, $4, 'demo') RETURNING id",
                rt["id"], code, label, product)
            await conn.execute(
                """INSERT INTO mes.recipe_version (recipe_id, version, "values", comment, created_by, status, validated_at, validated_by)
                   VALUES ($1, 1, $2, 'Recette de démonstration', 'demo', 'validee', now(), 'demo')""", rid, {**base, **over})


async def decouvrir(demo: bool) -> None:
    await db.open_pool()
    await typedefs.sync_machine_types()
    await typedefs.sync_recipe_types()
    await auth.ensure_admin()
    res = await discovery.run("mesctl")
    selections = [{"base_node": i["base_node"], "area": i["db"]} for i in res["instances"]
                  if i["status"] != "existant" and not i["missing"]]
    print(f"{len(res['instances'])} instances reconnues, {len(selections)} à ajouter, "
          f"{len(res['unknown'])} structures inconnues ({res['unknown_tags']} tags)")
    summary = await discovery.apply(res["id"], selections, include_unknown=True, disable_missing=False, actor="mesctl")
    print(summary)
    if demo:
        async with db.pool().acquire() as conn:
            await demo_setup(conn)
        print("Attributs et recettes de démonstration en place.")
    async with db.pool().acquire() as conn:
        n = await conn.fetchval("SELECT count(*) FROM catalog.tag WHERE enabled")
    print(f"{n} tags actifs dans le catalogue ; la collecte les prend en compte sous une minute.")
    await db.close_pool()


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in ("decouvrir",):
        print(__doc__)
        sys.exit(2)
    asyncio.run(decouvrir("--demo" in args))


if __name__ == "__main__":
    main()
