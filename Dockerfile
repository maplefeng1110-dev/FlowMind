# FlowMind backend image — shared by the Registry and Web services.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLOWMIND_DATA_DIR=/data \
    FLOWMIND_LOG_DIR=/data/logs \
    FLOWMIND_UPLOAD_DIR=/data/uploads \
    FLOWMIND_REGISTRY_DB=/data/registry.db

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/logs /data/uploads
VOLUME ["/data"]

# Registry API (8000) and Web UI (5173)
EXPOSE 8000 5173

# Default to the Registry; the Web service overrides this command in compose.
CMD ["python", "-m", "registry.main"]
