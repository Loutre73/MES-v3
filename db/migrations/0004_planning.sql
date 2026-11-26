-- Planning graphique de la semaine : contenu d'un poste (production d'une recette, maintenance ou arrêt planifié).
-- Un poste « maintenance » ou « arret » est entièrement planifié : il ne pèse pas sur la disponibilité.
ALTER TABLE mes.shift
  ADD COLUMN kind text NOT NULL DEFAULT 'production' CHECK (kind IN ('production', 'maintenance', 'arret')),
  ADD COLUMN recipe_id integer REFERENCES mes.recipe(id);
