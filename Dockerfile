# syntax=docker/dockerfile:1

# Base images and the PyPI index are parameterized so the internal GitLab/Kaniko
# build can pull them through the ProGet mirror (those runners have no public
# egress), while the public GitHub Actions build uses the upstream defaults below.
# Override with --build-arg PYTHON_IMAGE=... --build-arg UV_IMAGE=... --build-arg
# UV_INDEX_URL=...
ARG PYTHON_IMAGE=python:3.14-alpine
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.10.10
ARG UV_INDEX_URL=https://pypi.org/simple

# Pinned, statically-linked uv binary (musl-compatible, works on Alpine).
FROM ${UV_IMAGE} AS uv

# ---- Build stage ------------------------------------------------------------
# Install locked dependencies and the project itself into a self-contained
# virtualenv using uv + uv.lock, so the image ships the exact, pinned (and
# CVE-patched) versions that are tested in CI.
FROM ${PYTHON_IMAGE} AS build

COPY --from=uv /uv /uvx /bin/

# Re-declare the global ARGs so they are in scope below.
ARG UV_INDEX_URL
# Lock mode for `uv sync`. Public builds keep `--frozen` to install the exact
# pinned versions from uv.lock (whose URLs point at public PyPI). The GitLab
# build overrides this to empty so uv resolves through the ProGet mirror
# (UV_DEFAULT_INDEX) instead of replaying uv.lock's unreachable pythonhosted
# URLs -- matching how the CI test job already resolves.
ARG UV_LOCK_MODE=--frozen

# Use the base image's interpreter; never download a managed Python.
# UV_DEFAULT_INDEX points uv at the PyPI index (public by default; the ProGet
# mirror in the GitLab build, whose runners have no public PyPI egress).
ENV UV_PYTHON=python3.14 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=${UV_INDEX_URL}

WORKDIR /app

# Resolve dependencies first so this layer is cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync ${UV_LOCK_MODE} --no-dev --no-install-project

# Install the project itself against the locked environment. --no-editable
# copies the package into site-packages instead of linking to ./src, so the
# runtime image doesn't depend on the source tree (and its permissions) being
# readable by the unprivileged user.
COPY README.md ./
COPY src ./src
RUN uv sync ${UV_LOCK_MODE} --no-dev --no-editable

# ---- Runtime stage ----------------------------------------------------------
FROM ${PYTHON_IMAGE}

# git is required at runtime for mirroring; openssl generates the self-signed
# webhook TLS certificate in the entrypoint.
RUN apk add --no-cache git openssl

WORKDIR /app

# Bring in the populated virtualenv and the app source from the build stage.
COPY --from=build /app /app

# Entrypoint auto-generates the webhook TLS cert when the listener is enabled.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Put the venv's console scripts (holocron) on PATH, and give git/tools a
# writable HOME under the unprivileged user created below.
ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/home/holocron

# Run as an unprivileged user instead of root. Create it and pre-own the paths
# the app writes to at runtime -- the mirror data and the TLS cert directory --
# BEFORE declaring the volume, because changes to a volume path made after the
# VOLUME instruction are discarded (the cert volume would otherwise be root-owned
# and the entrypoint could not write the generated certificate to it).
RUN addgroup -S holocron \
    && adduser -S -G holocron -h /home/holocron holocron \
    && mkdir -p /app/mirror-data /certs \
    && chown -R holocron:holocron /app/mirror-data /certs /home/holocron

# Webhook TLS cert/key default to /certs/webhook.{crt,key}; the entrypoint sets
# those paths (not ENV, to keep the *_KEY var out of image metadata). Mount your
# own cert into this volume to replace the auto-generated self-signed one.
VOLUME ["/certs"]

# Webhook listener port (only used when running with --webhook; >1024 so the
# unprivileged user can bind it).
EXPOSE 8080

USER holocron

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default command (can be overridden).
CMD ["--watch", "--interval", "60"]
