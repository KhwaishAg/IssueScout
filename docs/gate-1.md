# Gate 1 review

Date: 2026-10-17 · Phase 1 (Baseline) · Evidence for every item, including what is missing.

**Verdict: Gate 1 is met** (updated after the dense baseline A2 was run once disk space was
freed). Every item below passes.

## Checklist

| # | Item | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Recall@10 for the sparse and dense baselines is recorded in MLflow | Met | Experiment `issuescout-retrieval`: A0 run `c5d8ebad`, A1 run `d532b1b6`, A2 run `8c39e9a0`. |
| 2 | The CLI returns ranked files for a query on a local repository | Met | P1.4: `issuescout index src` then `issuescout query ...` printed ranked files with symbols and line ranges in sparse and dense mode (build log). |
| 3 | TC-03 to TC-06, TC-11 and TC-21 pass in CI | Met | CI run `39034706543` on `7ca6c97`: green, 192 tests, including Qdrant integration tests (service container). TC-03/04/05: `tests/unit/test_chunker.py`; TC-06/11: `tests/integration/test_qdrant_storage.py`; TC-21: `tests/unit/test_metrics.py`. |
| 4 | SRS section 6.6 holds measured values, and G1 has a fixed target | Met | SRS 6.6 rewritten from measurements (`scripts/measure_volumes.py`); G1 fixed at 0.85; see below. |

## Baseline results (dev split, 23 instances)

| Config | Hit@1 | Hit@5 | Recall@10 | MRR | nDCG@10 | function Recall@10 |
| --- | --- | --- | --- | --- | --- | --- |
| A0 line windows, keyword | 0.304 | 0.652 | 0.783 (18/23) | 0.453 | 0.529 | n/a |
| A1 function chunks, keyword | 0.391 | 0.696 | 0.739 (17/23) | 0.535 | 0.581 | 0.714 (21 instances) |
| A2 function chunks, embeddings (all-MiniLM-L6-v2) | 0.174 | 0.478 | 0.609 (14/23) | 0.317 | 0.376 | 0.333 (21 instances) |

How much to trust these: one instance is 4.3 points of Recall@10, so A0 and A1 are tied
on Recall@10 (they differ by one instance). Function-level chunking improves ranking
quality (Hit@1 0.30 to 0.39, MRR 0.45 to 0.54), which is the more informative difference.
The dev split is six small repositories (1 to 5 instances each), and results are uneven:
pvlib and marshmallow 1.0, pydicom 0.4 to 0.6, astroid 0.8 to 1.0, sqlfluff 0.6.
Per-instance results and the misses are stored as MLflow artifacts.

The dense baseline is clearly weaker than keyword search on this data (Recall@10 0.61
against 0.74 to 0.78; Hit@1 0.17 against 0.30 to 0.39). Its errors are different, though:
it finds all five pydicom instances (Recall@10 1.0, where keyword search found 0.4 to 0.6)
because it bridges the vocabulary gap, and does badly on sqlfluff (0.2) and astroid (0.4).
Complementary strengths are exactly what fusion (P2.1) is meant to use. The model is a
small provisional one (22M parameters); P2.2 chooses the real model by measurement.

## Decision: fix the recall target (G1) and revise G2

* **Data.** Plain keyword search already reaches Recall@10 of 0.74 to 0.78 on dev. The
  union of A0 and A1 (an instance counts if either finds it) reaches 19/23 = 0.83.
  The two variants miss largely the same instances (16 of 23 hit in both).
* **G1: 0.85 on the test split** (was 0.70, provisional). 0.70 is already beaten by a
  keyword search on dev, so it would measure nothing. 0.85 is above the best single
  baseline (0.78) and above the 0.83 ceiling of combining the two keyword variants, so
  reaching it needs real gains from fusion, reranking and query building. The failure
  analysis below says about half of the current misses are addressable by those.
* **G2: 8 percentage points** over the stronger keyword baseline, with a paired bootstrap
  interval (was 15). With a baseline near 0.78, 15 points would need 0.93. Eight points is
  about two instances on dev and is detectable on 300 instances (the interval will say).
* **Caveats, stated up front.** These numbers come from 23 instances in six small
  repositories; the test split is dominated by django and sympy, which are much larger, so
  the baseline there is probably lower. The test split was deliberately not run to
  calibrate the target (it may be run once per frozen configuration). If the test
  baseline turns out far below the dev baseline, the honest outcome under risk R-04 is to
  publish the real numbers with the error analysis, not to move the target afterwards.
  The targets are a decision for the project owner and can be overruled.

## First failure patterns (A1 misses: 6 of 23 instances outside the top 10)

| Instance | Gold file rank | What happened |
| --- | --- | --- |
| pydicom-1256 | 14 | Top results are `.github/ISSUE_TEMPLATE/*.md`: the issue's template words ("Describe the bug", "Steps to reproduce") match template files. |
| astroid-1333 | 14 | Top results are `.github/ISSUE_TEMPLATE.md`, `ci.yaml`, `pylintrc`: template and configuration text. |
| sqlfluff-1733 | none | Top results are the bug-report template and two docs pages. |
| pydicom-1139 | 25 | Symptom described in user terms ("make PersonName3 iterable"); top results are tutorial and migration docs. The fix is module-level code (no gold function). |
| pydicom-1413 | 29 | A `TypeError` message about `MultiValue`; the top results are a data dictionary and docs. Vocabulary mismatch. |
| sqlfluff-1763 | none | A 6,700-character issue (dbt, stack traces); results are config and the changelog. |

Patterns, with the Phase 2 work that addresses each:

1. **Template and boilerplate text matches non-code files** (3 of 6). Query builder strips
   template headings (P2.3); test and doc files are excluded or down-weighted (FR-RET-11).
2. **Documentation, configuration and changelogs outrank code** (all 6 have a non-source
   file in the top three). Same fix; a reranker should also help.
3. **Vocabulary mismatch** (2 of 6: user symptom words vs code names). Dense retrieval and
   reranking are the intended answer, which is why A2 matters.
4. **Very long issues dilute the query** (1 of 6). Truncation rule and identifier
   extraction (P2.3).
5. Context for the strong baseline: the gold file's module name appears in the issue text
   in 18 of 23 instances, and a gold function name in 11 of 23. A keyword search is hard to
   beat when the reporter names the code.

## Findings that changed the design (see ADR 0005 and the build log)

* **Qdrant's collection-wide IDF made rankings depend on other repositories** (a score of
  23.8 became 34.4 after an unrelated repository was added). Replaced by per-repository
  IDF with regression tests. SRS 5.2 and 6.1 edited accordingly.
* **Equal scores are returned in an order that depends on write history.** Ties are now
  broken by path and line. One residual mismatch in one cold-rebuild comparison (0 of 23 on
  the repeat) is probably an exact tie at the 50-chunk cut-off.
* **Qwen3-Embedding-0.6B cannot index on this CPU** (under 1 chunk/s). P2.2 must choose a
  model that meets NFR-PERF-06, or use a GPU notebook (risk R-02).
* **Long non-Python windows exceed the embedding limit.** 60-line windows of YAML and SQL
  reach 1,500 tokens at the 95th percentile; only their start is embedded. P2.6's chunk
  size study should include these.

## Open items carried into Phase 2

1. **Disk space.** The C: drive hit 100% full during indexing; about 2.4 GB is free now.
   Docker's virtual disk is 30 GB and does not shrink by itself, so large indexing runs
   (the 300-instance test split) need a plan before P2.6.
2. P1.8 did not run the test split, by design.
