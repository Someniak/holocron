# Docker Usage Guide

This guide explains how to run Holocron using Docker and configure it using Environment Variables.

## 🚀 Quick Start

```bash
docker run -d \
  -e GITHUB_TOKEN="your_github_pat" \
  -e GITLAB_TOKEN="your_gitlab_pat" \
  -v $(pwd)/mirror-data:/app/mirror-data \
  ghcr.io/someniak/holocron:latest
```

## ⚙️ Configuration

You can configure Holocron entirely using Environment Variables. This is ideal for containerized deployments (Docker, Kubernetes).

### Authentication (Required)

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub Personal Access Token (PAT) with `repo` scope. |
| `GITLAB_TOKEN` | GitLab Personal Access Token (PAT) with `read_api` and `write_repository` scopes. |
| `AZURE_DEVOPS_TOKEN` | Azure DevOps PAT with the `Code (Read)` scope. Only needed with `HOLOCRON_SOURCE=azure`. |
| `AZURE_DEVOPS_ORG_URL` | Azure DevOps organisation URL, e.g. `https://dev.azure.com/my-org`. Required with `HOLOCRON_SOURCE=azure`. |
| `AZURE_DEVOPS_PROJECT` | Optional: limit the Azure DevOps source to a single project. |
| `HOLOCRON_INCLUDE` | Optional: comma-separated glob patterns; only matching repositories are mirrored. |
| `HOLOCRON_EXCLUDE` | Optional: comma-separated glob patterns to skip (applied after includes). |
| `HOLOCRON_REPO_LIST` | Optional: path to a mounted file holding one include pattern per line. |

### Core Settings

| Variable | CLI Argument | Default | Description |
|----------|--------------|---------|-------------|
| `HOLOCRON_SOURCE` | `--source` | `github` | Source provider (`github` or `gitlab`). |
| `HOLOCRON_DESTINATION` | `--destination` | `gitlab` | Destination provider (`github`, `gitlab`, or `local`). |
| `HOLOCRON_DRY_RUN` | `--dry-run` | `false` | Simulate execution without mirroring. |
| `HOLOCRON_BACKUP_ONLY` | `--backup-only` | `false` | Mirror locally only, skip pushing to destination. |

### Sync Behavior

| Variable | CLI Argument | Default | Description |
|----------|--------------|---------|-------------|
| `HOLOCRON_WATCH` | `--watch` | `false`* | Run in a loop (daemon mode). |
| `HOLOCRON_INTERVAL` | `--interval` | `60` | Seconds to wait between sync cycles (only in watch mode). |
| `HOLOCRON_WINDOW` | `--window` | `10` | Only sync repositories updated in the last X minutes. |
| `HOLOCRON_CONCURRENCY` | `--concurrency` | `5` | Number of concurrent threads for syncing. |

*\*Note: The default Docker image `CMD` enables `--watch` mode. To disable it, override the container command.*

### Advanced Configuration

| Variable | CLI Argument | Default | Description |
|----------|--------------|---------|-------------|
| `GITLAB_NAMESPACE` | `--gitlab-namespace` | (Root) | Target GitLab Group or User (e.g., `my-group`). |
| `HOLOCRON_STORAGE` | `--storage` | `/app/mirror-data` | Path inside container to store git mirrors. |
| `HOLOCRON_CHECKOUT` | `--checkout` | `false` | Create a working directory checkout alongside the bare mirror. |
| `HOLOCRON_VERBOSE` | `--verbose` | `false` | Enable detailed debug logging. |
| `GITHUB_API_URL` | N/A | `https://api.github.com` | Override GitHub API URL (for Enterprise). |
| `GITLAB_API_URL` | N/A | `http://gitlab.local/api/v4` | Override GitLab API URL (for Self-Hosted). |

## 🐳 Docker Compose Example

Create a `docker-compose.yml`:

