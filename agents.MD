# AGENTS.md

## 📌 Purpose

This document defines contribution standards for both humans and AI agents working in this repo.
It enforces **consistency, security, and maintainability** across the codebase.
Think of this as our **style guide + quality contract**.

---

## 🔐 Security

* Treat all inputs as untrusted — always validate and sanitize.
* Avoid unsafe patterns:

  * Shell exec, `eval`, unverified deserialization, raw SQL.
* Enforce explicit schemas (Pydantic, Django models, Marshmallow).
* Never expose secrets, API keys, or tokens (zero-trust assumption).
* Run linters, type checks, and tests before merging.

---

## 🧱 Code Design Principles

* **Clarity > Cleverness**: prioritize readability over conciseness.
* **Small functions**: ≤ 20 lines (target 10–15).
* **Single responsibility**: each function/class does one thing well.
* **Explicit over implicit**:

  * Avoid dense one-liners, hidden side effects, and tricky language features.
  * Prefer explicit `if` over nested ternaries.
  * Limit call chaining to max 2 steps.
* **DRY but not obsessed**: reuse logic, but don’t abstract prematurely.
* **Consistent naming**: descriptive, not abbreviated.
* **Remove dead code**: no commented-out blocks or unused imports.
* **Documentation at point of use**: comments explain *why*, not *what*.

**Django specifics**:

* Use `bulk_create`/`bulk_update` for efficiency.
* Always define `UPDATE_FIELDS` constants for updates.
* Wrap multi-step writes in transactions.
* Cache repeated upserts in memory during a run.

---

## 🧰 Helpers & Utilities

* Keep helpers **local** if only used once; mark them private (`_helper`).
* Extract to `api/utils/<purpose>.py` if reused.
* Avoid generic “misc” utils files.
* Public helpers: type hints + docstrings; side-effect free.
* Private helpers: `_parse_*`, `_build_*`, `_apply_*`.
* Migration rule: start local, extract only when reused or file > 300 lines.

---

## 🧩 Models

* **One file per model**: strict rule.

  * Example:

    * `api/models/ticker.py` → `Ticker`
    * `api/models/exchange.py` → `Exchange`
    * `api/models/job.py` → `Job`
* **Re-export** in `api/models/__init__.py`:

  ```python
  from .ticker import Ticker
  from .exchange import Exchange
  from .job import Job

  __all__ = ["Ticker", "Exchange", "Job"]
  ```
* No side effects in model files (signals, listeners go elsewhere).
* Admin config belongs in `admin.py`.
* Tests mirror model file structure.

---

## ✅ Testing

* Every new feature requires tests. **No exceptions.**
* Cover: CRUD, edge cases, abuse/misuse, success paths.
* Tests must be deterministic and fast (<1s per unit test).
* Use integration tests only when needed.
* Test naming: `test_<functionality>_<expected_behavior>`.
* Test location: `/tests/` mirroring code layout.

---

## 🧩 Extensibility

* No hardcoded credentials/config.
* Use base classes/interfaces for provider-specific logic.
* Prefer loose coupling; modules should be swappable.

---

## 🗂️ File & Folder Conventions

* Each model = its own file under `src/recipes/models/`.
* No placeholder/empty boilerplate files.
* Organize by domain, not by layer (avoid `misc.py`, `common.py`).

---

## 🧾 Documentation

* All public classes and functions need docstrings.
* Comments should clarify *why* decisions were made.
* Update Markdown/docs when behavior changes.

---

## 💬 Commits & PRs

* **Commit title format**: `[scope] Short summary`

  * Example: `[auth] Add password reset expiry`
* Messages must explain:

  * **What** changed
  * **Why** it’s needed
  * **How** to test it
* Keep commits small and focused (atomic).
* No auto-generated files in commits.

---

## 🤖 Agents (AI Contribution Rules)

* Respect this document before generating code.
* Reuse existing patterns and naming.
* Never assume frameworks/libraries not already in the repo.
* Ensure functions are short, explicit, and modular.
* Include docstrings and type hints.
* Run security and readability checks before suggesting changes.

---

## 🔍 Code Review Checklist

* [ ] Functions ≤ 20 lines; single responsibility
* [ ] No dense inline logic, no multi-statement lines
* [ ] Call chaining ≤ 2 steps
* [ ] DB ops isolated; transactions around multi-step writes
* [ ] Bulk ops with `UPDATE_FIELDS`
* [ ] No dead code; imports trimmed
* [ ] Comments explain *why*
* [ ] Tests added/updated (CRUD, edge, abuse, success paths)
* [ ] No secrets/unsafe code
* [ ] Docs updated

---

## 🛠️ PR Template

`.github/pull_request_template.md`:

```markdown
## Summary
- What does this change do?

## Checklist
- [ ] Functions ≤ 20 lines
- [ ] Single responsibility per function/class
- [ ] No chained one-liners or dense inline ternaries
- [ ] Intermediate variables for clarity
- [ ] DB I/O isolated; transactions for multi-step writes
- [ ] Narrow exceptions (no bare `except`)
- [ ] Docstrings + type hints present
- [ ] `UPDATE_FIELDS` constant used for bulk updates
- [ ] Tests mirror module layout and cover edge cases

## 🧪 Test & Quality Tools (Backend)
All backend test and quality tooling is executed from the project root and targets the backend codebase in `src`. Configuration files for each tool live in the backend code root (`src`).