-- Lots 1 à 6 : catalogue étendu, utilisateurs, arrêts, shifts, alarmes, moteurs, recettes, énergie.

-- ---------------------------------------------------------------- catalogue
ALTER TABLE catalog.machine_type ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE catalog.machine ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE catalog.machine ADD COLUMN enabled boolean NOT NULL DEFAULT true;
CREATE INDEX ON catalog.tag (role);

CREATE TABLE catalog.discovery_run (
  id           serial PRIMARY KEY,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  status       text NOT NULL DEFAULT 'en_cours',   -- en_cours, termine, erreur, applique
  started_by   text,
  result       jsonb,
  error        text
);

-- ---------------------------------------------------------------- utilisateurs
CREATE TABLE mes.app_user (
  username      text PRIMARY KEY,
  display_name  text,
  role          text NOT NULL CHECK (role IN ('operateur', 'chef_equipe', 'maintenance', 'admin')),
  password_hash text,                 -- comptes locaux uniquement ; NULL pour les comptes AD
  source        text NOT NULL DEFAULT 'local' CHECK (source IN ('local', 'ldap')),
  active        boolean NOT NULL DEFAULT true,
  last_login    timestamptz
);

CREATE TABLE mes.audit_log (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  actor      text,
  action     text NOT NULL,
  entity     text,
  entity_id  text,
  details    jsonb
);
CREATE INDEX ON mes.audit_log (at DESC);

CREATE TABLE mes.setting (
  key    text PRIMARY KEY,
  value  jsonb NOT NULL
);
INSERT INTO mes.setting (key, value) VALUES
  ('arret_duree_min_s', '60'),
  ('derive_seuil_attention_pct', '10'),
  ('derive_seuil_alerte_pct', '20'),
  ('derive_fenetre_min', '15'),
  ('shifts_jours_a_l_avance', '7'),
  ('kpi_conservation', '"3 years"'),
  ('recette_tolerance_pct', '0.5');

-- ---------------------------------------------------------------- shifts et arrêts
CREATE TABLE mes.shift_template (
  id          serial PRIMARY KEY,
  code        text NOT NULL UNIQUE,
  label       text NOT NULL,
  start_time  time NOT NULL,
  duration    interval NOT NULL,
  active      boolean NOT NULL DEFAULT true
);
INSERT INTO mes.shift_template (code, label, start_time, duration) VALUES
  ('M', 'Matin', '06:00', '8 hours'),
  ('A', 'Après-midi', '14:00', '8 hours'),
  ('N', 'Nuit', '22:00', '8 hours');

CREATE TABLE mes.shift (
  id           serial PRIMARY KEY,
  line_id      integer NOT NULL REFERENCES catalog.machine(id),
  template_id  integer REFERENCES mes.shift_template(id),
  starts_at    timestamptz NOT NULL,
  ends_at      timestamptz NOT NULL,
  team         text,
  target_t     double precision,
  comment      text,
  UNIQUE (line_id, starts_at),
  CHECK (ends_at > starts_at)
);
CREATE INDEX ON mes.shift (starts_at);

CREATE TABLE mes.planned_stop (
  id          serial PRIMARY KEY,
  line_id     integer NOT NULL REFERENCES catalog.machine(id),
  starts_at   timestamptz NOT NULL,
  ends_at     timestamptz NOT NULL,
  reason      text NOT NULL,
  created_by  text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_at > starts_at)
);

CREATE TABLE mes.stop_cause (
  id         serial PRIMARY KEY,
  parent_id  integer REFERENCES mes.stop_cause(id),
  code       text NOT NULL UNIQUE,
  label      text NOT NULL,
  category   text NOT NULL CHECK (category IN ('panne', 'reglage', 'attente', 'qualite', 'organisation', 'planifie', 'autre')),
  active     boolean NOT NULL DEFAULT true
);
INSERT INTO mes.stop_cause (code, label, category) VALUES
  ('MEC', 'Panne mécanique', 'panne'),
  ('ELEC', 'Panne électrique', 'panne'),
  ('AUTO', 'Défaut automatisme', 'panne'),
  ('REG', 'Réglage, changement de format', 'reglage'),
  ('MAT', 'Manque matière', 'attente'),
  ('AVAL', 'Saturation aval', 'attente'),
  ('PERS', 'Manque de personnel', 'organisation'),
  ('QUAL', 'Problème qualité', 'qualite'),
  ('NETT', 'Nettoyage', 'planifie'),
  ('AUTRE', 'Autre', 'autre');

CREATE TABLE mes.stop_event (
  id            bigserial PRIMARY KEY,
  machine_id    integer NOT NULL REFERENCES catalog.machine(id),
  starts_at     timestamptz NOT NULL,
  ends_at       timestamptz,
  plc_code      integer,
  planned       boolean NOT NULL DEFAULT false,
  cause_id      integer REFERENCES mes.stop_cause(id),
  comment       text,
  qualified_by  text,
  qualified_at  timestamptz,
  split_from    bigint REFERENCES mes.stop_event(id)
);
CREATE INDEX ON mes.stop_event (machine_id, starts_at DESC);
CREATE INDEX ON mes.stop_event (starts_at DESC);

