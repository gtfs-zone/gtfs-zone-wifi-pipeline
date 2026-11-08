# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---

# Debian rather than alpine: Dagster's dependencies ship manylinux wheels
# only. Trixie for its tippecanoe package, which builds bins.pmtiles.
FROM python:3.13-slim-trixie

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tippecanoe \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 wifi && useradd -u 10001 -g wifi -M wifi

# DAGSTER_HOME must exist and be writable by the runtime user.
RUN mkdir -p /app/dagster_home && chown wifi:wifi /app/dagster_home

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY config/ /app/config/

ENV PATH="/app/.venv/bin:$PATH" \
    DAGSTER_HOME=/app/dagster_home

USER wifi

EXPOSE 4000

CMD ["dagster", "api", "grpc", "-h", "0.0.0.0", "-p", "4000", "-m", "gtfs_zone_wifi_pipeline.pipeline.definitions"]
