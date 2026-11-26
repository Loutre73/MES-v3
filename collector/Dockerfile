# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
# Certificat d'un proxy d'entreprise optionnel : docker build --secret id=ca,src=ca.crt
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY mes_collector ./mes_collector
RUN useradd -r -u 10001 mes && mkdir -p /spool && chown mes /spool
USER mes
VOLUME ["/spool"]
EXPOSE 9108 9109
CMD ["python", "-m", "mes_collector.main"]
