#!/usr/bin/env bash
# Build the Holocron container image locally and push it to Artifactory -- the
# same image, registry, and tags the GitLab `docker-publish` job produces, for
# when you want to publish from your workstation instead of via CI.
#
# Usage:
#   scripts/build-push.sh            # -> :latest and :<git-short-sha>   (like the default-branch CI rule)
#   scripts/build-push.sh v1.6.1     # -> :v1.6.1 and :latest            (like the tag CI rule)
#
# Requires Docker with buildx (Docker Desktop ships it) and these variables,
# read from the environment or a local .env file:
#   ARTIFACTORY_REGISTRY  registry host, e.g. mycompany.jfrog.io
#   ARTIFACTORY_IMAGE     full image path without a tag,
#                         e.g. mycompany.jfrog.io/docker-local/holocron
#   ARTIFACTORY_USER      username / service account
#   ARTIFACTORY_TOKEN     API token or password
#
# The image is built for $PLATFORM (default linux/amd64, matching the CI
# runners) so it runs on the same infrastructure regardless of your host arch.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Load a local .env if present so ARTIFACTORY_* can live there.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

missing=""
for v in ARTIFACTORY_REGISTRY ARTIFACTORY_IMAGE ARTIFACTORY_USER ARTIFACTORY_TOKEN; do
  eval "val=\${$v:-}"
  [ -n "$val" ] || missing="$missing $v"
done
if [ -n "$missing" ]; then
  echo "ERROR: missing required variable(s):$missing" >&2
  echo "Set them in your environment or in a local .env file at the repo root." >&2
  exit 1
fi

PLATFORM="${PLATFORM:-linux/amd64}"

# Tag set mirrors the CI rules: a version arg -> :<version> + :latest;
# otherwise a rolling :latest + :<git-short-sha>.
version="${1:-}"
if [ -n "$version" ]; then
  tags=("$ARTIFACTORY_IMAGE:$version" "$ARTIFACTORY_IMAGE:latest")
else
  sha="$(git rev-parse --short HEAD)"
  tags=("$ARTIFACTORY_IMAGE:latest" "$ARTIFACTORY_IMAGE:$sha")
fi

echo "Logging in to $ARTIFACTORY_REGISTRY as $ARTIFACTORY_USER ..."
printf '%s' "$ARTIFACTORY_TOKEN" | docker login "$ARTIFACTORY_REGISTRY" -u "$ARTIFACTORY_USER" --password-stdin

tag_args=()
for t in "${tags[@]}"; do tag_args+=(-t "$t"); done

echo "Building $PLATFORM image and pushing: ${tags[*]}"
# --provenance=false keeps the push to a plain image manifest (some registries
# reject buildx's default OCI attestation manifests), matching Kaniko's output.
docker buildx build \
  --platform "$PLATFORM" \
  --pull \
  --provenance=false \
  "${tag_args[@]}" \
  --push \
  .

echo "Done. Pushed:"
for t in "${tags[@]}"; do echo "  $t"; done
