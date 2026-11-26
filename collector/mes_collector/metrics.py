"""Indicateurs Prometheus du collecteur (tableau de bord « Système »)."""
from prometheus_client import Counter, Gauge, Histogram

NOTIFICATIONS = Counter("mes_collector_notifications_total", "Valeurs reçues du serveur OPC UA")
ROWS_STORED = Counter("mes_collector_rows_accepted_total", "Valeurs retenues après deadband")
ROWS_WRITTEN = Counter("mes_collector_rows_written_total", "Lignes écrites dans l'historian")
ROWS_SPOOLED = Counter("mes_collector_rows_spooled_total", "Lignes mises en tampon disque (base indisponible)")
ROWS_REPLAYED = Counter("mes_collector_rows_replayed_total", "Lignes rejouées depuis le tampon disque")
WRITE_ERRORS = Counter("mes_collector_write_errors_total", "Écritures en base échouées")
SESSIONS = Counter("mes_collector_opcua_sessions_total", "Sessions OPC UA ouvertes (1 + reconnexions)")
SESSION_ERRORS = Counter("mes_collector_opcua_session_errors_total", "Sessions OPC UA interrompues")
WRITE_SECONDS = Histogram("mes_collector_write_duration_seconds", "Durée d'une écriture COPY dans l'historian",
                          buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10))
BATCH_ROWS = Histogram("mes_collector_batch_rows", "Lignes par lot écrit",
                       buckets=(1, 10, 50, 100, 500, 1000, 2500, 5000, 10000, 20000))
SUBSCRIBE_SECONDS = Gauge("mes_collector_subscribe_seconds", "Durée de création des abonnements à la connexion")
SUBSCRIPTIONS = Gauge("mes_collector_subscriptions", "Abonnements OPC UA actifs")
TAGS = Gauge("mes_collector_tags", "Tags surveillés")
QUEUE = Gauge("mes_collector_queue", "Lignes en attente d'écriture")
SPOOL_FILES = Gauge("mes_collector_spool_files", "Lots en tampon disque")
DB_UP = Gauge("mes_collector_db_up", "1 si la dernière écriture a réussi")
OPC_UP = Gauge("mes_collector_opcua_up", "1 si la session OPC UA est active")
LAST_WRITE = Gauge("mes_collector_last_write_timestamp", "Horodatage de la dernière écriture réussie")
LAST_NOTIFICATION = Gauge("mes_collector_last_notification_timestamp", "Horodatage de la dernière valeur reçue")
ROWS_SUPPRESSED = Counter("mes_collector_rows_suppressed_total", "Valeurs non écrites : historisation suspendue")
HIST_ACTIVE = Gauge("mes_collector_historisation_active", "1 si l'historisation est active (globalement)")
