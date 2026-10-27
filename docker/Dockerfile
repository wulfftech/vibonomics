# syntax=docker/dockerfile:1.7

# ---- web UI ---------------------------------------------------------------------------------
FROM node:24-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build && npm test

# ---- python dependencies --------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS python-deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/vibonomics backend/vibonomics
RUN python -m venv /opt/venv \
 && . /opt/venv/bin/activate \
 && pip install --upgrade pip \
 && pip install /src/backend

# ---- runtime --------------------------------------------------------------------------------
FROM python:3.13-slim-bookworm

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIBONOMICS_CONFIG_DIR=/config \
    VIBONOMICS_STATIC_DIR=/app/static \
    VIBONOMICS_PORT=2289 \
    VIBONOMICS_HOST=0.0.0.0 \
    PUID=1000 \
    PGID=1000 \
    TZ=Etc/UTC

RUN apt-get update \
 && apt-get install --no-install-recommends -y util-linux ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1000 vibonomics \
 && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/vibonomics vibonomics

COPY --from=python-deps /opt/venv /opt/venv
COPY --from=frontend /src/frontend/dist /app/static
COPY backend/vibonomics /app/backend/vibonomics
COPY docker/entrypoint.sh /usr/local/bin/vibonomics-entrypoint

RUN chmod +x /usr/local/bin/vibonomics-entrypoint \
 && mkdir -p /config /run/vibonomics-sandbox \
 && chown -R vibonomics:vibonomics /config /app

WORKDIR /app
VOLUME ["/config"]
EXPOSE 2289

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:2289/ping', timeout=4)"

ENTRYPOINT ["/usr/local/bin/vibonomics-entrypoint"]
CMD ["python", "-m", "vibonomics"]
