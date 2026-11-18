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
# Dependencies are installed from the single uv.lock, not re-resolved.
#
# `uv export --frozen` turns the lock into exact pins with hashes without
# consulting the index, and `uv pip install --require-hashes` installs those
# artifacts from whichever index UV_DEFAULT_INDEX points at. The index can
# therefore change between the public build and the internal mirror while the
# versions and hashes cannot: the mirror can only serve the artifacts the lock
# already records. The export deliberately uses --frozen rather than --locked
# because --locked also compares the lock against the configured index and
# would reject the mirror. CI guards lock freshness with `uv lock --check`.
#
# The build backend is locked too (`[dependency-groups] build` in
# pyproject.toml), so the project install below runs with
# --no-build-isolation and never pulls an unpinned hatchling from the index.

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

# Resolve dependencies first so this layer is cached until pyproject/uv.lock
# change. Every requirement is fully pinned and hash-checked by uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt \
        -o /tmp/requirements.txt \
    && uv export --frozen --only-group build --no-emit-project --format requirements-txt \
        -o /tmp/build-requirements.txt \
    && uv venv "${UV_PROJECT_ENVIRONMENT}" \
    && uv pip install --python "${UV_PROJECT_ENVIRONMENT}/bin/python" \
        --require-hashes \
        -r /tmp/requirements.txt \
        -r /tmp/build-requirements.txt

# Install the project itself against the locked environment. --no-deps keeps
# the resolver out entirely, and --no-build-isolation reuses the locked
# hatchling from the build group. --no-editable copies the package into
# site-packages instead of linking to ./src, so the runtime image doesn't depend
# on the source tree (and its permissions) being readable by the unprivileged
# user.
COPY README.md ./
COPY src ./src
RUN uv pip install --python "${UV_PROJECT_ENVIRONMENT}/bin/python" \
        --no-deps --no-build-isolation .

# ---- Runtime stage ----------------------------------------------------------
FROM ${PYTHON_IMAGE}

# git is required at runtime for mirroring; openssl generates the self-signed
# webhook TLS certificate in the entrypoint.
#
# apk's repository is parameterized for the same reason as the base images and
# PyPI index above: the egress-restricted GitLab/Kaniko runners cannot reach the
# public Alpine CDN. When APK_REPO_URL is set (the GitLab build) it points at the
# internal Alpine feed.
#
# The internal ProGet feed serves an UNSIGNED APKINDEX (it strips the upstream
# .SIGN.RSA member), so `apk add` cannot authenticate that index. The install is
# therefore split in two:
#
#   1. `apk fetch` resolves and downloads the packages using the untrusted index.
#   2. The index is removed and the local .apk files are installed WITHOUT
#      --allow-untrusted. apk verifies each package's own signature against the
#      Alpine keys this base image ships in /etc/apk/keys, so a substituted
#      git/openssl package is rejected. (Verified: a tampered .apk fails and is
#      not installed.)
#
# What this does NOT authenticate is the *index*: a hostile feed could offer a
# different Alpine-signed version (e.g. a rollback). A feed that preserves or
# re-signs the upstream index, with its key added to /etc/apk/keys, remains the
# proper fix. This split is the strongest control available against the current
# feed without reintroducing an unsigned install.
ARG APK_REPO_URL=
RUN if [ -n "$APK_REPO_URL" ]; then \
        echo "${APK_REPO_URL}/" > /etc/apk/repositories \
        && mkdir -p /tmp/apk-packages \
        && apk fetch --allow-untrusted --recursive --no-cache -o /tmp/apk-packages git openssl \
        && : > /etc/apk/repositories \
        && apk add --no-network --no-cache /tmp/apk-packages/*.apk \
        && rm -rf /tmp/apk-packages; \
    else \
        apk add --no-cache git openssl; \
    fi

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
