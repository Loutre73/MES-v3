# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
# Certificat d'un proxy d'entreprise optionnel : docker build --secret id=ca,src=ca.crt
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY simulator.py scenarios.py ./
COPY config ./config
EXPOSE 4840 8090
CMD ["python", "simulator.py"]
