# Holocron
<!-- trigger: 1.0.0 -->
> **The "Ultimate" Git Mirroring Tool**


```
          /\
         /  \
        / /\ \
       / /  \ \
      / /    \ \
     /_/______\_\
     \ \      / /
      \ \    / /
       \ \  / /
        \ \/ /
         \  /
          \/
```

**Holocron** is a powerful Python application designed to mirror your GitHub repositories to a local directory or a self-hosted GitLab instance. It supports parallel syncing, continuous watch mode, and local-only backups (no GitLab required).

## Why Holocron?
> "Why not just run `git pull` and `git push` in a cron job?"

While a simple script works for one repo, managing hundreds requires a robust tool. Holocron solves the common headaches of mass-mirroring:

1.  **True Mirroring**: Uses `git clone --mirror` to perfectly replicate **all** refs (branches, tags, notes, and Pull Request refs), not just the default branch.
2.  **Automated Discovery**: Automatically finds all repositories (including new ones) in your user or organization account. You don't need to maintain a list.
3.  **Smart Sync**: Avoids redundant work by checking the `pushed_at` timestamp. If a repo hasn't changed, it isn't touched.
4.  **Resilience**: Handles GitLab branch protection rules automatically (enabling "Allow Force Push" when needed) which usually blocks standard mirroring scripts.
5.  **Parallelism**: Syncs multiple repositories simultaneously, turning an hours-long serial backup into minutes.

## Features
- **Supported Destinations**:
    - **GitLab**: Full mirror with automatic creation/updates (requires existing empty project or "create on push").
    - **Local Disk**: Create a local-only backup archive without needing a second Git server.
- **Parallel Syncing**: Sync multiple repositories concurrently for maximum speed.
- **Continuous Watch Mode**: Polls for changes and syncs only when necessary.
- **Sidecar Checkout**: Creates a bare mirror (`.git` folder) for safety AND an optional viewable checkout for easy browsing.
- **Dockerized**: Runs as a lightweight container.

## Quick Start

### PyPI (pip / uv)
Valuable for local usage or scripting.
```bash
pip install holocron-sync
# or
uv tool install holocron-sync
```



### Docker (Recommended for continuous operation)
Run Holocron instantly with a single command:

```bash
docker run -d \
  -e GITHUB_TOKEN="your_github_token" \
  -v $(pwd)/mirror-data:/app/mirror-data \
  ghcr.io/someniak/holocron
```

For full configuration options, environment variables, and Docker Compose examples, please refer to the **[Docker Guide](DOCKER_GUIDE.md)**.


## Running from Source
```bash
# Install dependencies
uv sync

# Run a one-time backup of all your repos locally (visible files)
export GITHUB_TOKEN=your_token
uv run holocron --backup-only --checkout --concurrency 10
```

## Configuration
Holocron uses environment variables for secrets:
 
 | Variable | Description | Required | 
 | :--- | :--- | :--- |
 | `GITHUB_TOKEN` | Your GitHub Personal Access Token (repo scope) | **Yes** |
 | `GITLAB_TOKEN` | Your GitLab Personal Access Token (api scope) | No (if `--backup-only`) |
 | `GITLAB_API_URL` | URL to your GitLab API (default: `http://gitlab.local/api/v4`) | No (if `--backup-only`) |
 | `GITHUB_API_URL` | URL to your GitHub API (default: `https://api.github.com`) | No |
 | `HOLOCRON_WEBHOOK_SECRET` | Shared secret used to verify GitHub webhook signatures | **Yes** (if `--webhook`) |
 | `HOLOCRON_WEBHOOK_CERT` / `HOLOCRON_WEBHOOK_KEY` | TLS cert/key for the webhook listener (auto-generated in Docker) | No |
 
 ### API Permissions

 Required scopes depend on whether a provider is used as the **source** (read-only)
 or the **destination** (read/write). Grant the minimum for your `--source` /
 `--destination` combination.

 #### GitHub Token (`GITHUB_TOKEN`)

 API calls made: `GET /user/repos`, `GET /user/orgs`, `GET /orgs/{org}/repos`,
 `git clone` (source); plus `GET`/`PUT /repos/{owner}/{repo}/branches/{branch}/protection`
 and `git push` (destination).

 **As source (read-only)**
 - *Classic PAT:* `repo` (private repos) — or `public_repo` for public only — **plus** `read:org` (for the organization endpoints).
 - *Fine-grained PAT:* Repository permissions → **Contents: Read** and **Metadata: Read** (mandatory). A fine-grained token is scoped to a single owner, so to mirror organization repos it must be issued for / approved by that org; otherwise use a classic token with `read:org`.

 **As destination (adds write + branch-protection management)**
 - *Classic PAT:* `repo` (includes the `administration` rights needed to relax branch protection for force-push).
 - *Fine-grained PAT:* **Contents: Read and Write** (push) **and** **Administration: Read and Write** (to toggle `allow_force_pushes` on protected branches). Without Administration access the protection update is skipped with a warning and a protected-branch push may fail.

 #### GitLab Token (`GITLAB_TOKEN`)

 API calls made: `GET /projects` (source); plus `GET /projects/:path`,
 `GET`/`PATCH /projects/:id/protected_branches/:branch`, `git push` (destination).

 **As source (read-only)**
 - `read_api` + `read_repository` — or the broader `api`.

 **As destination**
 - `api` — required, because relaxing a protected branch for force-push uses the write API (`PATCH .../protected_branches`); `write_repository` alone only permits `git push`, not API writes.
 - The token's user must have the **Maintainer** or **Owner** role on the target namespace/project, or the branch-protection update and push will fail.

 > If a token lacks the branch-protection permission, Holocron logs a warning (with the HTTP status and a scope hint) and continues; the subsequent push simply fails for any protected branch rather than crashing the whole run.

