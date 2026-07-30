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

**Holocron** is a powerful Python application designed to mirror your GitHub, GitLab or Azure DevOps repositories to a local directory or a self-hosted GitLab instance. It supports parallel syncing, continuous watch mode, and local-only backups (no GitLab required).

## Why Holocron?
> "Why not just run `git pull` and `git push` in a cron job?"

While a simple script works for one repo, managing hundreds requires a robust tool. Holocron solves the common headaches of mass-mirroring:

1.  **True Mirroring**: Uses `git clone --mirror` to perfectly replicate **all** refs (branches, tags, notes, and Pull Request refs), not just the default branch.
2.  **Automated Discovery**: Automatically finds all repositories (including new ones) in your user or organization account. You don't need to maintain a list.
3.  **Smart Sync**: Avoids redundant work by checking the `pushed_at` timestamp. If a repo hasn't changed, it isn't touched.
4.  **Resilience**: Handles GitLab branch protection rules automatically (enabling "Allow Force Push" when needed) which usually blocks standard mirroring scripts.
5.  **Parallelism**: Syncs multiple repositories simultaneously, turning an hours-long serial backup into minutes.

## Features
- **Supported Sources**:
    - **GitHub** (github.com or GitHub Enterprise).
    - **GitLab** (gitlab.com or self-hosted).
    - **Azure DevOps Services** (cloud, `dev.azure.com`) — see [Azure DevOps as a source](#azure-devops-as-a-source).
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

> The container runs as an unprivileged user (UID `100`), not root. If you
> **bind-mount** a host directory onto `/app/mirror-data` (or `/certs` for
> webhook TLS), it must be writable by that UID — `chown -R 100:101 <dir>` or run
> with `--user "$(id -u):$(id -g)"`. See the [Docker Guide](DOCKER_GUIDE.md#-non-root-user-and-volume-permissions).

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
 | `AZURE_DEVOPS_TOKEN` | Azure DevOps Personal Access Token | **Yes** (if `--source azure`) |
 | `AZURE_DEVOPS_ORG_URL` | Azure DevOps organisation URL, e.g. `https://dev.azure.com/my-org` | **Yes** (if `--source azure`) |
 | `AZURE_DEVOPS_PROJECT` | Limit the Azure DevOps source to one project (default: every project in the org) | No |
 | `HOLOCRON_INCLUDE` | Comma-separated glob patterns; only matching repositories are mirrored | No |
 | `HOLOCRON_EXCLUDE` | Comma-separated glob patterns to skip (applied after includes) | No |
 | `HOLOCRON_REPO_LIST` | Path to a file holding one include pattern per line | No |
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

 #### Azure DevOps Token (`AZURE_DEVOPS_TOKEN`)

 API calls made: `GET /_apis/git/repositories`,
 `GET /_apis/git/repositories/{id}/pushes`, `git clone` — all read-only.

 **As source (the only supported role)**
 - Scope: **Code (Read)** (`vso.code`).
 - The PAT is sent as HTTP Basic auth with an empty username, which is what
   Azure DevOps expects for both the REST API and `git` over HTTPS.
 - A PAT is bound to a single organisation (or "all accessible organisations");
   mirror one organisation per Holocron instance.

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
| `--github-status` | False | Provision a per-project `GITHUB_REPO` CI/CD variable on GitLab so runners can report CI checks back to GitHub (GitHub→GitLab only) |
| `--source` | `github` | Source provider: `github`, `gitlab` or `azure` |
| `--destination` | `gitlab` | Destination provider: `github`, `gitlab` or `local` |
| `--azure-org-url` | _(none)_ | Azure DevOps organisation URL (required for `--source azure`) |
| `--azure-project` | _(none)_ | Limit the Azure DevOps source to a single project |
| `--include` | _(none)_ | Only mirror repositories matching these glob patterns (repeatable, comma-separated) |
| `--exclude` | _(none)_ | Skip repositories matching these glob patterns (applied after `--include`) |
| `--repo-list` | _(none)_ | File holding one include pattern per line — an explicit fetch list |

### Selecting which repositories to mirror

By default Holocron mirrors **everything** the source token can see. In an organisation
with hundreds or thousands of repositories that is rarely what you want, so the set can be
narrowed with glob patterns — this works for every source (GitHub, GitLab, Azure DevOps).

```bash
# An explicit fetch list -- one pattern per line, '#' comments allowed
cat > repos.txt <<'EOF'
# platform team
api
web-frontend
acme/shared-*      # a whole prefix
EOF
holocron --repo-list repos.txt

# Or inline; both flags are repeatable and accept comma-separated values
holocron --include 'api,web-*' --exclude '*-archive,*-sandbox'
```

**Semantics**

- Patterns are shell globs (`*`, `?`, `[seq]`), matched **case-insensitively**.
- Each pattern is matched against the repository's **mirror name** (`widgets`) *and* its
  fully-qualified source path when it has one — `acme/widgets` on GitHub,
  `MyProject/Widgets Repo` on Azure DevOps. So `acme/*` selects a whole GitHub org, and
  `MyProject/*` selects a whole Azure DevOps project.
- With **no** include patterns, everything is included. `--exclude` is applied after
  `--include` and always wins.
- `--repo-list` simply contributes more include patterns, so it composes with `--include`.
  A missing or unreadable list file is a **startup error** — quietly mirroring all 1,000
  repositories because a path was mistyped would be much worse.
- A filter that matches nothing logs a warning rather than silently mirroring an empty set.
- The filter applies to **webhook deliveries** too, so a push to an excluded repository is
  ignored rather than sneaking past the poll loop's selection.

Run with `--dry-run --verbose` first to see exactly which repositories the patterns select.

> **Azure DevOps note**: the filter is applied inside the source, before the per-repository
> last-push lookups, so filtering also cuts the API calls per cycle — in a 1,000-repo
> organisation that is the difference between ~1,001 requests and a handful. Mirror names
> are resolved over the full repository list *before* filtering, so narrowing the filter
> never renames an existing mirror.

### Azure DevOps as a source

Holocron can mirror **from Azure DevOps Services** (the cloud edition, `dev.azure.com`)
to GitLab or to local disk. Azure DevOps is **source-only** — Holocron does not create
or push to Azure DevOps repositories.

```bash
export AZURE_DEVOPS_TOKEN="your_pat"
export AZURE_DEVOPS_ORG_URL="https://dev.azure.com/my-org"
export GITLAB_TOKEN="your_gitlab_pat"
export GITLAB_API_URL="https://gitlab.example.com/api/v4"

holocron --source azure --destination gitlab --gitlab-namespace mirrors
```

Without `--azure-project`, every repository in **every project** of the organisation is
mirrored. The legacy `https://my-org.visualstudio.com` organisation URL works too — set
`AZURE_DEVOPS_ORG_URL` to whichever host your clone URLs actually use, because the PAT is
only ever sent to that host.

**Creating the PAT**: Azure DevOps → *User settings* → *Personal access tokens* → *New
Token*, scope **Code (Read)**. Copy it immediately; it is shown once.

**How repository names are mapped.** Azure DevOps repository names may contain spaces and
other characters that are not valid in a directory name, and they are only unique *within
a project*. Holocron therefore slugifies each name to the `[A-Za-z0-9._-]` charset
(`My Cool Repo` → `My-Cool-Repo`) and, when the same name exists in more than one project,
qualifies it with the project (`Alpha/docs` → `Alpha-docs`). Every remapping is logged as a
warning, so run once with `--dry-run` to review the destination names before mirroring.

**What is skipped**: disabled repositories, and repositories with no default branch (an
uninitialised repo has no refs to mirror).

**Last-activity lookups**: the Azure DevOps repository list carries no timestamp, so
`--window` filtering needs one extra `pushes` API call per repository per cycle. If that
lookup fails, the repository is treated as recently pushed rather than being skipped.

#### Testing an Azure DevOps mirror

1. **Automated tests** (no network, no credentials — part of the normal suite):
   ```bash
   uv run pytest tests/test_azure_provider.py tests/test_azure_e2e.py -v
   ```
   `test_azure_provider.py` stubs `requests.get` with recorded Azure DevOps payloads and
   covers pagination, name mapping, timestamp parsing and the credential host-pinning
   rules. `test_azure_e2e.py` goes further: it serves a stub Azure DevOps API *and* a real
   git repository from a loopback socket, then runs the actual sync engine against it, so
   `git clone --mirror` really executes against the URL the provider builds.

2. **Check the API and PAT by hand** before involving Holocron:
   ```bash
   curl -u :"$AZURE_DEVOPS_TOKEN" \
     "$AZURE_DEVOPS_ORG_URL/_apis/git/repositories?api-version=7.1"
   ```
   A JSON document means the PAT works. **HTML** means it does not: Azure DevOps answers an
   unauthenticated request with `203` and a sign-in page rather than a `401`. Holocron
   detects this case and reports it as a token problem instead of a JSON parse error.

3. **Dry run** — lists what would be mirrored, and shows the destination names after
   slugifying, without cloning anything:
   ```bash
   holocron --source azure --destination local --dry-run --verbose
   ```

4. **Local backup** — a real clone with no GitLab involved:
   ```bash
   holocron --source azure --destination local --storage ./mirror-data --verbose
   ls ./mirror-data          # one <repo>.git bare mirror per repository
   ```

5. **To GitLab** — point at a throwaway namespace first (a local
   `gitlab/gitlab-ce` container or a personal group on gitlab.com):
   ```bash
   holocron --source azure --destination gitlab --gitlab-namespace mirror-test --verbose
   ```
   Verify on the GitLab side that branches **and** tags arrived
   (`git ls-remote <gitlab-url>` against the source's `git ls-remote`).

6. **Watch mode** — push a commit in Azure DevOps and confirm the next cycle picks it up:
   ```bash
   holocron --source azure --destination gitlab --watch --interval 60 --window 10 --verbose
   ```
   (Webhook mode is GitHub-only; Azure DevOps syncs are poll-driven.)

### Webhook Mode (push-triggered sync)

Instead of waiting for the next poll cycle, Holocron can sync a repository the
moment it changes. With `--webhook`, it starts a small HTTP listener that
accepts GitHub `push` and `pull_request` events, and syncs just the affected
repo asynchronously.

Reacting to `pull_request` events (the `opened`, `synchronize`, and `reopened`
actions) means a PR opened or updated on GitHub triggers a sync straight away —
useful for running CI on the mirrored side. Holocron does **not** create any
merge request or branch on the destination; it just refreshes the mirror, which
already carries the PR head refs (`refs/pull/*`, including fork PRs).

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
   - **Events**: select *Let me select individual events* and tick **Pushes**
     and **Pull requests** (or keep *Just the push event* if you only want
     push-triggered syncs)

Every delivery is authenticated via the `X-Hub-Signature-256` HMAC header. Valid
pushes return `202 Accepted` immediately (well inside GitHub's delivery timeout)
and are synced on a background thread. Concurrent poll- and webhook-triggered
syncs of the same repo are serialized so they never corrupt the mirror.

Anything that isn't a validly-signed delivery — a missing/invalid signature, a
wrong path or method, a plain browser `GET` — receives a uniform, unbranded
`404`, and the listener does not send a `Server:` banner. This avoids
advertising to scanners that a webhook endpoint is here.

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

> **Restricting access to GitHub's IPs:** since deliveries only ever come from
> GitHub, restrict the port at your host/network firewall to GitHub's published
> webhook source ranges (the `hooks` field of <https://api.github.com/meta>), or
> keep the listener off the public internet entirely (reverse proxy / tunnel /
> VPN). A firewall `DROP` is also the only way to make the port itself appear
> closed to scanners — the app can't hide an open listening socket.

### Reporting CI checks back to GitHub

If you mirror **GitHub → GitLab** and run your CI on the GitLab side (jobs in each
repo's `.gitlab-ci.yml`), Holocron can help those GitLab jobs report their results
back onto the matching **GitHub commit** — so contributors see per-job checks on
the GitHub PR while the pipeline actually runs on GitLab.

**Why no PR number is needed:** GitHub commit statuses are keyed by **commit SHA**,
not by PR. Because Holocron mirrors with `git push --mirror`, the commit SHA on
GitLab is identical to the one on GitHub. A GitLab job already has it in
`$CI_COMMIT_SHA`, posts a status to that SHA, and GitHub shows it on whichever PR
has that commit as its head — automatically. (This covers branches pushed within
the GitHub repo. PRs opened from **forks** are not covered, since their head lives
in `refs/pull/*` rather than a branch.)

**The one piece Holocron provides:** the GitHub `owner/repo` a mirror came from.
Run with `--github-status` (or `HOLOCRON_GITHUB_STATUS=true`) and Holocron sets a
non-secret, per-project GitLab CI/CD variable `GITHUB_REPO` on each mirrored
project.

**Setup**

1. Run Holocron with `--github-status` (GitHub source, GitLab destination).
2. In GitLab, add **one group-level, *masked*** CI/CD variable
   `GITHUB_STATUS_TOKEN` holding a GitHub token with the `repo:status` scope. Set
   it at the group so it inherits to every mirrored project — do **not** put a
   secret in each project.
3. In the repos you want reported, extend the `.github-check` template (a
   ready-to-use, commented copy lives in this repo's `.gitlab-ci.yml`) from the
   jobs you care about:
   ```yaml
   test:
     extends: .github-check
     script:
       - ...
   ```

Each such job reports itself as a separate GitHub check named `ci/gitlab/<job>`,
moving from `pending` to `success`/`failure`, and links back to the GitLab job
log. The job image needs `curl` (swap for `wget` otherwise). The template's
empty-variable guards make it a harmless no-op in projects that haven't opted in.

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
