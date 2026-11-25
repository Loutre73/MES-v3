# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY api/requirements.txt .
# Certificat d'un proxy d'entreprise optionnel : docker build --secret id=ca,src=ca.crt
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY api/mes_api ./mes_api
COPY catalog ./catalog
RUN useradd -r -u 10001 mes
USER mes
EXPOSE 8000
CMD ["uvicorn", "mes_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
