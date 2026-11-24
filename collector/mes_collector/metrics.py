"""Indicateurs Prometheus du collecteur (profil de surveillance optionnel)."""
from prometheus_client import Counter, Gauge

NOTIFICATIONS = Counter("mes_collector_notifications_total", "Valeurs reçues du serveur OPC UA")
ROWS_STORED = Counter("mes_collector_rows_accepted_total", "Valeurs retenues après deadband")
ROWS_WRITTEN = Counter("mes_collector_rows_written_total", "Lignes écrites dans l'historian")
TAGS = Gauge("mes_collector_tags", "Tags surveillés")
QUEUE = Gauge("mes_collector_queue", "Lignes en attente d'écriture")
SPOOL_FILES = Gauge("mes_collector_spool_files", "Lots en tampon disque")
DB_UP = Gauge("mes_collector_db_up", "1 si la dernière écriture a réussi")
OPC_UP = Gauge("mes_collector_opcua_up", "1 si la session OPC UA est active")
LAST_WRITE = Gauge("mes_collector_last_write_timestamp", "Horodatage de la dernière écriture réussie")
