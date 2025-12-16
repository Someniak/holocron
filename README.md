# Holocron
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

**Holocron** is a powerful Python application designed to mirror your GitHub repositories to a local directory or a self-hosted GitLab instance. It supports parallel syncing, continuous watch mode, and local-only backups (no GitLab required). Ideal is you need a local backup for whatever reason.   TEST



## Features
- **Parallel Syncing**: Sync multiple repositories concurrently for maximum speed.
- **Continuous Watch Mode**: Polls for changes and syncs only when necessary (smart redundancy checks).
- **Two-Way Mirroring**: Creates a bare mirror (`.git` folder) for safety AND an optional checkout for visibility.
- **Dockerized**: Runs as a lightweight container.
- **Backup Level**: 
    - Full: GitHub -> Local -> GitLab
    - Backup-Only: GitHub -> Local (No GitLab token needed)

## Quick Start

### PyPI (pip / uv)
Valuable for local usage or scripting.
```bash
pip install holocron-sync
# or
uv tool install holocron-sync
```

### Docker (Recommended)
For continuous operation, pull the official image:
```bash
docker pull ghcr.io/someniak/holocron
```

Or run via Docker Compose:
```bash
docker-compose up -d --build
```
This starts Holocron in watch mode.


## Manual Run
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
| `GITLAB_API_URL` | URL to your GitLab API (default: `http://gitlab.local/api/v4`) | No |

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
Run tests with coverage:
```bash
uv run pytest --cov=src
```
