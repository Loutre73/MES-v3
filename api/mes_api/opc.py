"""Accès OPC UA côté API : navigation de l'automate, lecture et écriture ponctuelles."""
from __future__ import annotations

import contextlib
from dataclasses import dataclass

from asyncua import Client, ua

from .config import settings

NUMERIC_TYPES = {
    1: "Boolean", 2: "SByte", 3: "Byte", 4: "Int16", 5: "UInt16", 6: "Int32",
    7: "UInt32", 8: "Int64", 9: "UInt64", 10: "Float", 11: "Double",
}
VARIANT = {
    "Boolean": ua.VariantType.Boolean, "SByte": ua.VariantType.SByte, "Byte": ua.VariantType.Byte,
    "Int16": ua.VariantType.Int16, "UInt16": ua.VariantType.UInt16, "Int32": ua.VariantType.Int32,
    "UInt32": ua.VariantType.UInt32, "Int64": ua.VariantType.Int64, "UInt64": ua.VariantType.UInt64,
    "Float": ua.VariantType.Float, "Double": ua.VariantType.Double,
}


@contextlib.asynccontextmanager
async def connect(write: bool = False):
    client = Client(settings.opcua_endpoint, timeout=20)
    if settings.opcua_security:
        await client.set_security_string(settings.opcua_security)
    user = settings.opcua_write_username if write and settings.opcua_write_username else settings.opcua_username
    pwd = settings.opcua_write_password if write and settings.opcua_write_username else settings.opcua_password
    if user:
        client.set_user(user)
        client.set_password(pwd or "")
    async with client:
        yield client


@dataclass
class BNode:
    node_id: str
    name: str
    path: str                 # chemin lisible depuis la racine de navigation
    parent: str | None
    is_var: bool
    children: list[str]
    datatype: str | None = None   # renseigné pour les feuilles numériques ou booléennes


async def _browse_many(client: Client, node_ids: list[ua.NodeId]) -> list[list[ua.ReferenceDescription]]:
    out = []
    for i in range(0, len(node_ids), 500):
        params = ua.BrowseParameters()
        params.RequestedMaxReferencesPerNode = 0
        for nid in node_ids[i:i + 500]:
            d = ua.BrowseDescription()
            d.NodeId = nid
            d.BrowseDirection = ua.BrowseDirection.Forward
            d.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HierarchicalReferences)
            d.IncludeSubtypes = True
            d.NodeClassMask = int(ua.NodeClass.Object | ua.NodeClass.Variable)
            d.ResultMask = int(ua.BrowseResultMask.All)
            params.NodesToBrowse.append(d)
        for res in await client.uaclient.browse(params):
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


async def find_path(client: Client, path: str) -> ua.NodeId:
    current = client.nodes.objects.nodeid
    for part in [p for p in path.split("/") if p]:
        refs = (await _browse_many(client, [current]))[0]
        match = [r for r in refs if r.BrowseName.Name == part]
        if not match:
            raise RuntimeError(f"chemin introuvable dans l'automate : {path} (élément {part})")
        current = match[0].NodeId
    return current


async def browse_tree(client: Client, root_path: str) -> tuple[str, dict[str, BNode]]:
    """Parcourt tout l'espace sous root_path. Renvoie l'identifiant de la racine et les nœuds."""
    root = await find_path(client, root_path)
    root_id = root.to_string()
    nodes: dict[str, BNode] = {root_id: BNode(root_id, root_path.split("/")[-1], "", None, False, [])}
    frontier = [root]
    hasprop = ua.NodeId(ua.ObjectIds.HasProperty)
    while frontier:
        results = await _browse_many(client, frontier)
        nxt = []
        for parent, refs in zip(frontier, results):
            pid = parent.to_string()
            for r in refs:
                if r.ReferenceTypeId == hasprop:
                    continue
                cid = r.NodeId.to_string()
                if cid in nodes:
                    continue
                ppath = nodes[pid].path
                name = r.BrowseName.Name
                nodes[cid] = BNode(cid, name, f"{ppath}.{name}" if ppath else name, pid,
                                   r.NodeClass == ua.NodeClass.Variable, [])
                nodes[pid].children.append(cid)
                nxt.append(r.NodeId)
        frontier = nxt
    leaves = [n for n in nodes.values() if n.is_var and not n.children]
    for i in range(0, len(leaves), 1000):
        chunk = leaves[i:i + 1000]
        params = ua.ReadParameters()
        for n in chunk:
            rv = ua.ReadValueId()
            rv.NodeId = ua.NodeId.from_string(n.node_id)
            rv.AttributeId = ua.AttributeIds.DataType
            params.NodesToRead.append(rv)
        for n, dv in zip(chunk, await client.uaclient.read(params)):
            dt = dv.Value.Value if dv.Value is not None else None
            if isinstance(dt, ua.NodeId) and dt.NamespaceIndex == 0 and dt.Identifier in NUMERIC_TYPES:
                n.datatype = NUMERIC_TYPES[dt.Identifier]
    return root_id, nodes


async def read_values(client: Client, node_ids: list[str]) -> list:
    nodes = [client.get_node(n) for n in node_ids]
    return await client.read_values(nodes)


async def write_values(client: Client, items: list[tuple[str, object, str]]) -> None:
    """items = (node_id, valeur, type OPC UA)."""
    nodes = [client.get_node(n) for n, _, _ in items]
    values = [ua.DataValue(ua.Variant(v, VARIANT[t])) for _, v, t in items]
    await client.write_values(nodes, values)