```yaml
version: "3.8"
services:
  holocron:
    image: ghcr.io/someniak/holocron:latest
    restart: unless-stopped
    environment:
      - GITHUB_TOKEN=${GITHUB_TOKEN}
      - GITLAB_TOKEN=${GITLAB_TOKEN}
      - HOLOCRON_WATCH=true
      - HOLOCRON_INTERVAL=300
      - HOLOCRON_WINDOW=60
      - GITLAB_NAMESPACE=my-backup-group
    volumes:
      - ./data:/app/mirror-data
```

## 🏗️ Building the Image

The image is built from source with a multi-stage `uv` build, so it ships the
exact dependency versions pinned in `uv.lock`. Two pipelines build it:

- **GitHub Actions** publishes the image to GHCR on release (see `RELEASE.md`).
- **GitLab CI** (`.gitlab-ci.yml`) builds the image with Kaniko on GitLab
  runners and pushes it to Artifactory. It pushes `:latest` + a short-SHA tag on
  the default branch, `:<tag>` + `:latest` on git tags, and validates the build
  (no push) on merge requests. Configure these masked CI/CD variables in GitLab
  (Settings -> CI/CD -> Variables):

  | Variable | Example | Purpose |
  |---|---|---|
  | `ARTIFACTORY_REGISTRY` | `mycompany.jfrog.io` | Docker registry host (for auth) |
  | `ARTIFACTORY_IMAGE` | `mycompany.jfrog.io/docker-local/holocron` | Full image path, no tag |
  | `ARTIFACTORY_USER` | `svc-holocron` | Artifactory user / service account |
  | `ARTIFACTORY_TOKEN` | *(masked)* | Artifactory API token or password |

To build locally: `docker build -t holocron:local .`

## 🔒 Non-root user and volume permissions

The image runs as an unprivileged user (`holocron`, UID `100`), not root. The
paths the app writes to inside the image — `/app/mirror-data` and the `/certs`
cert volume — are pre-owned by that user, so the default (anonymous) volumes and
the auto-generated TLS cert work out of the box.

If you **bind-mount a host directory** onto one of those paths, it keeps its host
ownership, and UID `100` must be able to write to it. Otherwise git mirroring
(to `/app/mirror-data`) or webhook cert generation (to `/certs`) will fail with
permission errors. Fix it one of two ways:

```bash
# Option A: make the host directory writable by the container user (UID 100)
mkdir -p ./mirror-data && sudo chown -R 100:101 ./mirror-data

# Option B: run the container as your own host UID/GID (must own the mount)
docker run -d --user "$(id -u):$(id -g)" \
  -e GITHUB_TOKEN="..." -e GITLAB_TOKEN="..." \
  -v "$(pwd)/mirror-data:/app/mirror-data" \
  ghcr.io/someniak/holocron:latest
```

> If you run with `--webhook` on a bind-mounted `/certs`, that directory must be
> writable by the running UID too — or mount a cert you generated yourself, which
> is used as-is (never regenerated).

## 📦 Building and publishing to Artifactory

The public image is published to GHCR by GitHub Actions. A separate internal
image is published to Artifactory by the GitLab `docker-publish` job (Kaniko),
which builds and pushes on every default-branch commit (`:latest` +
`:<short-sha>`) and on each mirrored tag (`:<tag>` + `:latest`). Tags are
carried over from GitHub by the mirror, so cutting a GitHub release publishes
the matching Artifactory image with no manual step. It runs only where the
`ARTIFACTORY_*` CI/CD variables are set.

## ⚠️ Notes

- **URL Construction**: Syncing works by cloning from Source and pushing to Destination.
- **GitLab Namespaces**: If you are syncing to a GitLab Group, ensure `GITLAB_NAMESPACE` is set correctly.
- **Submodules**: Git submodules with invalid URLs in their history may cause sync failures. Use `--verbose` (or `HOLOCRON_VERBOSE=true`) to debug.
