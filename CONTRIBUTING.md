# Contributing to Holocron

Thank you for your interest in contributing to Holocron! We welcome contributions from everyone.

## Development Setup

We use `uv` for dependency management and running tasks.

1.  **Install uv**: [Follow instructions here](https://github.com/astral-sh/uv)
2.  **Clone the repo**:
    ```bash
    git clone https://github.com/someniak/holocron.git
    cd holocron
    ```
3.  **Install dependencies**:
    ```bash
    uv sync
    ```

## Running Tests

Run the test suite with `uv`:

```bash
uv run pytest
```

With coverage:
```bash
uv run pytest --cov=src
```

## Release Process

We follow a **Release Branch** workflow for releases. This process is partially automated to ensure consistency.

### 1. Prerequisite
Ensure all feature PRs to be included in the release are merged into `main`.

### 2. Prepare Release (Automated)
Instead of manually creating branches, use the **Prepare Release** workflow:

1.  Go to the **Actions** tab in GitHub.
2.  Select the **Prepare Release** workflow.
3.  Click **Run workflow**.
4.  Enter the new version number (e.g., `1.2.0`).

**What this does:**
*   Creates a new branch `release/v1.2.0`.
*   Bumps the version in `pyproject.toml` (and other files if configured).
*   Commits and pushes the branch.
*   Opens a Pull Request from `release/v1.2.0` to `main`.

### 3. Verification
CI checks will run automatically on the Pull Request. **Ensure all checks pass** before proceeding.

In addition to unit tests, our CI pipeline runs:
*   **Nightly Builds**: To ensure long-term stability.
*   **Smoke Tests**:
    *   **PyPI**: Builds the package and verifies it installs and runs in a fresh environment.
    *   **Docker**: Builds the container and verifies it starts up correctly.

This validates that the codebase is stable with the new version bump.

### 4. Release & Publish (Manual Tag)
Once the release branch is verified:

1.  **Merge** the release PR into `main`.
2.  **Create a Tag** on `main` corresponding to the version.
    ```bash
    git checkout main
    git pull
    git tag v1.2.0
    git push origin v1.2.0
    ```
    *(Alternatively, you can tag the release-branch commit directly if you prefer, but merging to main first is standard).*

**What this does:**
*   Pushing the tag `v*` triggers the **Publish** workflows.
*   **Docker Image**: Built and pushed to GHCR (`ghcr.io/someniak/holocron:1.2.0` and `latest`).
*   **PyPI**: Build and published to PyPI.

### 5. Automated Release Notes
We use `release-drafter` to keep track of changes.
*   As PRs are merged to `main`, a draft release is continuously updated on GitHub.
*   When you push the tag, you simply need to edit the drafted release on GitHub and publish it.

---

## Code Style

*   We use standard Python styling (PEP 8).
*   Type hints are encouraged.
