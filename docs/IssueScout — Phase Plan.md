# IssueScout — Phase Plan

Oct 10, 2026 · @wish

IssueScout is built in 46 work packages across five phases, with a gate at the end of each. The project is resume-ready after Phase 2. Requirement and test-case IDs refer to the [IssueScout SRS](https://claude.ai/code/artifact/42c9da31-df0c-4da2-b7d5-b4cd7e0d6d78).

## At a glance

&#91;embedded content: work packages by phase · 46 packages, 5 phases\]

Each column lists a phase's packages in build order, top to bottom.

## Summary

| Phase | Work packages | Ends when |
| --- | --- | --- |
| 0. Foundations | 7 | The stack runs locally and CI is green |
| 1. Baseline | 8 | Baseline retrieval numbers are measured |
| 2. Quality | 9 | The ablation is published and the CI gate is active |
| 3. GitHub App | 11 | The bot comments correctly on a sandbox repository |
| 4. Launch | 11 | Every acceptance criterion in the SRS is met |
| 5. Extensions (optional) | 5 | No gate; each item stands alone |

## How to work

These rules apply in every phase.

1. **One package, one branch, one pull request.** Merge only when CI is green. Each package below is sized to fit one or two sittings.
2. **Finish in order.** Packages are listed in the order they should be built. Where two can run in either order, the phase says so.
3. **"Done when" is the test.** Write the named test with the code, not afterwards. A package is done when its check passes, not when the code exists.
4. **Stop at the gate.** Tick every item in the gate checklist before starting the next phase. A red item is fixed or cut, never carried forward.
5. **Log every evaluation run.** Each run goes to MLflow with its configuration. Tune only on the development split; touch the test split once per frozen configuration.
6. **Keep a build log.** Note each decision, surprise, and number in one file. It becomes the README and the interview answers.
7. **Cut scope, not quality.** If a phase drags, cut from the list at the end of this plan instead of skipping tests or gates.

## Phase 0: Foundations

Phase 0 produces an empty project that runs, so that every later phase starts with coding instead of setup.

### Work packages

- [ ] **P0.1 Repository and tooling.** Create the project with uv and the source layout from SRS Appendix C. Add Ruff, mypy, pytest, and pre-commit hooks. **Done when:** a placeholder test passes and pre-commit rejects a badly formatted file.
- [ ] **P0.2 Settings and logging.** Build the typed settings module with the defaults from SRS Appendix B. Set up JSON logging with a correlation ID field. **Done when:** a test loads settings from the environment and a log line prints as valid JSON.
- [ ] **P0.3 Docker Compose.** Define Qdrant, MongoDB, RabbitMQ, and MLflow with named volumes and health checks. **Done when:** one command starts all four and every health check passes.
- [ ] **P0.4 CI workflow.** Add a GitHub Actions workflow that runs lint, type check, and tests on every push and pull request. **Done when:** it is green on main and red on a branch with a failing test.
- [ ] **P0.5 Doctor command.** Create the Typer CLI and `issuescout doctor`, which checks each service and the model cache. **Done when:** it reports all services reachable, and names Qdrant when Qdrant is stopped.
- [ ] **P0.6 GitHub App setup.** Register a development GitHub App with the four permissions in FR-APP-01. Store the private key and webhook secret in an ignored environment file. Create a sandbox repository and install the app on it. **Done when:** the app shows as installed, and no secret appears in git history.
- [ ] **P0.7 Decisions.** Write the first three decision records: stack, chunking, and vector store. Answer the open questions in SRS Appendix D. **Done when:** every open question has a written answer.

### Gate 0

- [ ] One command starts every service, and all health checks pass.
- [ ] CI is green on main.
- [ ] `issuescout doctor` reports every dependency as reachable.
- [ ] The open questions in SRS Appendix D are answered.

### Watch out for

- Do not write product code in this phase. If setup is done early, start P1.1.
- Pin every dependency version in the lock file now. Changing versions later can shift benchmark numbers.

## Phase 1: Baseline

Phase 1 builds the indexer and the evaluation harness, then measures how well plain keyword and plain dense search perform.

### Work packages

- [ ] **P1.1 Python chunker.** Parse Python files with tree-sitter into function, method, class-header, and module chunks. Record qualified names and line ranges. Split chunks over 400 tokens, and fall back to line windows on parse failure. Add the file filters. **Done when:** TC-03, TC-04, and TC-05 pass on the fixture repository.
- [ ] **P1.2 Encoders and cache.** Build the sparse tokenizer that splits identifiers and keeps the original. Wrap the dense model with batching and an on-disk embedding cache keyed by content hash. **Done when:** tokenizer tests pass, and embedding the same repository twice takes almost no time the second run.
- [ ] **P1.3 Qdrant storage.** Create the collection with dense and sparse vectors, payload indexes, and deterministic point IDs. Route every search through one function that applies the repository filter. **Done when:** TC-06 and TC-11 pass.
- [ ] **P1.4 CLI index and query.** Implement `issuescout index` and `issuescout query` in sparse and dense modes, with chunk scores rolled up to files. **Done when:** a query on a local repository prints ranked files with their symbols.
- [ ] **P1.5 Metrics module.** Implement Hit@k, Recall@k, MRR, and nDCG. **Done when:** TC-21 passes against hand-computed values.
- [ ] **P1.6 SWE-bench Lite loader.** Load both splits from Hugging Face and clone the repositories. Group instances by repository and order them by commit, so each index is reached by syncing from the previous one. Extract the gold file and gold functions from each reference patch. **Done when:** all 23 development instances are indexed, and every gold file exists at its base commit.
- [ ] **P1.7 Baseline runs.** Build the evaluation runner and `issuescout eval`. Run configurations A0, A1, and A2 on the development split and log them to MLflow. **Done when:** three runs appear in MLflow with Recall@10 and MRR, plus measured chunk counts and indexing speed.
- [ ] **P1.8 Gate review.** Replace the estimates in SRS section 6.6 with measurements. Fix the provisional recall target. Note the first failure patterns. **Done when:** the SRS is updated and the target is a number you chose from data.

### Order

Build P1.1 to P1.4 in sequence. P1.5 depends on nothing and can fill any gap. P1.6 needs P1.1 to P1.3, and P1.7 needs P1.5 and P1.6.

### Gate 1

- [ ] Recall@10 for the sparse and dense baselines is recorded in MLflow.
- [ ] The CLI returns ranked files for a query on a local repository.
- [ ] TC-03 to TC-06, TC-11, and TC-21 pass in CI.
- [ ] SRS section 6.6 holds measured values, and goal G1 has a fixed target.

### Watch out for

- P1.6 is the largest package and the easiest to underestimate. Cloning and checking out large repositories is slow, so start the downloads while you build P1.3 and let them run in the background.
- Do not improve retrieval yet. A weak baseline is the point of this phase; it is what Phase 2 is measured against.
- Check a few chunks by eye. A chunker that passes its tests can still produce odd boundaries on real code.

## Phase 2: Quality

Phase 2 adds one retrieval technique at a time and measures what each is worth, ending with the published ablation table. The project is resume-ready when this phase closes.

### Work packages

- [ ] **P2.1 Hybrid fusion.** Combine the dense and sparse searches with reciprocal rank fusion in one Qdrant query. Add the mode switch for the five retrieval modes. **Done when:** configuration A3 runs on the development split and is logged.
- [ ] **P2.2 Reranker and model selection.** Wrap the cross-encoder and measure its latency on CPU. Run the embedding shortlist on the development split as set out in SRS section 5.3. Choose both models and record their revisions. **Done when:** NFR-PERF-04 is met or the lighter reranker is chosen, and the choice is written as a decision record.
- [ ] **P2.3 Query builder.** Strip issue-template noise. Extract file paths, identifiers, quoted errors, and stack frames. Promote mentioned paths into the candidates and apply the truncation rule. **Done when:** TC-10 passes, along with unit tests on ten sample issues.
- [ ] **P2.4 File scoring and history.** Implement the file score from SRS section 4.5. Build the evaluation history index from earlier SWE-bench instances of the same repository, and add the history boost. **Done when:** configuration A5 runs, and a test proves no history item is newer than its instance.
- [ ] **P2.5 Calibration and degraded mode.** Fit the confidence thresholds on the calibration set and implement abstention. Add the fallback path for encoder or reranker failure. **Done when:** precision at high confidence is reported, and TC-25 passes.
- [ ] **P2.6 Full ablation.** Run configurations A0 to A5 on the test split with bootstrap confidence intervals. Run the three sensitivity studies on the development split. **Done when:** all six configurations are in MLflow with intervals.
- [ ] **P2.7 Report generator.** Implement `issuescout report`: the ablation table, per-repository breakdown, stage latencies, and the list of misses. **Done when:** one command rebuilds the whole report from stored results.
- [ ] **P2.8 CI quality gate.** Cache the gate indexes as Qdrant snapshots, commit the baseline file, and wire exit code 3 into CI. **Done when:** the gate passes on main and fails on a branch with a deliberately broken retriever (TC-22).
- [ ] **P2.9 Gate review.** Freeze the configuration by commit hash. Put the headline table in the README. Write the resume bullets from the report. **Done when:** every number in the bullets can be traced to the report.

### Order

Do P2.2 second, before the query builder. The embedding model must be final before the 300 test instances are indexed, and that indexing takes many hours of machine time. Start it in the background as soon as P2.2 closes, and build P2.3 to P2.5 while it runs.

### Gate 2

- [ ] The ablation table with confidence intervals is published in the repository.
- [ ] The gain of hybrid retrieval with reranking over the sparse baseline is stated (goal G2).
- [ ] Reranking meets its latency budget, or the fallback model is recorded.
- [ ] The CI gate fails on a deliberately broken retriever.
- [ ] The configuration is frozen by commit hash.
- [ ] The resume is updated with measured numbers.

### Watch out for

- Never tune on the test split. If a test number looks disappointing, go back to the development split, change something, freeze again, and rerun once.
- If test-split indexing will not finish in time, run embedding on a free notebook GPU or report on a stratified subset of 100 instances, and say so in the report.
- A technique that does not help still belongs in the table. A row showing that reranking added little is a finding, not a failure.

## Phase 3: GitHub App

Phase 3 wraps the retrieval engine in a bot that comments on issues correctly and exactly once.

### Work packages

- [ ] **P3.1 State store.** Create the MongoDB collections and indexes from SRS section 6, including the TTL and unique indexes, behind a small repository layer. **Done when:** inserting the same delivery ID twice fails, and integration tests pass against a real MongoDB.
- [ ] **P3.2 GitHub client.** Sign the app JWT, exchange it for installation tokens held in memory, and add REST and GraphQL helpers that respect rate limits. Build a stub GitHub server for tests. **Done when:** the client posts and edits a comment on the sandbox repository, and no token is ever written to disk or logs.
- [ ] **P3.3 Webhook gateway.** Build the FastAPI endpoint: verify the signature, drop duplicates, route by event, and answer 202. Add the health and readiness endpoints. **Done when:** TC-01 and TC-02 pass.
- [ ] **P3.4 Queue and workers.** Configure Celery on RabbitMQ with the queues and priorities from SRS section 8.4, retries with backoff, the dead-letter queue, and a per-repository lock. Carry the correlation ID through every job. **Done when:** a task that always fails lands in the dead-letter queue after three attempts.
- [ ] **P3.5 Install and index flow.** Handle installation events, create repository records, and run the full index as a new version that is activated only on success. Enforce size limits, resume interrupted jobs, and delete data on uninstall. **Done when:** TC-17 and TC-24 pass.
- [ ] **P3.6 Incremental sync.** On a push, fetch, diff against the indexed commit, and re-chunk only changed files. Merge rapid pushes and fall back to a full index after a force push. **Done when:** TC-07, TC-08, and TC-09 pass.
- [ ] **P3.7 Live history.** Ingest closed issues with their closing pull requests and changed files through GraphQL. Handle the issue-closed event. **Done when:** the sandbox repository's closed issues appear in the history index with their fixed files.
- [ ] **P3.8 Response flow.** Implement the gates, the `auto_comment` setting, the locate command, abstention, the daily cap, and dry-run mode. Store a response record for every decision. **Done when:** TC-12 and TC-23 pass.
- [ ] **P3.9 Generator.** Write the prompt and output schema, verify every citation, enforce the hypothesis wording, and fall back to retrieval-only on timeout. Choose the language model with a small comparison. **Done when:** TC-13, TC-14, and TC-15 pass.
- [ ] **P3.10 Comment renderer.** Render the comment from a template with permalinks pinned to the indexed commit, the truncation order, and the hidden marker. Edit the existing comment on a rerun. **Done when:** TC-16 and TC-19 pass, with snapshot tests for the output.
- [ ] **P3.11 End-to-end run.** Run the whole stack against the stub server in CI. Then open 10 scripted issues on the sandbox repository, first in dry-run mode and then live. **Done when:** the scripted issues behave as expected, and redelivering every webhook adds no comment.

### Order

P3.1 to P3.6 form the main line and are built in sequence. P3.7 needs only P3.2. P3.9 and P3.10 can be developed against CLI output before the queue exists, so move them earlier if the main line is blocked.

### Gate 3

- [ ] Ten scripted issues on the sandbox repository give the expected result: comments on the clear ones, silence on the vague ones.
- [ ] Redelivering every webhook creates no duplicate comment.
- [ ] A push updates the index (TC-07 to TC-09).
- [ ] TC-01, TC-02, TC-12 to TC-16, and TC-23 to TC-25 pass in CI.
- [ ] Every link in a posted comment opens the right lines at the indexed commit.

### Watch out for

- Keep dry-run mode on until P3.11. A bug in this phase can post wrong comments in public.
- Idempotency is the hardest part. Test every handler by running it twice on the same input.
- Start asking maintainers of small projects now whether they would try the app. Installs take time to arrange, and Phase 4 needs five.

## Phase 4: Launch

Phase 4 puts the service into production, makes it observable and safe, and gets it onto real repositories.

### Work packages

- [ ] **P4.1 Deploy.** Build non-root production images. Set up the EC2 instance, Caddy for HTTPS, and secrets. Automate deployment from GitHub Actions and schedule daily volume snapshots. **Done when:** a merge to main reaches production without manual steps, and the sandbox app works against the live URL.
- [ ] **P4.2 Observability.** Expose the Prometheus metrics in NFR-OBS-01 and add the four alerts. Build a Grafana dashboard only if time allows. **Done when:** a test issue shows up in the metrics with its stage timings, and stopping the worker fires an alert.
- [ ] **P4.3 Security suite.** Write the 20 adversarial issues and the cross-repository test. Add secret-file exclusion, credential redaction, the private-repository default, and dependency and image scanning in CI. **Done when:** the whole suite passes in CI (AC-07).
- [ ] **P4.4 Load test.** Use Locust to send 100 webhooks a second to the gateway and a burst of 20 issues in a minute. **Done when:** the measured 95th percentiles meet AC-08 and AC-09, or the gap is explained in the report.
- [ ] **P4.5 Mention answers.** Handle mentions of the bot: retrieve, generate a cited answer of at most 150 words, apply rate limits, and decline off-topic requests. **Done when:** five test questions on the sandbox repository get cited answers and an off-topic one is declined.
- [ ] **P4.6 Config file and commands.** Parse `.issuescout.yml` with safe defaults. Implement pause, resume, reindex, and status with a permission check. **Done when:** TC-18 passes and an invalid file is reported by the status command.
- [ ] **P4.7 Feedback and live hit rate.** Poll reactions daily. When a commented issue is closed by a merged pull request, record whether its files were in the top five. **Done when:** both numbers appear as metrics for the sandbox repository.
- [ ] **P4.8 More languages.** Add tree-sitter chunking for Java, JavaScript, and TypeScript with fixture tests. **Done when:** a fixture file in each language yields chunks with correct names and line ranges.
- [ ] **P4.9 Answer evaluation.** Write the 50-question set, score faithfulness with RAGAS, and check citation validity across all generated comments. **Done when:** both figures are in the report (AC-10 and AC-11).
- [ ] **P4.10 README and demo.** Write the README with the architecture diagram, results table, setup steps, and privacy note. Record a short demo. **Done when:** someone else can run the stack locally from the README alone.
- [ ] **P4.11 Installs and acceptance.** Install the app on at least five repositories, in dry-run mode first. Walk through all 14 acceptance criteria and record the evidence for each. **Done when:** every criterion has a link to its evidence.

### Order

Deploy first. Real repositories need days to produce issues, so the sooner the service is live in dry-run mode, the more real traffic exists by the end of the phase. P4.5 to P4.8 are independent of each other and can be reordered freely.

### Gate 4

- [ ] All 14 acceptance criteria in SRS section 15 are met, each with evidence.
- [ ] The service is deployed and installed on at least five repositories.
- [ ] At least 20 real issues have been processed, counting both comments and decisions to stay silent.
- [ ] The README covers architecture, results, setup, and privacy.
- [ ] The resume carries the final numbers and the install count.

### Watch out for

- Do the first installs on your own repositories, then on ones where you know the maintainer. Leave dry-run mode only after reading what the bot would have posted.
- Twenty real issues may take a while to arrive. Until they do, report the true count and keep the service running.

## Phase 5: Extensions (optional)

Phase 5 starts only after Gate 4. Each item stands alone, so take them in whatever order is most useful.

- [ ] **P5.1 Mined gold set.** Build the gold set from Java, JavaScript, and TypeScript repositories, with a hand-curated subset of 50 instances (FR-EVL-05 and FR-EVL-15).
- [ ] **P5.2 Larger CI gate.** Grow the gate set from 23 to 100 instances using the mined set.
- [ ] **P5.3 Go support.** Add tree-sitter chunking for Go (FR-IDX-06).
- [ ] **P5.4 Tracing.** Add Langfuse traces per response (NFR-OBS-02).
- [ ] **P5.5 Remaining Could items.** FR-HIS-06, FR-LOC-11, FR-FBK-02, NFR-AVL-04, and NFR-OBS-05.

## If time runs short

Cut scope in this order instead of skipping tests or gates. Every item on the first list is a Should or Could requirement, so cutting it does not block acceptance of the Must requirements.

### Cut first, in this order

1. P4.8 More languages. The benchmark and the first installs can be Python only.
2. P4.7 Feedback and live hit rate.
3. P4.5 Mention answers.
4. P4.6 Config file and commands, keeping only the `auto_comment` switch and the locate command from Phase 3.
5. The sensitivity studies in P2.6.
6. The alerts in P4.2, keeping the metrics endpoint.

### Never cut

- P1.6 and P2.6: the benchmark is the project's main evidence.
- P2.8: the CI gate is what makes the numbers trustworthy over time.
- P3.3 and the idempotency tests: a bot that double-posts gets uninstalled.
- Citation verification in P3.9: an invented file path destroys trust in one comment.
- P4.3: the bot reads untrusted text and writes in public.

### If you need to stop early

Stop after Gate 2. Phases 0 to 2 give a complete, measured retrieval project with a CLI. The GitHub App can follow later without changing anything already built.
