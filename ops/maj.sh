#!/bin/bash
# Tâche de version lancée par la console d'exploitation (rubrique Mises à jour).
# Tourne dans un conteneur à part (mes-maj), créé depuis l'image mes-ops de la version visée :
# il survit au remplacement de la console et apporte les fichiers d'installation de cette version.
# Le dossier d'installation de l'hôte est monté au même chemin (MES_DIR), pour que les chemins
# relatifs de docker-compose.yml désignent les mêmes fichiers que sur l'hôte.
#   maj.sh update <version> | rollback | backup
set -uo pipefail
dir=${MES_DIR:?MES_DIR manquant}
cd "$dir/deploy" || { echo "Erreur : $dir/deploy introuvable"; exit 1; }
mkdir -p .mesctl
prev=.mesctl/kit-precedent

copier_kit() {   # $1 : source (dossier contenant deploy/ et grafana/)
  cp -R "$1/deploy/." ./
  local d
  for d in dashboards dashboards-en; do
    mkdir -p "../grafana/$d"
    find "../grafana/$d" -maxdepth 1 -name '*.json' -delete   # tableaux retirés de la version
  done
  cp -R "$1/grafana/." ../grafana/   # dossiers conservés : Grafana garde ses montages
}

installer_kit() {
  echo "==> Fichiers d'installation"
  if [ -e "$dir/.git" ]; then
    echo "Dossier de développement (dépôt git) : fichiers conservés."
    return 0
  fi
  rm -rf "$prev" && mkdir -p "$prev/deploy" "$prev/grafana"
  cp -R docker-compose.yml mesctl site.env.example monitoring "$prev/deploy/" 2>/dev/null
  cp -R ../grafana/. "$prev/grafana/" 2>/dev/null
  copier_kit /kit
  echo "Fichiers de la version $(cat /kit/VERSION 2>/dev/null || echo '?') installés."
}

restaurer_kit() {
  [ -d "$prev/deploy" ] || return 0
  [ -e "$dir/.git" ] && return 0
  echo "Fichiers d'installation précédents remis en place."
  copier_kit "$prev"
}

cmd=${1:-}; shift || true
case "$cmd" in
  update)
    installer_kit
    ./mesctl update "$@"; rc=$?
    [ $rc -eq 0 ] || restaurer_kit
    exit $rc ;;
  rollback)
    installer_kit
    exec ./mesctl rollback ;;
  backup)
    exec ./mesctl backup ;;
  *) echo "usage : maj.sh update <version> | rollback | backup"; exit 2 ;;
esac
