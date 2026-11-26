# Agent d'exploitation : bibliothèque standard Python seulement (aucune dépendance à installer).
FROM python:3.12-slim
WORKDIR /app
COPY ops_agent.py .
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 9200
CMD ["python", "-u", "ops_agent.py"]
