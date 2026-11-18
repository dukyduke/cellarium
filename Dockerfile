FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CELLARIUM_DATA_DIR=/data CELLARIUM_HOST=0.0.0.0 CELLARIUM_PORT=8770

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY cellarium ./cellarium
RUN pip install --no-cache-dir . && useradd --system --uid 1000 --home /data cellarium \
    && mkdir -p /data && chown cellarium /data

USER cellarium
VOLUME ["/data"]
EXPOSE 8770
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8770/healthz')"
CMD ["python", "-m", "cellarium", "serve"]
