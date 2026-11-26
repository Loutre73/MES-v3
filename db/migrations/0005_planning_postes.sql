-- Planning édité à la main : postes créés, dupliqués, copiés de semaine en semaine.
-- Les arrêts planifiés (arrêt ou maintenance) deviennent des plages à l'intérieur d'un poste : ils sont
-- rattachés au poste (supprimés avec lui) et comptent comme avant dans le calcul de la disponibilité.
ALTER TABLE mes.planned_stop
  ADD COLUMN shift_id integer REFERENCES mes.shift(id) ON DELETE CASCADE,
  ADD COLUMN type text NOT NULL DEFAULT 'arret' CHECK (type IN ('arret', 'maintenance')),
  ALTER COLUMN reason DROP NOT NULL;
CREATE INDEX ON mes.planned_stop (shift_id);
CREATE INDEX ON mes.planned_stop (line_id, starts_at);

-- Un poste « maintenance » ou « arrêt » devient un poste de production entièrement couvert par un arrêt planifié.
INSERT INTO mes.planned_stop (line_id, starts_at, ends_at, reason, created_by, shift_id, type)
SELECT line_id, starts_at, ends_at, NULL, 'migration', id, kind FROM mes.shift WHERE kind IN ('maintenance', 'arret');
UPDATE mes.shift SET kind = 'production' WHERE kind <> 'production';

-- Les postes ne sont plus générés à partir des modèles qu'une seule fois, à la mise en service
-- (planning vide) ; ensuite la copie automatique reprend la semaine en cours dans une semaine suivante vide.
INSERT INTO mes.setting (key, value) VALUES
  ('planning_copie_auto', 'true'),
  ('planning_copie_jour', '4'),        -- 0 = lundi … 4 = vendredi
  ('planning_copie_heure', '6'),
  ('planning_initialise', to_jsonb(EXISTS (SELECT 1 FROM mes.shift)))
ON CONFLICT (key) DO NOTHING;
DELETE FROM mes.setting WHERE key = 'shifts_jours_a_l_avance';
