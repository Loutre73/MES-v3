-- Suppression de recettes : une recette jamais utilisée est effacée ; une recette déjà produite ou chargée
-- est marquée supprimée (masquée partout, conservée pour l'historique des postes et des chargements).
-- Son code redevient libre.
ALTER TABLE mes.recipe
  ADD COLUMN IF NOT EXISTS deleted_at timestamptz,
  ADD COLUMN IF NOT EXISTS deleted_by text;

ALTER TABLE mes.recipe DROP CONSTRAINT IF EXISTS recipe_type_id_code_key;
CREATE UNIQUE INDEX IF NOT EXISTS recipe_code_actif ON mes.recipe (type_id, code) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS shift_recipe ON mes.shift (recipe_id) WHERE recipe_id IS NOT NULL;
