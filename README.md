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
uv run python src/holocron.py --backup-only --checkout --concurrency 10
```

## Configuration
Holocron uses environment variables for secrets:
 
 | Variable | Description | Required | 
 | :--- | :--- | :--- |
 | `GITHUB_TOKEN` | Your GitHub Personal Access Token (repo scope) | **Yes** |
 | `GITLAB_TOKEN` | Your GitLab Personal Access Token (api scope) | No (if `--backup-only`) |
 | `GITLAB_API_URL` | URL to your GitLab API (default: `http://gitlab.local/api/v4`) | No (if `--backup-only`) |
 | `GITHUB_API_URL` | URL to your GitHub API (default: `https://api.github.com`) | No |
 
 ### API Permissions
 Ensure your tokens have the minimum required scopes:
 
 **GitHub Token (`GITHUB_TOKEN`)**
 - `repo` (Full control of private repositories) - *Required for reading private repos and modifying branch protection*
 - `read:org` (Read org and team membership) - *Required for fetching organization repos*
 
 **GitLab Token (`GITLAB_TOKEN`)**
 - `api` (Grants complete read/write access to the API) - *Simplest option*
 - OR `read_repository` + `write_repository` - *More granular control*

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
