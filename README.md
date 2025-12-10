# G2G-Sync (GitHub to GitLab Mirror)

A lightweight, security-focused CLI tool to mirror repositories from GitHub to a private GitLab instance (or local backup). Designed for GRC compliance, backup strategies, and automated mirroring.

## Features

- **🛡️ Secure Tokens**: Uses environment variables for authentication. No credentials stored on disk.
- **⚡ Parallel Syncing**: Syncs multiple repositories simultaneously using thread pools.
- **🔄 Smart Watch Mode**: Intelligent watchdog that continuously mirrors updates.
    - Prevents redundant syncs (only syncs upon new pushes).
    - Bootstraps missing repositories automatically.
- **📂 Local Backup Mode**: Can function as a pure local backup tool (no GitLab required).
- **👀 Sidecar Checkout**: Optional feature to maintain a visible file tree alongside the bare git mirror.
- **🧹 Storage Management**: Custom raw storage paths.

---

## Installation

### Prerequisites
- Python 3.8+
- [uv](https://github.com/astral-sh/uv) (Recommended) or pip.

### Steps
1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/g2g-sync.git
   cd g2g-sync
   ```

2. Install dependencies:
   **Using uv (Recommended):**
   ```bash
   uv sync
   ```
   **Using pip:**
   ```bash
   pip install -r requirements.txt
   ```

---

## Configuration

Set your access tokens using environment variables or a `.env` file in the root directory.

```bash
# Required for GitHub Access
export GITHUB_TOKEN="your_github_pat"

# Required for GitLab Push (Optional if using --backup-only)
export GITLAB_TOKEN="your_gitlab_pat"

# Optional: Custom GitLab URL (Defaults to http://gitlab.local/api/v4)
export GITLAB_API_URL="https://gitlab.example.com/api/v4"
```

---

## Usage Guide

Run the script using `uv run` or `python`:

```bash
uv run python src/g2g.py [OPTIONS]
```

### Command Line Arguments

| Argument | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **`--dry-run`** | Flag | `False` | Simulate execution. Prints what would happen without making network changes. |
| **`--watch`** | Flag | `False` | Run continuously in IDLE/Daemon mode. Checks for updates periodically. |
| **`--interval`** | Int | `60` | Time in seconds to wait between sync cycles in watch mode. |
| **`--concurrency`** | Int | `5` | Number of parallel threads to use for syncing. Increase for faster throughput. |
| **`--backup-only`** | Flag | `False` | **Local Mode**: Skips pushing to GitLab. Useful for pure local backups. `GITLAB_TOKEN` not required. |
| **`--checkout`** | Flag | `False` | **Sidecar**: Creates a visible working tree (`repo/`) alongside the bare mirror (`repo.git`). |
| **`--storage`** | Path | `./mirror-data` | Directory where repositories will be stored. |
| **`--window`** | Int | `10` | Only syncs repositories updated in the last `X` minutes (Watch mode optimization). |
| **`--verbose`** | Flag | `False` | Enable detailed logging (useful for debugging). |

---

## Common Examples

### 1. Simple Dry Run
Check what valid repositories are found and what would be synced.
```bash
uv run python src/g2g.py --dry-run
```

### 2. Full Local Backup (With Visible Files)
Backup all your repositories to a custom folder, preserving visible files, using 10 concurrent threads.
```bash
uv run python src/g2g.py --backup-only --checkout --concurrency 10 --storage /Volumes/BackupDrive/GitHub
```

### 3. Continuous Mirror to GitLab
Run as a daemon, checking every 30 seconds, syncing to your GitLab instance.
```bash
uv run python src/g2g.py --watch --interval 30 --concurrency 5
```
*Note: This requires `GITLAB_TOKEN` to be set.*

### 4. Quick Catch-up
Force a sync of all repositories changed in the last 24 hours (1440 minutes).
```bash
uv run python src/g2g.py --window 1440
```
