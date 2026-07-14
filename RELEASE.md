# Release Guide (Maintainers Only)

This repository follows the **CHANGELOG-driven** release model: a single
protected `main`, and one `Release` workflow that reads the version from
`CHANGELOG.md`, tags it, and publishes. The `CHANGELOG.md` is the single source
of truth for what the next version is. CI never tags by hand.

## Release Process

### 1. Prerequisite

Ensure all feature PRs to be included in the release are merged into `main`, and
that their changes are recorded under the `## Unreleased` heading in
`CHANGELOG.md` (this happens automatically when you land work via `/preflight`).

### 2. Prepare the release (`/release`)

On a short-lived branch, run `/release` and pick the version bump (patch / minor
/ major). It will:

- Promote `## Unreleased` in `CHANGELOG.md` to `## vX.Y.Z - YYYY-MM-DD`, leaving
  an empty `## Unreleased` block above it.
- Bump the version in **`pyproject.toml`** and **`src/holocron/config.py`**
  (`__version__`) to `X.Y.Z` so all three sources agree.
- Commit as `[docs] Update CHANGELOG for vX.Y.Z` and open a PR into `main`.

> The `Release` workflow refuses to run if `pyproject.toml` or
> `src/holocron/config.py` disagree with the CHANGELOG version, so keep all
> three in sync during prep.

### 3. Verify and merge

CI runs on the PR (tests on Python 3.14, plus PyPI and Docker smoke tests).
Ensure all checks pass, then **merge the release PR into `main`**.

### 4. Publish (`Release` workflow)

Once the release commit is on `main`:

1. Go to the **Actions** tab -> **Release** workflow -> **Run workflow**
   (`workflow_dispatch`, no inputs).

**What this does:**

- Reads the top `## vX.Y.Z` version from `CHANGELOG.md` (fails loudly if nothing
  was prepared, or if the tag already exists).
- Validates `pyproject.toml` and `src/holocron/config.py` match that version.
- Creates and pushes the git tag `vX.Y.Z`.
- Creates a **GitHub Release**, with the notes taken directly from that version's
  `CHANGELOG.md` section body.
- Publishes:
  - **PyPI**: builds and publishes `holocron-sync` (trusted publishing / OIDC).
  - **Docker**: pushes the multi-arch image to GHCR
    (`ghcr.io/someniak/holocron:X.Y.Z` and, for final releases, `latest`).

### Pre-releases

Prepare a version containing letters (e.g. `1.2.0rc1`). The `Release` workflow
detects it as a pre-release automatically:

- The GitHub Release is marked **Pre-release**.
- The Docker image is pushed **without** the `latest` tag.

To promote to final, run `/release` again with the final version (e.g. `1.2.0`),
merge, and dispatch `Release` once more.

## Notes

- Tagging and publishing are owned entirely by the `Release` workflow. **Do not
  create tags manually** - a hand-created tag will collide with the workflow.
- There is no separate "prepare release" workflow anymore; version prep is done
  locally via `/release` and lands through a normal PR.
- Release notes come from `CHANGELOG.md`, so keep the entries user-facing and
  grouped by section (Features, Bug Fixes, Infrastructure, etc.).
