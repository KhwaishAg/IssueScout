# 0003. Vector store

Status: accepted (P0.7) · Covers: SRS 5.1, 6.1, FR-RET-01, FR-IDX-13, NFR-SEC-06

## Context
Search must combine meaning (dense) and exact identifiers (sparse), be restricted to one
repository per query, and stay idempotent when the same commit is indexed twice.

## Decision
Use **Qdrant**: one collection per kind (`code_chunks`, `issues`), each point holding a
named `dense` vector (cosine) and a `sparse` vector (IDF modifier). Tenancy is a payload
filter on an indexed `repo_id`, applied in exactly **one** search function. Point IDs are
UUIDv5 of repository, index version, path, symbol, and content hash.

## Why
- Dense and sparse vectors live in one point, and reciprocal rank fusion runs server side,
  so the hybrid ablation needs one query instead of two systems.
- Indexed payload filters make per-repository isolation cheap; a single filtered
  collection avoids managing hundreds of collections.
- Deterministic IDs make re-indexing an upsert, so TC-06 (no duplicates) holds by
  construction. Index versions let a full rebuild swap in only on success.
- pgvector is out because PostgreSQL is banned by the CareLoop overlap rule; FAISS has no
  server, filtering, or persistence story.

## Alternatives rejected
pgvector (stack overlap); FAISS (no filters/server); collection per repository (management
overhead); Elasticsearch (heavy for one VM).

## Consequences
Isolation depends on one function never being bypassed; NFR-SEC-06 and TC-11 test it.
Dense memory (about 120 MB per 40k chunks) fits the VM; scalar quantisation is the planned
escape hatch if memory binds.