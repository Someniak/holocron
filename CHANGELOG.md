# CHANGELOG


## Unreleased

### Infrastructure

- Publish the internal container image to Artifactory automatically from GitLab
  CI. The `docker-publish` job builds and pushes on default-branch commits
  (`:latest` + `:<short-sha>`) and on mirrored release tags (`:<tag>` +
  `:latest`), records each publish under a GitLab `artifactory` environment, and
  is guarded so it is skipped where Artifactory is unconfigured. The `--no-push`
  Dockerfile smoke test (`docker-build`) no longer requires registry
  credentials, so the Artifactory push token can be a protected variable without
  breaking merge-request validation.

## v1.6.0 - 2026-07-16

### Features

- Report GitLab CI results back to GitHub with the opt-in `--github-status` flag
  (`HOLOCRON_GITHUB_STATUS`). When mirroring GitHub -> GitLab, Holocron
  provisions a per-project `GITHUB_REPO` CI/CD variable so GitLab runners can
  post each job's result to the matching GitHub commit SHA (preserved by
  `git push --mirror`), surfacing the checks on the GitHub PR with no PR-number
  lookup. Ships a documented `.github-check` job template; fork PRs are out of
  scope.

### Infrastructure

- Clean up `ruff` lint errors across the source and tests (unused imports, a
  placeholder-less f-string, an unused mock). No behavior change.

## v1.5.0 - 2026-07-16

### Features

- Trigger a sync from GitHub `pull_request` webhook events (the `opened`,
  `synchronize`, and `reopened` actions), not just `push`. A PR opened or
  updated on GitHub now refreshes the mirror immediately -- handy for running CI
  on the mirrored side, including fork PRs whose head arrives as `refs/pull/*`.
  No merge request or branch is created on the destination; only the mirror is
  refreshed. Other `pull_request` actions are acknowledged without work.

### Infrastructure

- Pull the Kaniko builder image in `.gitlab-ci.yml` through the ProGet mirror
  (`artifactory.aparty.blue-yard.be`) instead of `gcr.io` directly; ProGet
  proxies and caches it on first request. Only affects where the builder image
  is pulled from, not the push destination.

## v1.4.1 - 2026-07-15

### Security

- Validate untrusted repository fields before they reach `git` or the
  filesystem. A repo name is now confined to a safe slug (no path separators or
  `..` traversal) and a clone URL must be `http(s)` — rejecting git's `ext::`
  transport (arbitrary command execution), `file://`/`ssh://`, and option-like
  values. Enforced at the single sync chokepoint, covering both the poll and
  webhook paths.
- Pin the GitHub token to the configured GitHub host. A forged clone URL
  pointing at another host (e.g. `https://attacker.tld/...`) is now refused
  instead of having the OAuth token attached and sent there, closing a
  credential-exfiltration vector.
- Stop the webhook listener from fingerprinting itself to scanners. Any request
  that is not a validly-signed delivery now gets a uniform, unbranded `404`
  (previously a `401`/health page that advertised the endpoint), the `Server:`
  banner naming Python/BaseHTTP is suppressed, and unsupported methods return
  `404` instead of the stdlib's `501`.
- Harden the Docker image: it now runs as an unprivileged `holocron` user
  instead of root, and the webhook TLS key path is set at runtime by the
  entrypoint rather than baked into image metadata via `ENV`.

## v1.4.0 - 2026-07-15

### Features

- Serve the webhook listener over HTTPS when a TLS certificate and key are
  provided (`--webhook-cert` / `--webhook-key`, or the matching env vars). In
  Docker the certificate is auto-generated as a self-signed cert on first start
  (openssl is bundled) and can be replaced by mounting your own into `/certs`;
  plain `pip` installs stay HTTP-by-default and never auto-generate a cert.

## v1.3.0 - 2026-07-15

### Features

- Add an optional webhook listener (`--webhook`) that syncs a single repository
  on demand when GitHub POSTs a `push` event, running alongside `--watch`
  polling as a safety net. Deliveries are authenticated with an HMAC-SHA256
  signature (`HOLOCRON_WEBHOOK_SECRET`), acknowledged instantly, and synced on a
  background thread; concurrent poll- and webhook-triggered syncs of the same
  repo are serialized so they cannot corrupt the mirror. Configurable via
  `--webhook-port` / `--webhook-path`.

### Infrastructure

- Rebuild the Docker image from source with a multi-stage `uv` build that
  installs dependencies from `uv.lock`, so the image ships the exact pinned
  (CVE-patched) versions instead of resolving them fresh at build time. Remove
  the unpinned `requirements.txt`.
- Add a GitLab CI pipeline (`.gitlab-ci.yml`) that builds the container image
  with Kaniko on GitLab runners and pushes it to Artifactory (default branch
  and tags), validating the build on merge requests. Complements the GHCR
  publish on GitHub for the mirrored GitLab copy.

## v1.2.0 - 2026-07-14

### Bug Fixes

- Raise on incomplete GitHub/GitLab repository pagination so a partial repo
  list can no longer be mirrored as if it were complete; the sync cycle logs
  the failure and retries next interval instead of silently skipping repos.
