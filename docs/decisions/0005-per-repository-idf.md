# 0005. Keyword weighting is computed per repository, not by Qdrant

Status: accepted (P1.7). **Departs from SRS 5.2** ("IDF applied by Qdrant") and touches the
tenancy choice in SRS 4.8; the matching SRS edit is proposed at the Gate 1 review.

## Context
The SRS keeps every repository in one Qdrant collection, filtered by `repo_id`, and has
Qdrant apply IDF to the sparse (keyword) vectors through the `IDF` modifier. While running
the first baselines I saw A0 results change between two identical runs, and measured why.

On a controlled index of one repository:

| Step | Top sparse score |
| --- | --- |
| Index repository A, query | 23.77 |
| Delete 25 files and re-add identical content, query | 23.63 |
| Add an unrelated repository B to the same collection, query A | 34.35, and the order of A's results changed |

## Decision
Switch the modifier off. Documents store the BM25 term-frequency weight only. A small
per-repository table of document frequencies (`indexing/termstats.py`) gives each query
term its IDF, and that IDF is folded into the query vector. The dot product Qdrant computes
is then the full BM25 sum, over this repository's own chunks only.

The table is updated whenever chunks are added or removed (the text of a point is read
back from its payload before deletion), is compared with the live point count before use,
and is rebuilt from the index if they disagree. Equal scores are ordered by path and line.

## Why
* **Isolation.** A repository's ranking must not depend on which other repositories are
  installed; with the modifier it did (NFR-SEC-06 is about leaks of code; this is a leak of
  ranking influence between tenants).
* **Reproducibility.** Identical index and query must give identical results (FR-RET-12),
  and benchmark numbers are only meaningful if reruns agree.
* **Keeps the tenancy decision.** One collection with a payload filter stays; only where
  IDF comes from changes.

## Alternatives rejected
* One collection per repository: isolates IDF but still depends on write history inside the
  collection (deleted points kept counting), and adds many collections to manage.
* Rebuilding a fresh collection per benchmark instance: deterministic but far too slow for
  300 instances.
* Leaving it and reporting the variance: the effect (about 10 points of score, reordered
  results) is larger than the differences the ablation is meant to measure.

## Consequences
* A new bookkeeping duty: the statistics must follow every add and delete. Tests assert
  that stored statistics equal statistics rebuilt from the index after arbitrary
  histories, that another repository's data changes nothing, and that write history
  changes nothing.
* Production keeps the same table in MongoDB (P3.1) instead of a file.
* Dense search is unaffected: cosine scores do not depend on collection statistics.
* Ties at the exact cut-off of the 50 retrieved chunks can still be broken arbitrarily by
  Qdrant; the residual effect measured so far is at most one instance in 23.
