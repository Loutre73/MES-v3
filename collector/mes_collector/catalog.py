"""Lecture du catalogue de tags et amorçage par navigation OPC UA.

L'amorçage (lot 0) crée un tag par variable feuille trouvée sous BROWSE_ROOT, sans type de
machine. La découverte complète, qui reconnaît les instances par signature de structure,
arrive au lot 1 et remplira machine_id, member et role.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg
from asyncua import Client, ua

from .deadband import Deadband

log = logging.getLogger("catalog")

# DataType standard OPC UA (ns=0) -> nom ; seuls les types numériques et booléens sont historisés
NUMERIC_TYPES = {
    1: "Boolean", 2: "SByte", 3: "Byte", 4: "Int16", 5: "UInt16", 6: "Int32",
    7: "UInt32", 8: "Int64", 9: "UInt64", 10: "Float", 11: "Double",
}


@dataclass(frozen=True)
class TagDef:
    id: int
    node_id: str
    name: str
    data_type: str
    deadband: Deadband


async def load_tags(conn: asyncpg.Connection) -> dict[int, TagDef]:
    rows = await conn.fetch(
        """SELECT id, node_id, name, data_type, deadband_kind, deadband_value, eu_min, eu_max
           FROM catalog.tag WHERE enabled AND historize ORDER BY id"""
    )
    return {
        r["id"]: TagDef(r["id"], r["node_id"], r["name"], r["data_type"],
                        Deadband(r["deadband_kind"], r["deadband_value"], r["eu_min"], r["eu_max"]))
        for r in rows
    }


async def fingerprint(conn: asyncpg.Connection) -> tuple:
    r = await conn.fetchrow("SELECT count(*) AS n, max(updated_at) AS m FROM catalog.tag")
    return (r["n"], r["m"])


async def _browse_many(client: Client, node_ids: list[ua.NodeId]) -> list[list[ua.ReferenceDescription]]:
    """Navigation groupée (BrowseRequest de 500 nœuds) avec gestion des points de continuation."""
    out: list[list[ua.ReferenceDescription]] = []
    for i in range(0, len(node_ids), 500):
        chunk = node_ids[i:i + 500]
        params = ua.BrowseParameters()
        params.RequestedMaxReferencesPerNode = 0
        for nid in chunk:
            d = ua.BrowseDescription()
            d.NodeId = nid
            d.BrowseDirection = ua.BrowseDirection.Forward
            d.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HierarchicalReferences)
            d.IncludeSubtypes = True
            d.NodeClassMask = int(ua.NodeClass.Object | ua.NodeClass.Variable)
            d.ResultMask = int(ua.BrowseResultMask.All)
            params.NodesToBrowse.append(d)
        results = await client.uaclient.browse(params)
        for res in results:
            refs = list(res.References)
            cp = res.ContinuationPoint
            while cp:
                nxt = ua.BrowseNextParameters()
                nxt.ContinuationPoints = [cp]
                more = (await client.uaclient.browse_next(nxt))[0]
                refs.extend(more.References)
                cp = more.ContinuationPoint
            out.append(refs)
    return out


async def _find_root(client: Client, path: str) -> ua.NodeId:
    current = client.nodes.objects.nodeid
    for part in [p for p in path.split("/") if p]:
        refs = (await _browse_many(client, [current]))[0]
        match = [r for r in refs if r.BrowseName.Name == part]
        if not match:
            raise RuntimeError(f"chemin de navigation introuvable : {path} (élément {part})")
        current = match[0].NodeId
    return current


async def browse_leaves(client: Client, root_path: str) -> list[tuple[str, str]]:
    """Retourne (NodeId, chemin lisible) de toutes les variables feuilles sous root_path."""
    root = await _find_root(client, root_path)
    frontier: list[tuple[ua.NodeId, str, bool]] = [(root, "", False)]
    leaves: list[tuple[ua.NodeId, str]] = []
    while frontier:
        results = await _browse_many(client, [n for n, _, _ in frontier])
        nxt: list[tuple[ua.NodeId, str, bool]] = []
        for (node, path, is_var), refs in zip(frontier, results):
            children = [r for r in refs if r.NodeClass in (ua.NodeClass.Object, ua.NodeClass.Variable)]
            var_children = [r for r in children if r.NodeClass == ua.NodeClass.Variable]
            if is_var and not var_children:
                leaves.append((node, path))
                continue
            for r in children:
                # les propriétés (HasProperty) ne sont pas des membres de structure
                if r.ReferenceTypeId == ua.NodeId(ua.ObjectIds.HasProperty):
                    continue
                child_path = f"{path}.{r.BrowseName.Name}" if path else r.BrowseName.Name
                nxt.append((r.NodeId, child_path, r.NodeClass == ua.NodeClass.Variable))
        frontier = nxt
    log.info("%d variables trouvées sous %s", len(leaves), root_path)

    # Lecture groupée des DataType pour ne garder que les numériques et booléens
    out: list[tuple[str, str, str]] = []
    for i in range(0, len(leaves), 1000):
        chunk = leaves[i:i + 1000]
        params = ua.ReadParameters()
        for nid, _ in chunk:
            rv = ua.ReadValueId()
            rv.NodeId = nid
            rv.AttributeId = ua.AttributeIds.DataType
            params.NodesToRead.append(rv)
        values = await client.uaclient.read(params)
        for (nid, path), dv in zip(chunk, values):
            dt = dv.Value.Value if dv.Value is not None else None
            if isinstance(dt, ua.NodeId) and dt.NamespaceIndex == 0 and dt.Identifier in NUMERIC_TYPES:
                out.append((nid.to_string(), path, NUMERIC_TYPES[dt.Identifier]))
    return out


async def bootstrap(client: Client, conn: asyncpg.Connection, root_path: str) -> int:
    leaves = await browse_leaves(client, root_path)
    await conn.executemany(
        """INSERT INTO catalog.tag (node_id, name, data_type) VALUES ($1, $2, $3)
           ON CONFLICT (node_id) DO NOTHING""",
        leaves,
    )
    log.info("amorçage du catalogue : %d tags créés", len(leaves))
    return len(leaves)
