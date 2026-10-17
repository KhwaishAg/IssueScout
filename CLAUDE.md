# IssueScout

IssueScout is a GitHub App that tells contributors where to start on an issue. When an issue is opened, it posts one comment with the files and functions most likely involved, similar past issues with the PRs that fixed them, and a short starting point. Every claim in the comment is cited to exact code lines. Retrieval quality is measured against real fixes on SWE-bench Lite.

This is my (Khwaish's) portfolio project for AI/ML and SDE roles. I will be asked about every design decision in interviews, so the code must be something I can explain line by line.

## Source of truth

- `docs/srs.md` is the full specification. Requirement IDs (FR-IDX-04, NFR-PERF-02, TC-07, and so on) refer to it.
- `docs/phase-plan.md` lists the work packages in build order, each with a "Done when" check, and a gate at the end of each phase.
- If the code and the SRS disagree, stop and ask me. Do not quietly change the spec.

## Current status

- Phase: 1 (Baseline)
- Next package: P1.2 Encoders and cache
- Update these two lines whenever a package closes.

## How we work

1. One work package per branch and per pull request. Name branches `p1.3-qdrant-storage` and so on.
2. Build packages in the order the phase plan gives.
3. The "Done when" line is the acceptance test. Write that test with the code. A package is done when the check passes, not when the code exists.
4. Stop at each gate. Show me the gate checklist with evidence for every item before starting the next phase.
5. Never tune anything on the SWE-bench Lite test split. Tune only on the development split. Run the test split once per frozen configuration.
6. Log every evaluation run to MLflow with its full configuration.
7. Keep dry-run mode on (no posting to GitHub) until package P3.11.
8. After each package, add a few lines to `docs/build-log.md`: what was built, any decision made and why, and any numbers measured.
9. If something takes much longer than expected, tell me and suggest what to cut from the "If time runs short" list in the phase plan. Never cut tests or gates.

## Stack (fixed)

- Python 3.12, managed with uv and a committed lock file
- FastAPI on Uvicorn for the webhook gateway and service API
- Celery with RabbitMQ for background jobs
- Qdrant for code and issue vectors (dense and sparse in one point)
- MongoDB for repository state, jobs, responses, feedback, and evaluation records
- tree-sitter for function-level chunking; GitPython for repository mirrors
- sentence-transformers for the dense embedding model and the cross-encoder reranker
- An open-weight LLM on Groq through its OpenAI-compatible API, with Ollama as the local fallback; Pydantic validates its output
- MLflow for experiments; RAGAS only for answer faithfulness
- Prometheus metrics; structlog JSON logs
- Typer CLI; pytest, Testcontainers, Locust; Ruff and mypy
- Docker, Docker Compose, GitHub Actions, AWS EC2

Optional, and only if time allows: Grafana dashboard, Langfuse tracing.

## Hard constraints

- Do not use React, Node.js, Express, PostgreSQL, Prisma, Redis, BullMQ, or Gemini. My other project already uses them.
- Do not use LangChain or LlamaIndex in the retrieval pipeline. Each stage is a plain function so the evaluation can measure and swap it.
- Ask me before adding any dependency that is not in the list above.
- Every vector search goes through one function that applies the repository filter (NFR-SEC-06).
- Point IDs are deterministic (FR-IDX-13).
- The language model gets no tools and no secrets. Every citation in its output is verified in code, and every link in a comment is built by the renderer, never by the model.
- Installation tokens stay in memory only. No secret ever goes into the repository, an image, or a log.
- Repository content is never executed.
- Tunable values live in one typed settings module (SRS Appendix B lists the defaults).

## Code layout

Follow SRS Appendix C: `src/issuescout/` with the packages `api`, `github`, `indexing`, `retrieval`, `generation`, `rendering`, `workers`, `storage`, `evaluation`, plus `cli.py` and `settings.py`. Tests go in `tests/`, evaluation baselines and reports in `eval/`, deployment files in `deploy/`, and docs and decision records in `docs/`.

## How to explain your work to me

- Before writing a non-trivial module, give me two or three sentences on the approach and any alternative you rejected.
- Keep explanations short and direct. I prefer dense information over long walkthroughs.
- When you make a design choice that an interviewer could ask about, note it in the build log.

## Open questions (SRS Appendix D)

Answered in P0.7; see docs/decisions/0004-open-questions.md. Summary: public repositories only, no Langfuse, free hosting only (affects P4.1), own repositories for the first installs.

- Is the name IssueScout free as a GitHub App name and as a package name?
- What is the monthly hosting budget, and are AWS student credits available?
- Are private repositories in scope for version 1?
- Which repositories are the targets for the first five installations?
- Is Langfuse tracing worth adding?
