"""P1.3 'Done when': TC-06 (no duplicates on re-index) and TC-11 (repository isolation)."""

import hashlib

import pytest
from qdrant_client import QdrantClient

from issuescout.indexing.chunker import chunk_file
from issuescout.indexing.models import Chunk
from issuescout.indexing.sparse import (
    embedding_text,
    sparse_document_vector,
    sparse_query_vector,
)
from issuescout.settings import Settings
from issuescout.storage.qdrant import (
    Hit,
    count_points,
    delete_repository,
    ensure_collection,
    point_id,
    search_code,
    upsert_chunks,
)

DIM = 16
SETTINGS = Settings(_env_file=None)


def fake_dense(text: str) -> list[float]:
    """Deterministic unit-length vector derived from a hash; equal text gives equal vectors."""
    digest = hashlib.sha256(text.encode()).digest()
    raw = [(b - 127.5) / 127.5 for b in digest[:DIM]]
    norm = sum(x * x for x in raw) ** 0.5
    return [x / norm for x in raw]


def make_chunks(path: str, source: str) -> list[Chunk]:
    return chunk_file(path, source.encode(), SETTINGS)


def index(
    client: QdrantClient,
    collection: str,
    chunks: list[Chunk],
    *,
    repo_id: int = 1,
    version: int = 1,
    sha: str = "abc1234",
) -> int:
    texts = [embedding_text(c) for c in chunks]
    return upsert_chunks(
        client,
        chunks,
        [fake_dense(t) for t in texts],
        [sparse_document_vector(t, SETTINGS) for t in texts],
        repo_id=repo_id,
        installation_id=99,
        commit_sha=sha,
        index_version=version,
        collection=collection,
    )


RETRY_SRC = "def retry_request(url):\n    return fetch(url)\n"
PARSE_SRC = "def parse_config(path):\n    return load(path)\n"
TEST_SRC = "def test_retry():\n    assert True\n"


