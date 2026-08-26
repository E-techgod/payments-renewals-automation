FROM python:3.12.8-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.21

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --home-dir /home/appuser appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app /home/appuser

VOLUME ["/app/data"]

COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY run.py ./run.py

# Mount GOOGLE_CREDENTIALS_FILE at runtime and inject environment variables
# with --env-file or your orchestrator's secret mechanism. Never COPY secrets
# or a real .env file into the image.
# IMPORTANT: `VOLUME /app/data` documents the expected mount point; it does
# NOT provide persistence by itself. Every `docker run` must explicitly mount
# `/app/data` to a bind mount or named volume, and any host bind-mount must be
# writable by uid/gid 1000 (for example,
# `chown 1000:1000 /host/path` before `-v /host/path:/app/data`; named volumes
# like `-v birthday-automation-data:/app/data` also work) or duplicate-send
# protection resets on every execution, including cron-driven runs on an
# otherwise-persistent host.

USER appuser

COPY entrypoint.sh /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]