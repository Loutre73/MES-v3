-- Lot 0 : schémas, catalogue minimal et historian.
-- Les durées de conservation sont appliquées par migrate.py à partir de site.env.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS historian;
CREATE SCHEMA IF NOT EXISTS mes;

-- Arborescence du site (site / atelier / ligne)
CREATE TABLE catalog.area (
  id          serial PRIMARY KEY,
  parent_id   integer REFERENCES catalog.area(id),
  name        text NOT NULL,
  UNIQUE (parent_id, name)
);

-- Types de machines : chargés depuis catalog/types/*.yaml (lot 1)
CREATE TABLE catalog.machine_type (
  id          serial PRIMARY KEY,
  code        text NOT NULL UNIQUE,
  version     integer NOT NULL DEFAULT 1,
  label       text NOT NULL,
  definition  jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE catalog.machine (
  id          serial PRIMARY KEY,
  type_id     integer NOT NULL REFERENCES catalog.machine_type(id),
  area_id     integer REFERENCES catalog.area(id),
  name        text NOT NULL UNIQUE,
  base_node   text NOT NULL,
  attributes  jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Tag réellement collecté
CREATE TABLE catalog.tag (
  id              serial PRIMARY KEY,
  node_id         text NOT NULL UNIQUE,          -- NodeId OPC UA complet, ex. ns=3;s="DB_Moteurs"."M101"."Courant"
  name            text NOT NULL,                 -- chemin lisible, ex. DB_Moteurs.M101.Courant
  data_type       text NOT NULL,                 -- Float, Double, Boolean, Int16, Int32...
  unit            text,
  machine_id      integer REFERENCES catalog.machine(id),
  member          text,                          -- membre du type de machine (Courant, Marche...)
  role            text,                          -- rôle MES : courant_moteur, etat_marche, compteur_energie...
  deadband_kind   text NOT NULL DEFAULT 'none' CHECK (deadband_kind IN ('none', 'absolute', 'percent')),
  deadband_value  double precision NOT NULL DEFAULT 0,
  eu_min          double precision,              -- étendue de mesure, base de la deadband en %
  eu_max          double precision,
  historize       boolean NOT NULL DEFAULT true,
  enabled         boolean NOT NULL DEFAULT true,
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON catalog.tag (machine_id);

CREATE OR REPLACE FUNCTION catalog.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;
CREATE TRIGGER tag_touch BEFORE UPDATE ON catalog.tag FOR EACH ROW EXECUTE FUNCTION catalog.touch_updated_at();

-- Historian : une ligne par changement de valeur
-- quality : 0 = bon, 1 = incertain, 2 = mauvais (sévérité du StatusCode OPC UA)
CREATE TABLE historian.value (
  time     timestamptz      NOT NULL,
  tag_id   integer          NOT NULL,
  value    double precision,
  quality  smallint         NOT NULL DEFAULT 0
);
-- Chunks de 6 h compressés après 12 h : limite la part non compressée (environ 110 octets par point)
SELECT create_hypertable('historian.value', 'time', chunk_time_interval => interval '6 hours');
CREATE INDEX ON historian.value (tag_id, time DESC);
ALTER TABLE historian.value SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'tag_id',
  timescaledb.compress_orderby = 'time'
);
SELECT add_compression_policy('historian.value', interval '12 hours');

-- Agrégats à la minute (tendances rapides sur de longues périodes)
CREATE MATERIALIZED VIEW historian.value_1min
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 minute', time) AS bucket,
       tag_id,
       avg(value)        AS avg,
       min(value)        AS min,
       max(value)        AS max,
       last(value, time) AS last,
       count(*)          AS samples
FROM historian.value
GROUP BY 1, 2
WITH NO DATA;
SELECT add_continuous_aggregate_policy('historian.value_1min',
  start_offset => interval '2 hours', end_offset => interval '1 minute', schedule_interval => interval '1 minute');
ALTER MATERIALIZED VIEW historian.value_1min SET (timescaledb.compress = true,
  timescaledb.compress_segmentby = 'tag_id', timescaledb.compress_orderby = 'bucket');
SELECT add_compression_policy('historian.value_1min', compress_after => interval '2 days');

-- Agrégats horaires, calculés à partir des agrégats minute (conservation longue)
CREATE MATERIALIZED VIEW historian.value_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 hour', bucket) AS bucket,
       tag_id,
       sum(avg * samples) / nullif(sum(samples), 0) AS avg,
       min(min)            AS min,
       max(max)            AS max,
       last(last, bucket)  AS last,
       sum(samples)        AS samples
FROM historian.value_1min
GROUP BY 1, 2
WITH NO DATA;
SELECT add_continuous_aggregate_policy('historian.value_1h',
  start_offset => interval '1 day', end_offset => interval '1 hour', schedule_interval => interval '15 minutes');
ALTER MATERIALIZED VIEW historian.value_1h SET (timescaledb.compress = true,
  timescaledb.compress_segmentby = 'tag_id', timescaledb.compress_orderby = 'bucket');
SELECT add_compression_policy('historian.value_1h', compress_after => interval '7 days');

-- Dernière valeur connue d'un tag à un instant donné (lecture « historian » entre deux changements)
CREATE OR REPLACE FUNCTION historian.value_at(p_tag integer, p_time timestamptz)
RETURNS double precision LANGUAGE sql STABLE AS $$
  SELECT value FROM historian.value
  WHERE tag_id = p_tag AND time <= p_time
  ORDER BY time DESC LIMIT 1
$$;

-- Rôle en lecture seule pour Grafana (mot de passe fixé par migrate.py)
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mes_reader') THEN
    CREATE ROLE mes_reader LOGIN;
  END IF;
END $$;
GRANT USAGE ON SCHEMA catalog, historian, mes TO mes_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA catalog, historian, mes TO mes_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA catalog, historian, mes GRANT SELECT ON TABLES TO mes_reader;
