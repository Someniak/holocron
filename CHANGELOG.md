# CHANGELOG


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
