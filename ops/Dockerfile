# Agent d'exploitation : bibliothèque standard Python, plus le client Docker et Compose pour les mises à jour.
# Contexte de construction : la racine du dépôt (l'image embarque les fichiers d'installation de sa version).
FROM docker:27.3.1-cli AS docker-cli

FROM python:3.12-slim
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
WORKDIR /app
# Fichiers d'installation de la version (docker-compose.yml, mesctl, tableaux Grafana) : copiés sur le site
# par la mise à jour lancée depuis la console (maj.sh)
COPY deploy /kit/deploy
COPY grafana /kit/grafana
ARG MES_VERSION=dev
RUN echo "$MES_VERSION" > /kit/VERSION
COPY ops/ops_agent.py ops/maj.sh ./
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 9200
CMD ["python", "-u", "ops_agent.py"]
