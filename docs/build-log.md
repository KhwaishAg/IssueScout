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
## P0.3 Docker Compose

**Built.** `deploy/compose.yaml` with Qdrant v1.15.5, MongoDB 8.0, RabbitMQ 4.1 (management), MLflow v3.4.0; one named volume each; a health check on each; `restart: unless-stopped` (NFR-AVL-02).

**Done-when check.** `docker compose -f deploy/compose.yaml up -d --wait` exited 0 with all four containers `healthy` (about 11 s from a warm image cache).

**Decisions.**
- Ports bind to `127.0.0.1` only, so nothing is exposed to the network while the databases have no authentication.
- **Authentication is deferred to P4.1.** NFR-SEC-05 requires auth on the broker and databases in production; the dev stack runs with defaults (RabbitMQ guest on localhost only, Qdrant and Mongo open). Adding it now would mean API-key/credential settings in every client before any client exists. Flagged for the Phase 4 deploy package.
- Image tags are pinned to specific versions, not `latest`, so benchmark runs are not affected by a silent service upgrade.
- Qdrant's image has no curl/wget, so its health check opens the port with bash `/dev/tcp`; MLflow's uses Python's urllib against `/health`.
- MLflow uses SQLite plus artifacts on its volume: enough for a single-developer experiment tracker, and one fewer service than a Postgres backend (Postgres is also on the banned list).
- Compose file is `deploy/compose.yaml`, per Appendix C (`deploy/`), run with `-f`.
## P0.4 CI workflow

**Built.** `.github/workflows/ci.yml`: on every push and pull request, `uv sync --locked`, `ruff format --check`, `ruff check`, `mypy`, `pytest`. Read-only token permissions, concurrency group cancels superseded runs, 15-minute timeout, uv pinned to 0.13.1.

**Verified locally.** Each CI step passes on the current tree; adding a failing test makes pytest exit 1 (the red case), then removed.

**GitHub half of "Done when" verified (2026-10-17).** Green on main: runs 39024318436 and 39024641638 succeeded. Red on a branch: throwaway branch `ci-red-check` with a failing test, run 39024672140 concluded `failure`; branch deleted. (Originally pending; text below is the earlier note.) **Earlier note:** "Green on main, red on a branch with a failing test" needs a GitHub remote. This folder had no remote, so the workflow has not run on GitHub. Outstanding until the repository is pushed (see Gate 0).

**Decisions.**
- `uv sync --locked` fails CI if `uv.lock` is out of date, so lock drift cannot reach main.
- Same commands as the pre-commit hooks, so local and CI results agree.
- Actions are pinned to major tags (`checkout@v4`, `setup-uv@v6`); SHA pinning is a P4.3 supply-chain item.
## P0.5 Doctor command

**Built.** Typer CLI (`issuescout` entry point) with `issuescout doctor [--json]`. `doctor.py` checks Qdrant (`/readyz`), MLflow (`/health`), MongoDB and RabbitMQ (TCP connect), and the Hugging Face model cache. Exit 0 if nothing failed, 1 otherwise; the failing service is named on its own line.

**Done-when evidence (real stack).** All up: four `[OK]` lines, exit 0. After `docker stop issuescout-qdrant-1`: `[FAIL] qdrant ... unreachable: ConnectError`, others OK, exit 1. Unit tests (7) cover both outcomes without a live stack.

**Decisions.**
- Protocol-level probes (HTTP, TCP) instead of `pymongo`/`pika`: no driver dependency before a package uses one. Trade-off: a TCP connect proves the port is open, not that auth or the protocol works. When P1.3 and P3.1 add real clients, doctor should switch to client pings.
- Empty model cache is a **warning**, not a failure, because models are chosen in P2.2 (SRS 5.3); it becomes a failure then.
- Checks never raise and all run every time, so one dead service cannot hide another.
- Tests stub the network probes; only the manual run above touched real services.

**Dependencies added.** `typer`, `httpx` (both in the fixed stack).
## P0.6 GitHub App setup (done)

**Built.** `github_app_id`, `github_app_private_key`, `github_webhook_secret` in settings (the latter two `SecretStr`, no defaults, masked in repr/dump; 2 tests). `docs/github-app-setup.md`: exact registration steps, the four FR-APP-01 permissions, subscribed events, where secrets live, rotation. `scripts/check_no_secrets.py`: scans every blob in all refs for key blocks, GitHub token formats, webhook-secret assignments, and secret-like file names; prints rule and path, never the match.

**Evidence so far.** Scanner exits 0 on the real history (74 objects) and exits 1 on a throwaway repo with a planted fake key block, so it can fail.

**Closed by the owner (2026-10-17).** App registered and installed on the sandbox repository by Khwaish; `.env` is untracked and ignored; scanner reports 0 problems on 100 objects. I did not touch credentials.

**Decisions.**
- Secrets arrive only via environment/`.env`; `.gitignore` also blocks `.env*` (except `.env.example`) and `*.pem` as a backstop.
- The scanner covers history, not just the working tree, because a secret deleted in a later commit is still leaked.
- Setup guide examples avoid a literal key header so the scanner does not flag the docs.
## P0.7 Decisions

**Built.** `docs/decisions/0001-stack.md`, `0002-chunking.md`, `0003-vector-store.md` (context, decision, why, alternatives rejected, consequences) and `0004-open-questions.md` answering every SRS Appendix D question. Appendix D boxes ticked in the SRS with a pointer to ADR 0004; CLAUDE.md summary updated.

**Answers.** Public repos only; no Langfuse; free hosting only; first installs on own repos; package name free on PyPI (GitHub App name confirmed at registration).

**Flag for later.** "Free only" conflicts with the SRS's one 2 vCPU / 8 GB EC2 instance (NFR-CST-01). ADR 0004 lists the options; decide at the Phase 3 gate. Phases 0 to 3 are unaffected.

**Interview notes.** Be ready to defend: why no LangChain (opaque stages vs the ablation), why a function-level chunk (human-navigable unit, fixes are made at function level), why one filtered Qdrant collection (cheap isolation, one audited search function).