-- Correspondance code d'arrêt automate -> cause (pré-qualification automatique)
CREATE TABLE mes.plc_stop_code (
  code      integer PRIMARY KEY,
  cause_id  integer NOT NULL REFERENCES mes.stop_cause(id)
);

-- KPI par shift et par ligne (conservés 3 ans)
CREATE TABLE mes.shift_kpi (
  shift_id           integer PRIMARY KEY REFERENCES mes.shift(id) ON DELETE CASCADE,
  computed_at        timestamptz NOT NULL DEFAULT now(),
  final              boolean NOT NULL DEFAULT false,
  duration_s         double precision NOT NULL,
  planned_stop_s     double precision NOT NULL,
  run_s              double precision NOT NULL,
  unplanned_stop_s   double precision NOT NULL,
  stop_count         integer NOT NULL,
  unqualified_count  integer NOT NULL,
  tonnage_t          double precision,
  target_t           double precision,
  nominal_rate_th    double precision,
  availability       double precision,
  performance        double precision,
  quality            double precision,
  oee                double precision,
  energy_kwh         double precision,
  kwh_per_t          double precision
);

CREATE TABLE mes.energy_shift (
  shift_id    integer NOT NULL REFERENCES mes.shift(id) ON DELETE CASCADE,
  meter_id    integer NOT NULL REFERENCES catalog.machine(id),
  kwh         double precision NOT NULL,
  PRIMARY KEY (shift_id, meter_id)
);

-- ---------------------------------------------------------------- alarmes
CREATE TABLE mes.alarm_def (
  id         serial PRIMARY KEY,
  tag_id     integer NOT NULL REFERENCES catalog.tag(id),
  condition  text NOT NULL DEFAULT 'vrai' CHECK (condition IN ('vrai', 'faux', 'superieur', 'inferieur')),
  threshold  double precision,
  priority   text NOT NULL DEFAULT 'moyenne' CHECK (priority IN ('basse', 'moyenne', 'haute', 'critique')),
  message    text NOT NULL,
  active     boolean NOT NULL DEFAULT true,
  UNIQUE NULLS NOT DISTINCT (tag_id, condition, threshold)
);

CREATE TABLE mes.alarm_event (
  id          bigserial PRIMARY KEY,
  def_id      integer NOT NULL REFERENCES mes.alarm_def(id),
  raised_at   timestamptz NOT NULL,
  cleared_at  timestamptz,
  ack_at      timestamptz,
  ack_by      text,
  value       double precision
);
CREATE INDEX ON mes.alarm_event (raised_at DESC);
CREATE INDEX ON mes.alarm_event (def_id) WHERE cleared_at IS NULL OR ack_at IS NULL;

-- ---------------------------------------------------------------- moteurs
CREATE TABLE mes.motor_reference (
  machine_id  integer PRIMARY KEY REFERENCES catalog.machine(id),
  ref_current double precision NOT NULL,
  method      text NOT NULL CHECK (method IN ('nominal', 'mesure')),
  set_at      timestamptz NOT NULL DEFAULT now(),
  set_by      text
);

CREATE TABLE mes.drift_snapshot (
  time           timestamptz NOT NULL,
  machine_id     integer NOT NULL REFERENCES catalog.machine(id),
  mean_current   double precision,
  ref_current    double precision,
  deviation_pct  double precision,
  status         text NOT NULL CHECK (status IN ('normal', 'attention', 'alerte', 'arret', 'sans_reference')),
  PRIMARY KEY (machine_id, time)
);
CREATE INDEX ON mes.drift_snapshot (time DESC);

-- ---------------------------------------------------------------- recettes
CREATE TABLE mes.recipe_type (
  id          serial PRIMARY KEY,
  code        text NOT NULL UNIQUE,
  label       text NOT NULL,
  definition  jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mes.recipe (
  id          serial PRIMARY KEY,
  type_id     integer NOT NULL REFERENCES mes.recipe_type(id),
  code        text NOT NULL,
  label       text NOT NULL,
  product     text,
  archived    boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  text,
  UNIQUE (type_id, code)
);

CREATE TABLE mes.recipe_version (
  id            serial PRIMARY KEY,
  recipe_id     integer NOT NULL REFERENCES mes.recipe(id),
  version       integer NOT NULL,
  status        text NOT NULL DEFAULT 'brouillon' CHECK (status IN ('brouillon', 'validee', 'archivee')),
  "values"      jsonb NOT NULL,
  comment       text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text,
  validated_at  timestamptz,
  validated_by  text,
  UNIQUE (recipe_id, version)
);
CREATE INDEX ON mes.recipe_version USING gin ("values");

CREATE TABLE mes.recipe_load (
  id            serial PRIMARY KEY,
  version_id    integer NOT NULL REFERENCES mes.recipe_version(id),
  requested_at  timestamptz NOT NULL DEFAULT now(),
  requested_by  text,
  finished_at   timestamptz,
  result        text NOT NULL DEFAULT 'en_cours' CHECK (result IN ('en_cours', 'acceptee', 'refusee', 'delai_depasse', 'erreur')),
  plc_code      integer,
  details       jsonb
);

GRANT SELECT ON ALL TABLES IN SCHEMA catalog, historian, mes TO mes_reader;
REVOKE SELECT ON mes.app_user FROM mes_reader;