### Command Line Arguments
| Flag | Default | Description |
| :--- | :--- | :--- |
| `--watch` | False | Run continuously in a loop |
| `--interval` | 60 | Seconds to sleep between checks in watch mode |
| `--window` | 60 | Sync only repos pushed within the last N minutes |
| `--backup-only` | False | Mirror locally only, do not push to GitLab |
| `--checkout` | False | Create a visible working directory alongside the mirror |
| `--concurrency` | 5 | Number of parallel sync threads |
| `--storage` | `./mirror-data` | Directory to store repositories |
| `--dry-run` | False | Print what would happen without doing it |
| `--verbose` | False | Enable detailed debug logging |
| `--webhook` | False | Start an HTTP listener that syncs a repo on GitHub `push` events |
| `--webhook-port` | 8080 | Port for the webhook listener |
| `--webhook-path` | `/webhook` | URL path the listener serves |
| `--webhook-cert` | _(none)_ | TLS certificate file — serves HTTPS (pair with `--webhook-key`) |
| `--webhook-key` | _(none)_ | TLS private key file (pair with `--webhook-cert`) |

### Webhook Mode (push-triggered sync)

Instead of waiting for the next poll cycle, Holocron can sync a repository the
moment it changes. With `--webhook`, it starts a small HTTP listener that
accepts GitHub `push` events, and syncs just the affected repo asynchronously.

It runs **alongside** `--watch`, so polling remains a safety net for any missed
deliveries. Used without `--watch`, Holocron runs one initial full sync and then
stays up to serve deliveries.

**Setup**

1. Run with the listener enabled and a secret set:
   ```bash
   HOLOCRON_WEBHOOK_SECRET="your-random-secret" holocron --watch --webhook --webhook-port 8080
   ```
2. In your GitHub repo (or org) **Settings -> Webhooks -> Add webhook**:
   - **Payload URL**: `http://your-host:8080/webhook`
   - **Content type**: `application/json`
   - **Secret**: the same value as `HOLOCRON_WEBHOOK_SECRET`
   - **Events**: *Just the push event*

Every delivery is authenticated via the `X-Hub-Signature-256` HMAC header;
requests with a missing or invalid signature are rejected with `401`. Valid
pushes return `202 Accepted` immediately (well inside GitHub's delivery timeout)
and are synced on a background thread. Concurrent poll- and webhook-triggered
syncs of the same repo are serialized so they never corrupt the mirror.

#### TLS / HTTPS

The listener serves plain HTTP by default. To serve **HTTPS**, pass a certificate
and key (they must be provided together):

```bash
HOLOCRON_WEBHOOK_SECRET="..." holocron --watch --webhook \
  --webhook-cert /certs/webhook.crt --webhook-key /certs/webhook.key
```

**In Docker this is automatic.** When the webhook is enabled, the container
entrypoint generates a self-signed certificate at `/certs/webhook.crt` on first
start (openssl is bundled in the image). To **replace** it with your own
certificate, mount it over that path (a declared volume) — an existing cert is
used as-is and never regenerated:

```bash
docker run -d \
  -e GITHUB_TOKEN="..." -e GITLAB_TOKEN="..." \
  -e HOLOCRON_WEBHOOK_SECRET="your-random-secret" \
  -e HOLOCRON_WEBHOOK=true -e HOLOCRON_WATCH=true \
  -p 8443:8080 \
  -v "$(pwd)/certs:/certs" \
  -v "$(pwd)/mirror-data:/app/mirror-data" \
  ghcr.io/someniak/holocron
```

Override the cert paths with `HOLOCRON_WEBHOOK_CERT` / `HOLOCRON_WEBHOOK_KEY`, and
the generated cert's hostname with `HOLOCRON_WEBHOOK_CN` (default `holocron`).

> **Note on self-signed certs and github.com:** public GitHub rejects a
> self-signed endpoint unless you tick *"Disable SSL verification"* on the
> webhook. For public repos, prefer a real certificate or a TLS-terminating
> reverse proxy in front of Holocron; self-signed is best suited to self-hosted
> GitHub Enterprise or internal networks.

## Development

### Running Tests
To run the test suite:
```bash
uv run pytest
```

With coverage:
```bash
uv run pytest --cov=src
```

## Release Process

Holocron uses a manually triggered release workflow:

1.  **Prepare Release**: Go to Actions -> **Prepare Release** and run it with the new version (e.g., `1.2.0`). This creates a `release/v1.2.0` branch.
2.  **Verify**: Ensure CI passes on the release branch.
3.  **Publish**: Create and push a tag `v1.2.0` (or merge the release PR and tag main).
    - `git tag v1.2.0`
    - `git push origin v1.2.0`
    - This triggers Docker and PyPI publishing.
