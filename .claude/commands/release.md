Prepare a release for holocron: promote the CHANGELOG, bump every version file,
and open a PR. This is the project-local override of `/release` — it adds the
version-file bumps that holocron's `Release` workflow validates.

Holocron keeps the version in THREE places that must always agree:

| File | Field |
|---|---|
| `pyproject.toml` | `version = "X.Y.Z"` |
| `src/holocron/config.py` | `__version__ = "X.Y.Z"` |
| `CHANGELOG.md` | top `## vX.Y.Z - YYYY-MM-DD` heading |

The `Release` workflow (`.github/workflows/release.yml`) reads the version from
`CHANGELOG.md` and **refuses to run** if the two version files disagree with it,
so this command bumps all three together.

There is also a fourth, derived source: `uv.lock` records the project's own
version. Bumping `pyproject.toml` makes the lock stale, and the Dockerfile builds
with `uv sync --frozen`, which **fails on a stale lock** — so after the version
bump this command regenerates `uv.lock` and commits it alongside the other three.

## Steps

1. **Branch guard.** If on `main`, create a branch `chore/release-vX.Y.Z` (use
   the target version once known) and switch to it. Never prepare a release on
   `main` directly.

2. **Determine the version bump.** Ask the user for patch / minor / major if not
   specified. Read the current version from `pyproject.toml` and compute the new
   `X.Y.Z`. For a release candidate, use a suffix like `X.Y.Zrc1`.

3. **Collect changes** since the last release:
   ```
   git log <last-tag>..HEAD --pretty=format:"%s"   (if a tag exists)
   git log --oneline                                (if no tags yet)
   ```

4. **Generate the CHANGELOG entry**, grouped by commit scope prefix:

   | Prefix | Section |
   |---|---|
   | `[feature]` | Features |
   | `[fix]` | Bug Fixes |
   | `[refactor]` | Refactoring |
   | `[ci]` / `[deploy]` | Infrastructure |
   | `[docs]` | Documentation |
   | `[test]` | Tests |
   | `[chore]` | Chores |

   - Strip the `[scope]` prefix; write concise, user-facing descriptions.
   - Omit merge commits, version bumps, and trivial changes.
   - Commits without a scope go under **Other**.
   - Much of `## Unreleased` is already populated by `/preflight`; fold the new
     commits in and tidy, don't duplicate.

5. **Promote the CHANGELOG.** Keep the `## Unreleased` heading in place (now
   empty) and insert the new version section directly below it:
   ```markdown
   ## Unreleased

   ## vX.Y.Z - YYYY-MM-DD

   ### Features
   - ...

   ### Bug Fixes
   - ...
   ```
   Do NOT remove the `## Unreleased` heading — the `Release` workflow finds the
   next version by reading the first `## vX.Y.Z` heading below it. Use today's
   date in ISO format (YYYY-MM-DD).

6. **Bump the version files to match** (this is the holocron-specific step):
   - `pyproject.toml`: set `version = "X.Y.Z"`.
   - `src/holocron/config.py`: set `__version__ = "X.Y.Z"`.

   Edit these directly; do not use `cd`. After editing, verify all three sources
   agree, e.g.:
   ```
   grep -m1 '^version = ' pyproject.toml
   grep -m1 '^__version__ = ' src/holocron/config.py
   grep -m1 '^## v' CHANGELOG.md
   ```
   All three must show `X.Y.Z`.

7. **Regenerate the lockfile** so `uv.lock`'s project version matches the bump
   (the Dockerfile's `uv sync --frozen` fails on a stale lock):
   ```
   uv lock
   ```
   Then verify the lock is consistent and shows the new version:
   ```
   uv sync --frozen --no-dev
   grep -A1 'name = "holocron-sync"' uv.lock | grep version
   ```
   `uv sync --frozen` must exit 0 and the grep must show `X.Y.Z`. If `uv sync`
   later dropped dev tools (via `--no-dev`), run `uv sync` afterward to restore
   them.

8. **Sanity-check** that the package still builds and imports with the new
   version:
   ```
   uv run holocron --version
   ```
   It must print `X.Y.Z`. Fix any mismatch before continuing.

9. **Commit** all four files together (the three version sources plus the lock):
   ```
   git add pyproject.toml src/holocron/config.py CHANGELOG.md uv.lock
   git commit -m "[docs] Update CHANGELOG for vX.Y.Z"
   ```

10. **Push and open a PR** into `main`:
   ```
   git push -u origin chore/release-vX.Y.Z
   gh pr create --title "Release vX.Y.Z" --base main --head chore/release-vX.Y.Z --body "..."
   ```
   Report the PR URL.

11. **Do NOT tag or publish here.** Tagging and publishing are owned by the
    `Release` workflow. After the PR merges to `main`, trigger it from
    Actions -> Release -> Run workflow (no inputs). See `RELEASE.md`.

12. **Return to main** so the next session starts clean:
    - `git switch main`
    - `git pull --ff-only`
    - Report that you've switched back.

    If the switch fails (uncommitted changes appeared), report it but do not
    stash or discard work — leave the branch as is and tell the user.

## Rules

- NEVER create git tags directly — the `Release` workflow owns tagging.
- NEVER push to `main` directly; releases go through a PR.
- All THREE version sources must match before committing; a mismatch makes the
  `Release` workflow fail.
- Regenerate `uv.lock` after the bump and commit it; a stale lock breaks the
  Dockerfile's `uv sync --frozen` build in the `Release` workflow.
- Use present tense in the CHANGELOG ("Add", "Fix", "Remove").
- Keep descriptions concise and user-facing — one line per change.
- Do NOT use compound `cd ... &&` commands or `$()` substitution in Bash calls.
