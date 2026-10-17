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
## P1.1 Python chunker

**Built.** `indexing/models.py` (frozen `Chunk` plus a whitespace-normalised SHA-256 `content_hash`), `indexing/tokens.py` (token counter), `indexing/filters.py` (file selection, language and test-file detection), `indexing/chunker.py` (`chunk_file`). Python files go through tree-sitter into function, method, class-header and module chunks with qualified names, signatures, parent symbols and 1-based line ranges. Chunks over 400 tokens split into 400-token windows with 60 tokens of overlap, each carrying the signature. Everything else, and any file with a syntax error, falls back to 60-line windows with 10 lines of overlap.

**Done-when evidence.** 65 tests pass (Ruff, mypy strict clean). TC-03 on the fixture repo: exact (symbol, kind, start, end) set for nested classes, decorators, async, and module code. TC-04: a file with a syntax error yields one line-window chunk and the job continues. TC-05: a function of exactly 1,200 tokens gives 4 windows, each containing the signature, overlapping, covering lines 1 to 399. Also covered: CRLF input gives identical line numbers and hashes, invalid UTF-8 does not crash, parse-timeout fallback, every filter rule, symlinks not followed, deterministic output.

**Eye check on real code** (chunking this project's own `src/`): 53 chunks, boundaries match the function and method edges, decorators are inside their chunk, nested classes get dotted names.

**Decisions.**
- **Token counter is an approximation** (one token per word or punctuation mark) because the embedding model is not chosen until P2.2. It is injectable; P2.2 swaps in the model tokenizer. Real model counts run higher for code, so chunks will measure larger in model tokens.
- **Windows snap outward to whole lines**, so a split chunk can exceed 400 tokens by up to a partial line at each end plus the signature. Keeps line ranges exact for permalinks and the text readable. The test bounds this explicitly.
- **Fallback trigger is `root.has_error`.** tree-sitter recovers from errors, but a recovered tree can hide wrong boundaries, so any error sends the whole file to line windows (FR-IDX-10).
- **The module chunk holds only leftover top-level code** (imports, constants, `__main__` blocks), not the definitions. Its line range spans first to last leftover, and its text skips the functions in between. `_emit` takes explicit source rows so line numbers stay correct for non-contiguous chunks.
- **Class attributes are not indexed.** The SRS defines the class chunk as signature, docstring and method list, so `size = 1` in a class body is dropped. Revisit if the Phase 2 error analysis shows misses there.
- **Functions nested inside functions** stay inside their parent's chunk (no separate chunk).
- **Secret files are excluded now** (`.env*`, `*.pem`, `*.key`, `id_rsa*`), ahead of TC-20 in P4.3, since it is one table entry. `.env.example` is skipped too, which is the safe direction.
- **Symlinks are never followed** by `select_files`, so a hostile link cannot read outside the tree (NFR-SEC-11).

**Bugs found and what they cost (interview material).**
- **py-tree-sitter 0.26 on Windows:** `Point.row` and `.column` return mis-counted references. Rows above 256 come back wrong (399 instead of 398) or crash the interpreter with an access violation. Code reads points by index (`node.end_point[0]`) instead; a comment in `_end_row` explains why. Found by bisecting a crash that only appeared for functions of 300+ lines.
- **`Parser.parse(..., progress_callback=...)` also crashes** on this build, even with a trivial callback. So the 2-second parse limit (NFR-SEC-12) is checked after the parse, not enforced during it; a slow parse is discarded and the file falls back to line windows. Input size is already capped at 500 KB by the filters. A hard limit comes in P3.5 as a process-level time limit in the worker.
- Using a node after its `Tree` is garbage-collected is an access violation, so `_chunk_python` holds the tree for the whole walk.

**Dependencies added.** `tree-sitter`, `tree-sitter-python`. The grammar package was not in the CLAUDE.md list; SRS 3.7 names tree-sitter grammars as a dependency, so it was added as part of tree-sitter. Flagged here for your veto.

## P1.2 Encoders and cache

**Built.** `indexing/sparse.py`: code-aware tokenizer, hashed sparse vectors (BM25 term-frequency weights), query vectors, and `embedding_text` (path and symbol prefix). `indexing/dense.py`: `DenseEncoder` protocol, `SentenceTransformerEncoder`, SQLite-backed `EmbeddingCache`, and `embed_texts` (cache lookup, batching, dedupe, resumable). New settings for the model, revision, batch size, cache directory and BM25 constants.

**Done-when evidence.** 93 tests pass (28 new). Tokenizer cases cover camelCase, acronyms, snake_case, dotted chains, hyphens, digits. Real run of `scripts/check_embedding_cache.py` on this repo's `src/` (77 chunks, `all-MiniLM-L6-v2`, CPU): cold 4.69 s (16.4 chunks/s), warm 0.001 s, about 4,700x faster, vectors identical.

**Decisions.**
- **Identifiers keep their unsplit form and add parts.** `getUserName` gives `getusername, get, user, name`, so an issue saying "user name" and one saying `getUserName` both match (FR-IDX-12). Test `test_issue_words_match_identifier_parts` states the reason.
- **Hashing terms to CRC32 indices** instead of keeping a vocabulary. Qdrant sparse vectors need integer indices, a vocabulary would have to be stored and shared across index versions, and a hash needs none. Cost: rare collisions, handled by summing weights so indices stay unique.
- **Weights are BM25's term-frequency half only**; Qdrant's IDF modifier supplies the rest at query time. Length normalisation uses a placeholder `sparse_avgdl=120`. P1.7 measures the real mean chunk length and fixes it. Query terms weigh 1.0 and repeats are ignored.
- **Cache key = SHA-256 of (model id incl. revision, enriched text).** A new model or revision gets new keys, so vectors never mix. Because the key covers path and symbol, moving a file re-embeds it, which is correct since the input changed.
- **SQLite from the standard library** for the cache: no new dependency, one file, safe to delete. Partial progress is saved per batch, so an interrupted index resumes (groundwork for FR-IDX-15).
- **`trust_remote_code=False`.** Some embedding models, such as jina-embeddings-v2-base-code from the SRS shortlist, run third-party Python on load. The default model is Qwen3-Embedding-0.6B for that reason; P2.2 can reopen it deliberately.
- **Embedding model is provisional**, default `Qwen/Qwen3-Embedding-0.6B` (revision unpinned until P2.2 chooses). The timing check used the small MiniLM only to avoid a 1.2 GB download before it matters.

**Numbers to watch.** MiniLM manages 16 chunks/s on this CPU against NFR-PERF-06's 12 chunks/s floor. Qwen3-0.6B is roughly 25x larger, so expect it to miss that floor. P1.7 measures real indexing speed; the cache and the GPU-notebook fallback (risk R-02) are the answers.

**Dependencies added.** `sentence-transformers` (with `torch`, `transformers`, `numpy` transitively), all pinned in `uv.lock`.

## P1.3 Qdrant storage

**Built.** `storage/qdrant.py`: `ensure_collection` (named `dense` cosine and `sparse` IDF vectors, payload indexes on repo_id, installation_id, path, language, kind, is_test, index_version), deterministic `point_id`, `upsert_chunks`, the single `search_code`, `count_points`, `delete_repository`. CI now starts a Qdrant service container and waits for `/readyz`.

**Done-when evidence (real Qdrant 1.15.5).** TC-06: indexing the same chunks twice leaves the point count unchanged. TC-11: two repositories holding identical code, queried with the exact text of the other repository's chunk, return only the scoped repository's hits in dense and sparse modes; an unknown repository returns nothing; searching index version 2 never returns version 1 points. Also tested: changed chunk gets a new ID while unchanged ones keep theirs, sparse ranking puts the matching function first (IDF working), dense ranking puts the identical text first at score 1.0, `exclude_tests`, delete by repository, collection reuse and dimension-mismatch error. Full suite: 111 passed in 14 s.

**Security test.** `tests/security/test_search_isolation.py` scans `src/` and fails if any module other than `storage/qdrant.py` calls `query_points`, `search`, `scroll`, `recommend` and similar on a client, or if anything outside `storage/` imports `qdrant_client`. It also asserts `repo_id` and `index_version` are required keyword-only arguments of `search_code`. A self-test proves the scanner flags a bypass.

**Decisions.**
- **Filter by construction, not by convention.** `search_code` has no default for `repo_id` or `index_version`, both are keyword-only, and the filter is built inside. A caller cannot forget it because there is nothing to forget. The scanning test turns "never bypass it" into a CI failure.
- **Point ID = UUIDv5(repo, version, path, symbol, content hash)** per FR-IDX-13. Line numbers are deliberately not in the key, so an edit above a function changes its payload but not its ID. Byte-identical duplicate definitions of one name in one file collapse to one point.
- **`repo_id` is an integer** (SRS 6.1) with an exact-lookup index. Qdrant's tenant co-location flag exists only for keyword indexes; switching `repo_id` to a keyword is a possible optimisation if the P4.4 load test needs it.
- **Dimension check on reuse.** Changing the embedding model changes the vector size; `ensure_collection` raises instead of mixing sizes.
- **Integration tests use the Compose Qdrant locally and a service container in CI**, instead of Testcontainers: no extra dependency, and the Compose file already is the supported way to run it. Locally they skip when Qdrant is down; in CI (`CI` set) a missing Qdrant is a failure.

**Surprises worth knowing.**
- **`localhost` costs about 2 s per call on Windows** (IPv6 attempted first, then IPv4 after a timeout). Every Qdrant call took 2 s until the defaults became `127.0.0.1`, after which calls take about 10 ms. All service URLs now default to `127.0.0.1`. This also speeds up `issuescout doctor`.
- `qdrant-client` is pinned to the 1.15 line to match the 1.15.5 server; the newest client (1.19) warns about an unsupported version gap. Bump server and client together.

**Dependencies added.** `qdrant-client` (1.15.x). Not named in the CLAUDE.md list but required to use the fixed-stack Qdrant; flagged here for your veto.

## P1.4 CLI index and query

**Built.** `indexing/pipeline.py` (`index_repository`: select, chunk, embed with cache, upsert, delete stale), `indexing/source.py` (local path or shallow git clone), `retrieval/baseline.py` (`search_files`, `rank_files`), and `issuescout index` / `issuescout query` in the Typer CLI with `--json`, `--sparse-only`, `--chunking ast|lines`, `--mode sparse|dense`, `--top`. New settings: `qdrant_collection` (one collection per ablation configuration) and `state_dir`.

**Done-when evidence (real Qdrant, real model `all-MiniLM-L6-v2`).** `issuescout index src --name issuescout` indexed 15 files into 115 chunks (10 empty files skipped). `issuescout query "the embedding cache should not re-encode unchanged text" --repo issuescout` printed ranked files with symbols and line ranges. Sparse put `indexing/dense.py` first (`embed_texts`, `cache_key`); dense put `indexing/sparse.py` first and `dense.py` second. 140 tests pass in 30 s; integration tests cover index then query, re-index is a no-op, edits and deletions remove stale points, sparse-only and line-window indexes, remote clone and refresh, and the CLI through `CliRunner`.

**Decisions.**
- **Repo identity is a hash of `--name`** (56-bit, fits Qdrant's int64), so `query --repo NAME` recomputes it with no registry file and no MongoDB driver yet. Cost: two repositories given the same name share an index. Production uses GitHub's numeric repository ID (P3.5).
- **Local indexing rewrites version 1 in place** and then deletes points not in the new set, so the index equals the tree and re-runs are idempotent. The production path (P3.5) builds a new version and switches on success (FR-IDX-18); in-place is acceptable for a developer's local CLI.
- **File score = the best chunk's score**, with up to 3 distinct symbols per file (a split function's windows count once). This is the deliberate baseline; the SRS 4.5 formula and history are P2.4 so their gain can be measured.
- **Sparse-only index = no encoder loaded.** A0 and A1 never touch the 1 GB model, and a sparse-only collection has no dense vector config.
- **Remote URLs clone shallow into `.issuescout/mirrors`** with `GIT_TERMINAL_PROMPT=0` and an allow-list of protocols; nothing from the repository is executed.
- **Dense queries embed the raw text.** Some models (including the Qwen3 default) want an instruction prefix on queries; that is a P2.2 model-selection detail.
- The security scan from P1.3 caught my own new modules importing `qdrant_client`; `QdrantClient` is now re-exported from `storage/` so the rule "only storage touches Qdrant" still holds.

**Bugs and surprises worth remembering.**
- **A sparse-only index into a brand-new collection crashed** (`VectorParams(size=None)`). My first patch silently missed because Ruff had reflowed that code, and my existing sparse-only test passed only because the shared test collection already had a dense config. Found by running the real CLI; fixed, with a regression test that creates a fresh collection.
- **`localhost` again.** Indexing 15 files took 27 s because each Qdrant call waited about a second on an IPv6 attempt. The user's `.env`, copied from the earlier `.env.example`, pinned `localhost` and overrode the new default. Settings now rewrite a `localhost` host to `127.0.0.1` for service URLs (4.8 s afterwards), with a test. Lesson: a measured number (27 s for 115 chunks) was the clue; the fix was in configuration, not code.
- **`configure_logging` captured `sys.stdout` at setup**, so after the test runner closed its stream later log calls crashed. Loggers now look up stdout when created. This would also hit a service that redirects output.

**Numbers.** Dense indexing on this machine: 115 chunks in 64 s including a cold model load (1.8 chunks/s end to end here; the isolated cached-model rate from P1.2 was 16 chunks/s). Sparse-only: 115 chunks in 4.8 s, most of it one-time collection and payload-index creation.

**Dependencies added.** `gitpython` (in the fixed stack).

## P1.5 Metrics module

**Built.** `evaluation/metrics.py`: `hit_at_k`, `recall_at_k`, `reciprocal_rank`, `ndcg_at_k`, `mean`, and `evaluate` (Hit@1, Hit@5, Recall@10, MRR, nDCG@10 averaged over instances). Pure functions over lists of names, so the same code scores files and "path::symbol" functions.

**Done-when evidence (TC-21).** `tests/unit/test_metrics.py`, 14 tests. The module docstring works the example ranking [a,b,c,d,e] with gold {c,e,x} by hand: Hit@1=0, Hit@3=1, Recall@5=2/3, RR=1/3, nDCG@5 = 0.886853 / 2.130930 = 0.416181. Tests assert those literals (not recomputed from the same formula), plus perfect rankings score 1.0, no-hit scores 0, `k` cuts the list, and the ideal DCG is capped at `min(|gold|, k)`.

**Decisions.**
- **Duplicates in a ranking are removed (first occurrence kept)** before scoring. Chunk-level retrieval can list one file many times; counting each repeat would inflate recall and push true hits down. Test: `["a","a","a","c"]` still gives RR 1/2.
- **Empty gold raises** rather than scoring 0 or 1. An instance without a gold file is a loader bug (SWE-bench Lite instances all have one), and a silent score would hide it in the averages.
- **Binary relevance** for nDCG, which is what a gold file set gives. Graded core/supporting weights (FR-EVL-15) can extend this later.
- **Recall divides by the number of distinct gold items**, not by `k`, so it measures coverage of the answer rather than list length.

## P1.6 SWE-bench Lite loader

**Built.** `evaluation/swebench.py` (load both splits from Hugging Face; gold files and gold functions from the reference patch), `evaluation/gitstore.py` (bare mirrors, reading files at any commit straight from git objects), `evaluation/commit_sync.py` (`CommitIndexer`: advance one index per repository from commit to commit), `evaluation/prepare.py` (ordering, gold verification, `walk_split`), `evaluation/configs.py` (ablation presets A0 to A2, one Qdrant collection each). CLI: `issuescout dataset prepare` and `issuescout dataset index --config A0|A1|A2`. Gold labels for the dev split are committed in `eval/instances_dev.json`.

**Done-when evidence (real data, 2026-10-17).** `dataset prepare --split dev`: 23 instances over 6 repositories, **0 with a missing gold file at the base commit**. `dataset index --split dev --config A1`: all 23 indexed, **0 with a gold file absent from the index**, 6 full builds plus 17 incremental syncs, 2 min 20 s total. Same result for A0. 176 tests pass.

**Measured volume (these replace SRS 6.6 estimates; dev split, six repos).**

| Config | Chunks | Note |
| --- | --- | --- |
| A0 line windows | 15,126 | 60-line windows, 10 overlap |
| A1 function-level | 27,181 | about 1.8x A0 |

By repository for A1: pyvista 6,803, sqlfluff 6,046, pvlib 4,668, pydicom 4,720, astroid 3,661, marshmallow 1,283. The SRS assumed about 8 chunks per file; the dev repositories land at roughly 6 to 20 per source file depending on how much is data and docs. Sparse-only indexing ran at about 580 chunks/s for a full build (pyvista: 522 files, 6,803 chunks, 11.7 s). Dense indexing is the expensive part and is measured in P1.7. Disk: the six bare mirrors total 730 MB.

**Gold labels.** 21 of 23 instances have gold functions; the other two change module-level code (pydicom-1139, astroid-1268) and are scored on files only. Every instance has exactly one gold file (SWE-bench Lite's selection rule).

**Decisions.**
- **Read git objects instead of checking out.** `git ls-tree` and `git cat-file --batch` give every file at a commit without writing untrusted files to disk, and are fast. A checkout per instance would also have broken the incremental idea.
- **One living index per repository, advanced in commit order.** Instances are ordered by the base commit's committer time (not by instance ID or issue date). `git diff --name-status --no-renames` lists changes; only added and modified files are re-chunked; points of modified and deleted files are removed first. A rename is a delete plus an add (FR-SYN-04).
- **The defining test:** an index advanced through three commits equals a full index of the last commit (same paths, symbols, line ranges, point counts). A wiped collection forces a full rebuild instead of a silent partial index; the resume state file is checked against the store.
- **Changed lines, not hunk ranges, define gold functions.** SRS 10.2 says functions "that overlap the patch". A hunk's range includes three lines of context on each side, which would drag in neighbouring functions. Only removed lines count; a pure insertion is attributed to the line just before it, so an added line inside a function belongs to that function.
- **Gold function names use the chunker's own symbols** (`src/marshmallow/schema.py::src.marshmallow.schema.BaseSchema._invoke_field_validators`), so a gold name is exactly a name the retriever can return. They include the path-derived module prefix (`src.`), which differs from the Python import path; consistent everywhere, so harmless.
- **Dense indexing is not part of this package's check.** "Indexed" is proved with the sparse indexes A0 and A1; A2 needs the embedding model and runs in P1.7 through the same walk.
- **Test-split clones are not downloaded yet.** Disk is at 97% with 9.5 GB free; the test split's 12 repositories (django, sympy, matplotlib, scikit-learn and others) are several GB. `dataset prepare --split test` does it explicitly, and it is not needed until P2.6.

**Bugs found while doing it.**
- Mirror clones nested inside themselves (`.issuescout/swebench/.issuescout/swebench/...`): git ran with `cwd` set to the parent directory while the destination was a relative path. Fixed by resolving to an absolute path; found because the first real run failed with a Windows directory error.
- The diff parser treated a removed SQL comment (`-- text`, shown as `--- text`) as a file header. A header now needs `---`, `+++` and `@@` lines in a row (sqlfluff, one of the six repositories, is full of SQL). Test included.
- The parser ignored a context line that had lost its leading space; found by my own hand-written patch fixture. Blank lines inside a hunk now count as context.

**Dependencies added.** `datasets` (Hugging Face; brings `pyarrow` and `pandas`). It is the Hugging Face tooling named in the tech line but not in the CLAUDE.md fixed-stack list; flagged for your veto. `pyarrow` alone would have been the lighter alternative but needs hand-rolled download code.

## P1.7 Baseline runs (code complete; the dense run A2 is pending, see "Blocked")

**Built.** `evaluation/runner.py` (`run_config`: per-instance query with the issue text, file and function rankings, search latency, index size), `evaluation/tracking.py` (MLflow logging with full parameters, code commit, per-instance and misses artifacts; the test-split rule), `issuescout eval --split --config A0|A1|A2|all [--freeze-id]`. Retrieval now returns chunk hits, from which files and functions are ranked. The test split is refused unless a `--freeze-id` is given, the working tree is clean, and no finished test run exists for that configuration and freeze ID (CLAUDE.md rule 5).

**Results so far (dev split, 23 instances, MLflow experiment `issuescout-retrieval`, per-repository IDF).**

| Config | Hit@1 | Hit@5 | Recall@10 | MRR | nDCG@10 | function Recall@10 |
| --- | --- | --- | --- | --- | --- | --- |
| A0 line windows, keyword | 0.304 | 0.652 | 0.783 | 0.453 | 0.529 | n/a |
| A1 function chunks, keyword | 0.391 | 0.696 | 0.739 | 0.535 | 0.581 | 0.714 (21 instances) |
| A2 function chunks, embeddings | pending | | | | | |

With 23 instances, one instance is 4.3 points of Recall@10; A0 and A1 differ by one instance on Recall@10 (18 vs 17 of 23). Treat Recall@10 as indistinguishable between them. MRR, Hit@1 and nDCG favour function-level chunks. Search latency p50 about 135 ms, p95 about 500 ms (sparse, local Qdrant, includes loading term statistics). Index size per instance: mean 3,940 chunks (A1), 2,249 (A0). Indexing throughput 190 to 294 chunks/s (sparse only; includes Qdrant writes and term statistics).

**Early gate-review signals.** (a) A plain keyword search already reaches Recall@10 = 0.74 to 0.78 on dev, so goal G2 (hybrid beats the keyword baseline by 15 points) would need about 0.93; the target needs revisiting with a test-split baseline and confidence intervals. (b) Per-repository results are very uneven (pydicom 0.4 to 0.6, pvlib and marshmallow 1.0) and n is 1 to 5 per repository.

**A finding that changed the design (ADR 0005).** The first A0 results moved between identical runs. A controlled experiment showed Qdrant's IDF modifier uses collection-wide, history-dependent statistics: adding an unrelated repository moved a repository's top score from 23.77 to 34.35 and reordered results; delete and re-add of identical content moved it from 23.77 to 23.63. IDF is now computed per repository in `indexing/termstats.py`, folded into the query, and kept exact through every add and delete. After the fix: two identical runs give identical rankings in 23 of 23 instances; a cold rebuild gave 1 of 23 differing once and 0 of 23 on the next run (probable cause: exact score ties at the 50-chunk cut-off). Ties are now broken by path and line. The two A0 and A1 runs logged before the fix were deleted from MLflow as invalid. Regression tests: `tests/integration/test_idf_isolation.py`.

**Another finding: Qwen3-Embedding-0.6B is too slow for CPU indexing.** It did not finish a cold pass over 171 chunks in 15 minutes (well under 1 chunk/s), against the SRS floor of 12 chunks/s (NFR-PERF-06) and about 16 chunks/s measured for `all-MiniLM-L6-v2`. Indexing the dev split densely with it would take more than a day. The baseline A2 therefore uses MiniLM (provisional). This feeds P2.2: the shortlisted models of 137M to 600M parameters will need either a GPU notebook (risk R-02) or a smaller model.

**Decisions.**
- The query is the raw problem statement for every configuration; the query builder is P2.3. Dense queries are truncated by the model's maximum length (FR-RET-10 is P2.3).
- Function-level recall is omitted for A0 (line windows have no function chunks). Reporting 0.0 would be wrong rather than conservative.
- A test-split run is keyed by an explicit `--freeze-id`, not by commit hash, so committing a small tweak cannot reopen the test split.
- MLflow metric names cannot contain `@`; `recall@10` is logged as `recall_at_10`.
- Windows consoles use a legacy code page and MLflow prints emoji; the CLI now reconfigures stdout and stderr to never crash on a character.

**Blocked: A2 needs disk space.** The C: drive reached 100% full (20 MB free). Docker's virtual disk is 30 GB and grew by about 2 GB while indexing the benchmark into Qdrant; it never shrinks on its own. The dense run was paused (it resumes cheaply: embeddings are cached and Qdrant writes are idempotent), and the regenerable benchmark collections were dropped so Docker stops growing. A2 and the P1.7 "three runs in MLflow" check wait on about 1 GB of free space.

**Dependencies added.** `mlflow-skinny==3.4.0` (the MLflow client, matched to the 3.4.0 server in Compose). MLflow is in the fixed stack; the package name is flagged.
