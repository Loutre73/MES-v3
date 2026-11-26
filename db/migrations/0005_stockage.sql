-- Maîtrise du volume de l'historian : état du stockage, purge à la demande, plafond de taille.
--
-- Trois jeux de données : 'brut' (historian.value), '1min' (historian.value_1min), '1h' (historian.value_1h).
-- Les paramètres (plafond, âges minimaux protégés) sont écrits par migrate.py à chaque démarrage.

CREATE TABLE IF NOT EXISTS historian.stockage_param (
  cle    text PRIMARY KEY,
  valeur text NOT NULL
);
INSERT INTO historian.stockage_param (cle, valeur) VALUES
  ('plafond_octets', '0'),          -- 0 = pas de plafond
  ('brut_minimum', '1 day'),        -- données brutes jamais purgées avant cet âge (recalcul des agrégats)
  ('agregat_1min_minimum', '2 days')
ON CONFLICT (cle) DO NOTHING;

CREATE OR REPLACE FUNCTION historian.stockage_relation(p_jeu text) RETURNS regclass
LANGUAGE sql STABLE AS $$
  SELECT CASE p_jeu WHEN 'brut' THEN 'historian.value'::regclass
                    WHEN '1min' THEN 'historian.value_1min'::regclass
                    WHEN '1h'   THEN 'historian.value_1h'::regclass END
$$;

-- Hypertable qui porte les chunks d'un jeu (celle des agrégats est la table de matérialisation)
CREATE OR REPLACE FUNCTION historian.stockage_hypertable(p_jeu text, OUT schema_name name, OUT table_name name)
LANGUAGE sql STABLE AS $$
  SELECT 'historian'::name, 'value'::name WHERE p_jeu = 'brut'
  UNION ALL
  SELECT materialization_hypertable_schema, materialization_hypertable_name
  FROM timescaledb_information.continuous_aggregates
  WHERE view_schema = 'historian' AND view_name = CASE p_jeu WHEN '1min' THEN 'value_1min' WHEN '1h' THEN 'value_1h' END
$$;

CREATE OR REPLACE FUNCTION historian.stockage_minimum(p_jeu text) RETURNS interval
LANGUAGE sql STABLE AS $$
  SELECT CASE p_jeu
    WHEN 'brut' THEN (SELECT valeur::interval FROM historian.stockage_param WHERE cle = 'brut_minimum')
    WHEN '1min' THEN (SELECT valeur::interval FROM historian.stockage_param WHERE cle = 'agregat_1min_minimum')
    ELSE interval '1 day' END
$$;

-- État du stockage, lu par la console d'exploitation
CREATE OR REPLACE FUNCTION historian.stockage_etat() RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r jsonb := '[]'::jsonb;
  j text;
  h record;
  n int; debut timestamptz; octets bigint;
BEGIN
  FOREACH j IN ARRAY ARRAY['brut', '1min', '1h'] LOOP
    SELECT * INTO h FROM historian.stockage_hypertable(j);
    SELECT count(*), min(c.range_start) INTO n, debut
      FROM timescaledb_information.chunks c
      WHERE c.hypertable_schema = h.schema_name AND c.hypertable_name = h.table_name;
    octets := coalesce(hypertable_size(format('%I.%I', h.schema_name, h.table_name)::regclass), 0);
    r := r || jsonb_build_object('jeu', j, 'octets', octets, 'chunks', n, 'depuis', debut,
                                 'minimum_h', extract(epoch FROM historian.stockage_minimum(j)) / 3600);
  END LOOP;
  RETURN jsonb_build_object(
    'base_octets', pg_database_size(current_database()),
    'plafond_octets', (SELECT valeur::bigint FROM historian.stockage_param WHERE cle = 'plafond_octets'),
    'jeux', r);
END $$;

-- Purge à la demande : supprime les chunks entiers plus anciens que « garder ».
-- Refuse de descendre sous l'âge minimal protégé du jeu.
CREATE OR REPLACE FUNCTION historian.purger(p_jeu text, p_garder interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
  rel regclass := historian.stockage_relation(p_jeu);
  mini interval := historian.stockage_minimum(p_jeu);
  limite timestamptz := now() - p_garder;
  h record;
  liberes bigint;
  n int;
BEGIN
  IF rel IS NULL THEN
    RAISE EXCEPTION 'jeu de données inconnu : %', p_jeu;
  END IF;
  IF p_garder < mini THEN
    RAISE EXCEPTION 'conservation minimale pour ce jeu : % (recalcul des agrégats)', mini;
  END IF;
  -- taille mesurée avant suppression : les fichiers ne sont effacés qu'à la validation
  SELECT * INTO h FROM historian.stockage_hypertable(p_jeu);
  SELECT coalesce(sum(s.total_bytes), 0) INTO liberes
    FROM chunks_detailed_size(format('%I.%I', h.schema_name, h.table_name)::regclass) s
    JOIN timescaledb_information.chunks c ON c.chunk_schema = s.chunk_schema AND c.chunk_name = s.chunk_name
    WHERE c.range_end <= limite;
  SELECT count(*) INTO n FROM drop_chunks(rel, older_than => limite);
  RETURN jsonb_build_object('jeu', p_jeu, 'chunks', n, 'liberes_octets', liberes);
END $$;

-- Plafond de taille : tant que la base dépasse le plafond, supprime le chunk le plus ancien parmi les
-- données brutes et les agrégats minute (jamais le chunk en cours ni les âges protégés ; les agrégats
-- horaires, peu volumineux, ne sont pas touchés). Lancée toutes les 5 minutes par TimescaleDB.
CREATE OR REPLACE PROCEDURE historian.garde_volume(job_id int, config jsonb)
LANGUAGE plpgsql AS $$
DECLARE
  plafond bigint;
  taille bigint;
  c record;
  tours int := 0;
BEGIN
  SELECT valeur::bigint INTO plafond FROM historian.stockage_param WHERE cle = 'plafond_octets';
  IF coalesce(plafond, 0) <= 0 THEN
    RETURN;
  END IF;
  LOOP
    taille := pg_database_size(current_database());
    EXIT WHEN taille <= plafond OR tours >= 1000;
    SELECT x.jeu, x.range_end INTO c FROM (
      SELECT j.jeu, ch.range_end,
             row_number() OVER (PARTITION BY j.jeu ORDER BY ch.range_end DESC) AS rang
      FROM (VALUES ('brut'), ('1min')) AS j(jeu)
      CROSS JOIN LATERAL historian.stockage_hypertable(j.jeu) h
      JOIN timescaledb_information.chunks ch
        ON ch.hypertable_schema = h.schema_name AND ch.hypertable_name = h.table_name
    ) x
    WHERE x.rang > 1 AND x.range_end <= now() - historian.stockage_minimum(x.jeu)
    ORDER BY x.range_end
    LIMIT 1;
    IF NOT FOUND THEN
      RAISE WARNING 'garde_volume : base de % octets au-dessus du plafond de % octets, plus rien de purgeable', taille, plafond;
      EXIT;
    END IF;
    PERFORM drop_chunks(historian.stockage_relation(c.jeu), older_than => c.range_end);
    RAISE LOG 'garde_volume : chunk % jusqu''à % supprimé (base de % octets)', c.jeu, c.range_end, taille;
    tours := tours + 1;
    COMMIT;
  END LOOP;
END $$;
