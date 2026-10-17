# Build log

One entry per work package: what was built, decisions and why, numbers measured.

## P0.1 Repository and tooling

**Built.** uv project (`uv.lock` committed), `src/issuescout/` with the nine packages from SRS Appendix C plus `cli.py` and `settings.py` stubs, `tests/{unit,integration,e2e,security,fixtures}`, `eval/`, `deploy/`, `.github/workflows/`. Ruff (lint + format), mypy strict, pytest, and pre-commit hooks.

**Done-when check.** `uv run pytest` passes the placeholder test, and `scripts/check_precommit_rejects.py` writes a badly formatted file, runs the hooks on it, and requires a non-zero exit (all three hooks fail on it).

**Decisions.**
- Python is pinned to `>=3.12,<3.13` so the lock file and benchmark numbers cannot drift across interpreter minors (phase plan: "pin every dependency version now").
- Pre-commit hooks are `language: system` and call `uv run`, so the hooks use exactly the locked tool versions instead of a second copy pinned in `.pre-commit-config.yaml`. Trade-off: contributors need uv on PATH.
- `pre-commit` is not in the CLAUDE.md stack list, but P0.1 names it explicitly, so it is added as a dev dependency only.
- mypy runs strict on `src` and `tests` from day one; relaxing later is easier than tightening.
- `.gitignore` already excludes `.env*`, `*.pem`, and local mirrors/caches, ahead of P0.6.

**Environment notes (Windows).** uv's managed-Python install left a dangling minor-version junction; recreated it with `mklink /J`. Windows PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM that breaks TOML parsing; files are written without a BOM.
## P0.2 Settings and logging

**Built.** `settings.py`: one frozen `pydantic-settings` class holding every Appendix B value plus service endpoints and log level. `logging.py`: structlog JSON to stdout with ISO UTC timestamp, level, and a `correlation_id` field taken from a context variable.

**Done-when check.** `tests/unit/test_settings_logging.py` (9 tests): defaults equal Appendix B; values load from environment variables; invalid values (`DENSE_TOP_K=0`) are rejected; settings are immutable; a log line parses with `json.loads` and carries the bound correlation ID (null outside a context).

**Decisions.**
- No env prefix, so the variable names are the Appendix B names. Cost: a generic name like `DRY_RUN` could collide with another tool; acceptable for a service that owns its container.
- Settings are frozen and read through a cached `get_settings()` rather than a module-level instance, so tests control the environment and nothing is read at import time.
- `conf_high` / `conf_medium` default to `None`: SRS Appendix B says they are calibrated in P2.5, so a made-up default would be a hidden tuning decision.
- Correlation ID uses `contextvars`, so it follows the call stack (and asyncio tasks) without being passed as a parameter. Celery will set it from the job envelope in P3.4.
- **Spec vs. CLAUDE.md:** SRS Appendix B has `DRY_RUN=false`; CLAUDE.md rule 7 says dry-run stays on until P3.11. The code keeps the SRS default and `.env.example` sets `DRY_RUN=true`, so development runs are dry without changing the spec.
- mypy uses the `pydantic.mypy` plugin (ships with pydantic, no new dependency).

**Dependencies added.** `pydantic-settings`, `structlog` (both in the fixed stack).