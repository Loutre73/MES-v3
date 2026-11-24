import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "oui")


@dataclass(frozen=True)
class Config:
    endpoint: str
    database_url: str
    security: str | None            # ex. "Basic256Sha256,SignAndEncrypt,/certs/client.der,/certs/client.pem"
    username: str | None
    password: str | None
    sampling_ms: int
    publishing_ms: int
    items_per_subscription: int
    server_deadband: bool           # demande aussi la deadband absolue au serveur OPC UA
    keepalive_rewrite_s: int        # réécriture périodique de la dernière valeur (ancre pour les lectures)
    batch_max_rows: int
    batch_max_s: float
    spool_dir: str
    catalog_poll_s: int
    bootstrap_browse: bool
    browse_root: str                # chemin de navigation, ex. "PLC_1/DataBlocksGlobal"
    metrics_port: int

    @staticmethod
    def from_env() -> "Config":
        e = os.environ
        return Config(
            endpoint=e.get("OPCUA_ENDPOINT", "opc.tcp://simulator:4840"),
            database_url=e["DATABASE_URL"],
            security=e.get("OPCUA_SECURITY") or None,
            username=e.get("OPCUA_USERNAME") or None,
            password=e.get("OPCUA_PASSWORD") or None,
            sampling_ms=int(e.get("SAMPLING_MS", "1000")),
            publishing_ms=int(e.get("PUBLISHING_MS", "1000")),
            items_per_subscription=int(e.get("ITEMS_PER_SUBSCRIPTION", "1000")),
            server_deadband=_bool("SERVER_DEADBAND", True),
            keepalive_rewrite_s=int(e.get("KEEPALIVE_REWRITE_S", "900")),
            batch_max_rows=int(e.get("BATCH_MAX_ROWS", "20000")),
            batch_max_s=float(e.get("BATCH_MAX_S", "1.0")),
            spool_dir=e.get("SPOOL_DIR", "/spool"),
            catalog_poll_s=int(e.get("CATALOG_POLL_S", "30")),
            bootstrap_browse=_bool("BOOTSTRAP_BROWSE", True),
            browse_root=e.get("BROWSE_ROOT", "PLC_1/DataBlocksGlobal"),
            metrics_port=int(e.get("METRICS_PORT", "9108")),
        )
