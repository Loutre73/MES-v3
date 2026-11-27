-- Langue de l'interface choisie par chaque utilisateur (NULL : celle du navigateur, français par défaut).
ALTER TABLE mes.app_user ADD COLUMN IF NOT EXISTS langue text;
