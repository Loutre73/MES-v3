# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
# Certificat d'un proxy d'entreprise optionnel : docker build --secret id=ca,src=ca.crt
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY mes_ops ./mes_ops
COPY static ./static
# Reste root : la console lit et pilote l'API Docker par son socket (monté par docker-compose).
EXPOSE 8090
CMD ["uvicorn", "mes_ops.main:app", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers", "--forwarded-allow-ips", "*"]