- Surface the HTTP status and a token-scope hint when relaxing branch
  protection fails, instead of collapsing every error into a generic warning.

### Security

- Redact embedded credentials (tokens/passwords) from git output before it is
  written to the logs.
- Upgrade vulnerable dependencies to resolve 8 Dependabot alerts: urllib3
  2.7.0 (decompression-bomb and cross-origin header fixes), idna 3.18,
  requests 2.34.2, python-dotenv 1.2.2, pygments 2.20.0, and pytest 9.1.1.

### Documentation

- Replace stream-of-consciousness comments in the GitHub provider's branch
  protection logic and fix the stale `src/holocron.py` run command in the
  README (now `uv run holocron`).

### Infrastructure

- Adopt the CHANGELOG-driven release model: replace the input-driven
  Prepare/Publish Release workflows and release-drafter with a single
  `Release` workflow that reads the version from `CHANGELOG.md`, tags it,
  builds the GitHub Release notes from the version's section body, and
  publishes to PyPI and GHCR. Update `RELEASE.md` to match, and add a
  project-local `/release` command that bumps `pyproject.toml` and
  `src/holocron/config.py` alongside the CHANGELOG so all version sources
  stay in sync.

### Chores

- Stop tracking the committed `.coverage` file.
- Raise the minimum supported Python to 3.14 across `pyproject.toml`, the
  Dockerfile, and the CI/nightly workflow matrices; the patched dependency
  releases no longer support older interpreters.


## v1.1.0 (2025-12-18)

### Features

