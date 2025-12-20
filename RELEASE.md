# Release Guide (Maintainers Only)

This repository follows a **Release Branch** workflow. The release process is partially automated to ensure consistency and stability.

## Release Process

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
*   Checks that the version matches `pyproject.toml`.
*   Creates and pushes the git tag `v1.2.0`.
*   Creates a GitHub Release.
*   Triggers the build and publish steps for:
    *   **Docker Image**: Pushes to GHCR (`ghcr.io/someniak/holocron:1.2.0` and `latest`).
    *   **PyPI**: Publishes the package to PyPI.

### Pro-Tip: Release Candidates
If you entered a pre-release version (e.g., `1.2.0rc1`) in the **Prepare Release** step:
1.  The system detects it as a pre-release.
2.  Docker images are pushed *without* the `latest` tag.
3.  The GitHub Release is marked as "Pre-release".

**To promote to final:**
Simply run the **Prepare Release** workflow again with the final version (e.g., `1.2.0`). This will bump the version from `rc1` to final, create a new PR, and once merged, you can run **Publish Release**.

### 5. Automated Release Notes
We use `release-drafter` to keep track of changes.
*   As PRs are merged to `main`, a draft release is continuously updated on GitHub.
*   The **Publish Release** workflow will attach these notes to the final release.

