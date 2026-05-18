FROM python:3.12-slim AS base

LABEL org.opencontainers.image.source="https://github.com/NickyM/Housekeeperr" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="Housekeeperr" \
      org.opencontainers.image.description="Scan Radarr/Sonarr libraries for content available on streaming services and watched on Plex; bulk ignore/delete"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOUSEKEEPER_DATA_DIR=/data \
    PORT=8765

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app ./app
COPY static ./static

# Persistent data dir (config + ignore list + scan cache live here as SQLite)
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8765/api/scan/status', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