- Add multi-platform build support and caching to Docker publish workflow
  ([`0151054`](https://github.com/Someniak/holocron/commit/0151054f3b123d8b008e5ef76a6201e7defc3471))


## v1.0.0 (2025-12-17)

### Chores

- **release**: 1.0.0 [skip ci]
  ([`e9d4de6`](https://github.com/Someniak/holocron/commit/e9d4de66b12521511ac963cafa5f91a4da307968))

### Documentation

- Improve README with clearer Docker run instructions and minor text corrections.
  ([`38fc23a`](https://github.com/Someniak/holocron/commit/38fc23a86f91f818fb262d6f9b88f1f58792cbd4))

### Features

- Final cleanups for 1.0.0 release
  ([`40eee68`](https://github.com/Someniak/holocron/commit/40eee6861b7f3d9fddbc96a899763e7b80cb0022))

BREAKING CHANGE: Transitioning to 1.0.0 stable release.

### BREAKING CHANGES

- Transitioning to 1.0.0 stable release.


## v0.2.0 (2025-12-17)

### Chores

- **release**: 0.2.0 [skip ci]
  ([`d472752`](https://github.com/Someniak/holocron/commit/d47275235a591be80334b172b4a9d596203f8eac))

### Features

- Add CLI options for version and credits display, and delete docker-compose.yml.
  ([`fe3bfe1`](https://github.com/Someniak/holocron/commit/fe3bfe1c5b3d2ad26f39c9d71a9c1195f4872b5b))

- Add comprehensive Docker usage guide and enhance environment variable parsing for configuration.
  ([`9f8ccda`](https://github.com/Someniak/holocron/commit/9f8ccda91acb569df066922c163677a5e5a861e4))

- Add GitLab namespace support, configurable GitHub API URL, and improve GitLab clone URL
  generation.
  ([`180cc44`](https://github.com/Someniak/holocron/commit/180cc44998f48e6f34d6fedf1b3ac7ae9643dfdb))

- Add utility functions for credits and storage estimation, and remove contribution guidelines.
  ([`1ce23d1`](https://github.com/Someniak/holocron/commit/1ce23d185e9458be01cbd4c895983201e1ef26eb))

- Implement dynamic source and destination provider selection with new CLI arguments and updated
  provider initialization.
  ([`cd79758`](https://github.com/Someniak/holocron/commit/cd79758f32521f0abebf15f61ba50078f2e38d93))

- Introduce a pluggable provider architecture, migrating GitHub logic and adding GitLab support.
  ([`870e964`](https://github.com/Someniak/holocron/commit/870e9640d5de028a36be4a2e41f2dd0740f4fd01))

### Refactoring

- Encapsulate GitHub provider logic into a class, update `Repository` type hint, and enhance test
  instructions in README.
  ([`7c56a05`](https://github.com/Someniak/holocron/commit/7c56a05771f42db1bd9127c5d7ee0c7a4f00cb74))

- Extract git operations into private helper functions `_ensure_local_mirror` and
  `_push_to_destination` within `sync_one_repo`.
  ([`aa0ea5c`](https://github.com/Someniak/holocron/commit/aa0ea5ce042e1bbdcc28c18a40feb41250819510))

- Replace verbose parameter with structured logger and execution decorator in provider methods.
  ([`da75922`](https://github.com/Someniak/holocron/commit/da7592281fd1cc3405af3392074bb6c5bbb78d59))

- Standardize repository data handling with a new Repository dataclass and update API token
  permission documentation.
  ([`406ba0f`](https://github.com/Someniak/holocron/commit/406ba0f8e5f75cf61991950870bb996f13481c85))

- Update function signatures to accept explicit parameters instead of the full `args` object.
  ([`58a8829`](https://github.com/Someniak/holocron/commit/58a8829de9f194e590b46602f94081c49968ec67))


## v0.1.0 (2025-12-16)

### Bug Fixes

- Rename workflow to match PyPI config
  ([`528f02b`](https://github.com/Someniak/holocron/commit/528f02b93008946631c0f53f7e9d0b7eb9a5c4e5))

- Update CI release
  ([`7718fa3`](https://github.com/Someniak/holocron/commit/7718fa3371b00ea44bdfd25f3ad0822aadb3b58b))

### Build System

- Update project dependencies
  ([`17b3ded`](https://github.com/Someniak/holocron/commit/17b3ded4524fe80a6cf433384107c03ff7254fbd))

### Chores

- Add MIT License file.
  ([`beffac4`](https://github.com/Someniak/holocron/commit/beffac4315d2404ffa32f6027aa610fb5e2efcb5))

- Add pull request trigger for pre-release branch and correct semantic release condition branch
  name.
  ([`854e11e`](https://github.com/Someniak/holocron/commit/854e11e00d69e3ee2ecdd4692a90c172efe2b54d))

- Remove .DS_Store
  ([`4d5a721`](https://github.com/Someniak/holocron/commit/4d5a72166547cd5bebfc46f49caa04baa2862616))

- Update project dependencies and metadata.
  ([`818c3d2`](https://github.com/Someniak/holocron/commit/818c3d285b205294eb5df34bb7c1f26a6c0a5d27))

- **release**: 0.1.0 [skip ci]
  ([`6dac879`](https://github.com/Someniak/holocron/commit/6dac879061b437c88cd8bbd3a03e6fe5d2ba5bd5))

### Documentation

- Adjust README description.
  ([`848f19f`](https://github.com/Someniak/holocron/commit/848f19f5789edf1b2e88d8dbb6d0a4bae352151b))

- Append 'TEST' to README description.
  ([`3dd0f7e`](https://github.com/Someniak/holocron/commit/3dd0f7e6e9bd253ab76c12b25491c3574e047d93))

- Clarify ideal use case for local backups in README
  ([`0a72588`](https://github.com/Someniak/holocron/commit/0a725882a24e060b465a1d0aaf03ffc65a0f8d2f))

- Remove "TEST" from README description
  ([`49af615`](https://github.com/Someniak/holocron/commit/49af6153b493389db29a99f1cbb5e0ba9063be98))

### Features

- Add `--backup-only` mode to prevent GitLab pushes and optimize watch mode by tracking `pushed_at`
  timestamps.
  ([`732ff7d`](https://github.com/Someniak/holocron/commit/732ff7dd40b9810c30650f1499232d12e1857a9a))

- Add comprehensive test suite for core modules and implement CI workflow.
  ([`209e5e5`](https://github.com/Someniak/holocron/commit/209e5e59dbbee634430e940c409d0bcee7f1eb88))

- Add concurrency for repository synchronization using a thread pool.
  ([`f4b869b`](https://github.com/Someniak/holocron/commit/f4b869bada45d80380610ad71a5fc02b70ac08c1))

- Add Docker publishing workflow and separate PyPI publishing into its own workflow file.
  ([`0c5fd5b`](https://github.com/Someniak/holocron/commit/0c5fd5bd310c2f77d7ae72790e3840a86fb7a50d))

- Fetch all user and organization GitHub repositories with pagination and add uv for dependency
  management.
  ([`ebdb616`](https://github.com/Someniak/holocron/commit/ebdb616caa8ab43c38ef006797c60b269ffff4b4))

- Implement initial G2G-Sync for GitHub to GitLab mirroring, including core logic, API providers,
  configuration, logging, and documentation, and update .gitignore.
  ([`9d6929c`](https://github.com/Someniak/holocron/commit/9d6929c84792c49e2603f4842b9ed4ba007956bf))

- Introduce `--checkout` option for visible working trees and enhance documentation with new
  features and usage examples.
  ([`60a442b`](https://github.com/Someniak/holocron/commit/60a442b9ea66b2813b465a0c6fee625d0058e7a3))

- Replace generic CI workflow with separate production and prerelease CI/CD pipelines including
  semantic release, and update `pyproject.toml`.
  ([`9d97f9b`](https://github.com/Someniak/holocron/commit/9d97f9ba7741926b18ce37e4957871c9ce2a497e))

### Refactoring

- Consolidate modules into `holocron` package and update related configurations and tests.
  ([`3cfb703`](https://github.com/Someniak/holocron/commit/3cfb703d7239dee684ed7bdcbf150cb426375fe3))

- Rename project from G2G-Sync to Holocron, updating module names, documentation, and configuration.
  ([`7dc18b5`](https://github.com/Someniak/holocron/commit/7dc18b5498c0847f2b6f8450369507e60c55ce82))
