# Production Docker Hardening — multi-stage API/worker image.
# Runtime contains production dependencies only; runs as non-root.

# syntax=docker/dockerfile:1

FROM python:3.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY docker ./docker


FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    APP_HOME=/app

WORKDIR /app

RUN groupadd --gid 10001 verisure \
    && useradd --uid 10001 --gid verisure --home-dir /app --shell /usr/sbin/nologin verisure

COPY --from=builder --chown=verisure:verisure /app/.venv /app/.venv
COPY --from=builder --chown=verisure:verisure /app/app /app/app
COPY --from=builder --chown=verisure:verisure /app/alembic /app/alembic
COPY --from=builder --chown=verisure:verisure /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=verisure:verisure /app/docker /app/docker

RUN chmod +x /app/docker/start.sh

USER verisure:verisure

EXPOSE 8000

# Default role is API; compose overrides worker with: docker/start.sh worker
ENTRYPOINT ["/app/docker/start.sh"]
CMD ["api"]
