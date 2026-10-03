FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY autoscaler/ ./autoscaler/
COPY config.yaml ./config.yaml

ENTRYPOINT ["python", "-m", "autoscaler.main", "--config", "config.yaml"]
