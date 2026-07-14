# syntax=docker/dockerfile:1

# ---- Build stage ------------------------------------------------------------
# Install locked dependencies and the project itself into a self-contained
# virtualenv using uv + uv.lock, so the image ships the exact, pinned (and
# CVE-patched) versions that are tested in CI.
FROM python:3.14-alpine AS build

# Pinned, statically-linked uv binary (musl-compatible, works on Alpine).
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /uvx /bin/

# Use the base image's interpreter; never download a managed Python.
ENV UV_PYTHON=python3.14 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Resolve dependencies first so this layer is cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Install the project itself against the locked environment.
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# ---- Runtime stage ----------------------------------------------------------
FROM python:3.14-alpine

# git is required at runtime for mirroring.
RUN apk add --no-cache git

WORKDIR /app

# Bring in the populated virtualenv and the app source from the build stage.
COPY --from=build /app /app

# Put the venv's console scripts (holocron) on PATH.
ENV PATH="/app/.venv/bin:$PATH"

# Directory for mirror data.
RUN mkdir -p mirror-data

ENTRYPOINT ["holocron"]

# Default command (can be overridden).
CMD ["--watch", "--interval", "60"]
