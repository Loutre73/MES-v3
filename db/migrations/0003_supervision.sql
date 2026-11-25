-- Supervision technique : temps des requêtes SQL et activité de la base (tableau Grafana « Système »).
-- pg_stat_statements doit être préchargé (shared_preload_libraries, voir docker-compose.yml).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;
-- Grafana (mes_reader) lit pg_stat_activity et pg_stat_statements de tous les rôles
GRANT pg_read_all_stats TO mes_reader;
