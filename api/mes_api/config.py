import os
import secrets
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("DATABASE_URL", "postgresql://mes:mes@db:5432/mes")
    opcua_endpoint: str = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://simulator:4840")
    opcua_security: str | None = os.environ.get("OPCUA_SECURITY") or None
    opcua_username: str | None = os.environ.get("OPCUA_USERNAME") or None
    opcua_password: str | None = os.environ.get("OPCUA_PASSWORD") or None
    # Compte OPC UA dédié aux écritures de recettes (droits d'écriture limités côté automate)
    opcua_write_username: str | None = os.environ.get("OPCUA_WRITE_USERNAME") or None
    opcua_write_password: str | None = os.environ.get("OPCUA_WRITE_PASSWORD") or None
    browse_root: str = os.environ.get("BROWSE_ROOT", "PLC_1/DataBlocksGlobal")
    types_dir: str = os.environ.get("TYPES_DIR", "/app/catalog/types")
    recipes_dir: str = os.environ.get("RECIPE_TYPES_DIR", "/app/catalog/recipes")
    secret_key: str = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    session_hours: int = int(os.environ.get("SESSION_HOURS", "12"))
    admin_password: str | None = os.environ.get("ADMIN_PASSWORD") or None
    # Active Directory (AUTH_LDAP_URL vide = comptes locaux seulement)
    ldap_url: str | None = os.environ.get("AUTH_LDAP_URL") or None
    ldap_domain: str | None = os.environ.get("AUTH_LDAP_DOMAIN") or None          # ex. USINE (connexion USINE\\login)
    ldap_base_dn: str | None = os.environ.get("AUTH_LDAP_BASE_DN") or None        # ex. DC=usine,DC=local
    ldap_group_roles: str = os.environ.get("AUTH_LDAP_GROUP_ROLES", "")           # ex. MES_Admins=admin;MES_Chefs=chef_equipe
    workers: bool = os.environ.get("WORKERS", "true").lower() in ("1", "true", "oui", "yes")
    timezone: str = os.environ.get("SITE_TIMEZONE", "Europe/Paris")


settings = Settings()