def test_tc06_indexing_the_same_commit_twice_adds_no_points(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    chunks = make_chunks("pkg/net.py", RETRY_SRC + "\n\n" + PARSE_SRC)
    index(qdrant_client, collection, chunks)
    first = count_points(qdrant_client, repo_id=1, collection=collection)
    index(qdrant_client, collection, chunks)
    assert first == len(chunks) > 0
    assert count_points(qdrant_client, repo_id=1, collection=collection) == first


def test_changed_chunk_gets_a_new_point_unchanged_ones_keep_theirs(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    before = make_chunks("pkg/net.py", RETRY_SRC + "\n\n" + PARSE_SRC)
    after = make_chunks("pkg/net.py", RETRY_SRC + "\n\n" + PARSE_SRC.replace("load", "read"))
    ids_before = {c.symbol: point_id(1, 1, c) for c in before}
    ids_after = {c.symbol: point_id(1, 1, c) for c in after}
    assert ids_before["pkg.net.retry_request"] == ids_after["pkg.net.retry_request"]
    assert ids_before["pkg.net.parse_config"] != ids_after["pkg.net.parse_config"]


def test_point_ids_differ_by_repo_and_version() -> None:
    chunk = make_chunks("a.py", RETRY_SRC)[0]
    assert point_id(1, 1, chunk) == point_id(1, 1, chunk)
    assert point_id(1, 1, chunk) != point_id(2, 1, chunk)
    assert point_id(1, 1, chunk) != point_id(1, 2, chunk)


def test_tc11_search_scoped_to_one_repository_never_returns_another(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    a_chunks = make_chunks("a/net.py", RETRY_SRC)
    b_chunks = make_chunks("b/net.py", RETRY_SRC)  # same code, different repository
    index(qdrant_client, collection, a_chunks, repo_id=1)
    index(qdrant_client, collection, b_chunks, repo_id=2)

    query_text = embedding_text(b_chunks[0])  # identical to a chunk that exists in both repos
    for repo in (1, 2):
        dense_hits = search_code(
            qdrant_client,
            repo_id=repo,
            index_version=1,
            dense=fake_dense(query_text),
            collection=collection,
        )
        sparse_hits = search_code(
            qdrant_client,
            repo_id=repo,
            index_version=1,
            sparse=sparse_query_vector("retry_request fetch"),
            collection=collection,
        )
        assert dense_hits and sparse_hits
        assert {h.repo_id for h in dense_hits + sparse_hits} == {repo}
        assert {h.path.split("/")[0] for h in dense_hits + sparse_hits} == {
            "a" if repo == 1 else "b"
        }


def test_search_for_unknown_repository_is_empty(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    index(qdrant_client, collection, make_chunks("a.py", RETRY_SRC), repo_id=1)
    hits = search_code(
        qdrant_client, repo_id=404, index_version=1, dense=fake_dense("x"), collection=collection
    )
    assert hits == []


def test_search_only_sees_the_requested_index_version(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    index(qdrant_client, collection, make_chunks("a.py", RETRY_SRC), version=1)
    index(qdrant_client, collection, make_chunks("a.py", PARSE_SRC), version=2)
    v2 = search_code(
        qdrant_client,
        repo_id=1,
        index_version=2,
        sparse=sparse_query_vector("parse_config retry_request"),
        collection=collection,
    )
    assert {h.index_version for h in v2} == {2}
    assert {h.symbol for h in v2} == {"a.parse_config"}


def test_sparse_search_ranks_the_matching_chunk_first(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    chunks = make_chunks("pkg/net.py", RETRY_SRC + "\n\n" + PARSE_SRC)
    index(qdrant_client, collection, chunks)
    hits = search_code(
        qdrant_client,
        repo_id=1,
        index_version=1,
        sparse=sparse_query_vector("the retry request is broken"),
        collection=collection,
    )
    assert hits[0].symbol == "pkg.net.retry_request"
    assert (hits[0].start_line, hits[0].end_line) == (1, 2)


def test_dense_search_ranks_the_identical_text_first(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    chunks = make_chunks("pkg/net.py", RETRY_SRC + "\n\n" + PARSE_SRC)
    index(qdrant_client, collection, chunks)
    target = next(c for c in chunks if c.symbol.endswith("parse_config"))
    hits = search_code(
        qdrant_client,
        repo_id=1,
        index_version=1,
        dense=fake_dense(embedding_text(target)),
        collection=collection,
    )
    assert hits[0].symbol == "pkg.net.parse_config"
    assert hits[0].score == pytest.approx(1.0, abs=1e-4)


def test_exclude_tests_filter(qdrant_client: QdrantClient, collection: str) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    index(qdrant_client, collection, make_chunks("pkg/net.py", RETRY_SRC))
    index(qdrant_client, collection, make_chunks("tests/test_net.py", TEST_SRC))
    query = sparse_query_vector("retry")
    with_tests = search_code(
        qdrant_client, repo_id=1, index_version=1, sparse=query, collection=collection
    )
    without = search_code(
        qdrant_client,
        repo_id=1,
        index_version=1,
        sparse=query,
        exclude_tests=True,
        collection=collection,
    )
    assert any(h.is_test for h in with_tests)
    assert not any(h.is_test for h in without)


def test_delete_repository_removes_only_that_repository(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    index(qdrant_client, collection, make_chunks("a.py", RETRY_SRC), repo_id=1)
    index(qdrant_client, collection, make_chunks("a.py", RETRY_SRC), repo_id=2)
    delete_repository(qdrant_client, repo_id=1, collection=collection)
    assert count_points(qdrant_client, repo_id=1, collection=collection) == 0
    assert count_points(qdrant_client, repo_id=2, collection=collection) == 1


def test_ensure_collection_is_idempotent_and_rejects_a_different_dimension(
    qdrant_client: QdrantClient, collection: str
) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    ensure_collection(qdrant_client, DIM, collection)
    with pytest.raises(ValueError, match="dense size"):
        ensure_collection(qdrant_client, DIM + 1, collection)


def test_search_requires_at_least_one_query_vector(
    qdrant_client: QdrantClient, collection: str
) -> None:
    with pytest.raises(ValueError, match="dense, sparse or both"):
        search_code(qdrant_client, repo_id=1, index_version=1, collection=collection)


def _hybrid_corpus(qdrant_client: QdrantClient, collection: str, repo_id: int = 1) -> list[Chunk]:
    ensure_collection(qdrant_client, DIM, collection)
    zeta = "def unrelated_zeta():\n    return 0\n"
    chunks = make_chunks("pkg/net.py", RETRY_SRC + "\n\n" + PARSE_SRC + "\n\n" + zeta)
    index(qdrant_client, collection, chunks, repo_id=repo_id)
    return chunks


def _search(
    client: QdrantClient,
    collection: str,
    *,
    dense: list[float] | None = None,
    sparse: tuple[list[int], list[float]] | None = None,
    repo: int = 1,
    limit: int = 10,
) -> list[Hit]:
    return search_code(
        client,
        repo_id=repo,
        index_version=1,
        dense=dense,
        sparse=sparse,
        limit=limit,
        collection=collection,
    )


def test_hybrid_returns_the_union_of_both_signals(
    qdrant_client: QdrantClient, collection: str
) -> None:
    chunks = _hybrid_corpus(qdrant_client, collection)
    target = next(c for c in chunks if c.symbol.endswith("parse_config"))
    dense_q = fake_dense(embedding_text(target))  # dense: finds parse_config
    sparse_q = sparse_query_vector("retry_request")  # keywords: find retry_request
    dense_only = {h.symbol for h in _search(qdrant_client, collection, dense=dense_q)}
    sparse_only = {h.symbol for h in _search(qdrant_client, collection, sparse=sparse_q)}
    both = {h.symbol for h in _search(qdrant_client, collection, dense=dense_q, sparse=sparse_q)}
    assert "pkg.net.parse_config" in dense_only
    assert "pkg.net.retry_request" in sparse_only
    assert both >= dense_only | sparse_only  # nothing either signal found is lost


def test_hybrid_is_scoped_to_the_repository_in_every_stage(
    qdrant_client: QdrantClient, collection: str
) -> None:
    """TC-11 for the fused path: the prefetch stages each carry the repository filter."""
    chunks = _hybrid_corpus(qdrant_client, collection, repo_id=1)
    index(qdrant_client, collection, chunks, repo_id=2)  # identical code in repository 2
    query = fake_dense(embedding_text(chunks[0]))
    for repo in (1, 2):
        hits = _search(
            qdrant_client,
            collection,
            dense=query,
            sparse=sparse_query_vector("retry_request fetch"),
            repo=repo,
            limit=20,
        )
        assert hits
        assert {h.repo_id for h in hits} == {repo}


def test_hybrid_is_deterministic(qdrant_client: QdrantClient, collection: str) -> None:
    chunks = _hybrid_corpus(qdrant_client, collection)
    dense_q = fake_dense(embedding_text(chunks[0]))
    sparse_q = sparse_query_vector("retry parse config zeta")
    runs = [
        [
            (h.path, h.start_line, h.score)
            for h in _search(qdrant_client, collection, dense=dense_q, sparse=sparse_q)
        ]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]


def test_hybrid_with_no_usable_keywords_falls_back_to_the_dense_ranking(
    qdrant_client: QdrantClient, collection: str
) -> None:
    chunks = _hybrid_corpus(qdrant_client, collection)
    vec = fake_dense(embedding_text(chunks[0]))
    dense_only = _search(qdrant_client, collection, dense=vec)
    empty_terms = _search(qdrant_client, collection, dense=vec, sparse=([], []))
    assert [h.symbol for h in empty_terms] == [h.symbol for h in dense_only]


def test_empty_sparse_query_matches_nothing(qdrant_client: QdrantClient, collection: str) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    assert (
        search_code(
            qdrant_client, repo_id=1, index_version=1, sparse=([], []), collection=collection
        )
        == []
    )


def test_mismatched_lengths_are_rejected(qdrant_client: QdrantClient, collection: str) -> None:
    ensure_collection(qdrant_client, DIM, collection)
    chunks = make_chunks("a.py", RETRY_SRC)
    with pytest.raises(ValueError, match="equal length"):
        upsert_chunks(
            qdrant_client,
            chunks,
            [],
            [],
            repo_id=1,
            installation_id=1,
            commit_sha="x",
            index_version=1,
            collection=collection,
        )
