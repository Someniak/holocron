# G2G-Sync (GitHub to GitLab Mirror)

A lightweight, security-focused CLI tool to mirror repositories from GitHub to a private GitLab instance. Designed for GRC compliance, backup strategies, and automated mirroring.

## Features
* **Smart Sync:** Uses timestamp filtering to only sync repositories that have changed (saves bandwidth).
* **Unidirectional Mirror:** Enforces GitHub as the "Source of Truth" (overwrites GitLab changes).
* **Automated:** Runs as a CLI tool or a Docker container.
* **Secure:** Uses environment variables for tokens; no credentials stored on disk.

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/g2g-sync.git
   cd g2g-sync
   ```

2.  Install dependencies:
    
    **Using uv (Recommended):**
    ```bash
    uv sync
    ```

    **Using pip:**
    ```bash
    pip install -r requirements.txt
    ```

## Usage

**Prerequisite:** Set your access tokens (or use a `.env` file).

```bash
export GITHUB_TOKEN="your_github_pat"
export GITLAB_TOKEN="your_gitlab_pat"
```

### Running with uv

```bash
uv run python src/g2g.py --help
```

### 1. Dry Run (Safe Mode)

Check what would happen without actually downloading code.

```bash
python src/g2g.py --dry-run --verbose
```

### 2. Manual Run (One-time)

Sync all repositories immediately and exit.

```bash
python src/g2g.py --window 1440  # Sync everything changed in the last 24 hours
```

### 3. Watch Mode (Daemon)

Run continuously, checking for updates every 60 seconds.

```bash
python src/g2g.py --watch --interval 60
```

## Docker Support

(Coming soon - mapped to Milestone 2)
