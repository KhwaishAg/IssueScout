"""Integration fixtures: a real Qdrant with one throwaway collection per test module.

Locally the tests skip when Qdrant is not running (start it with docker compose). In CI
(the ``CI`` environment variable is set) a missing service is a failure, so a broken
service container can never turn the suite silently green.

Creating a collection with its payload indexes takes seconds, so each module creates one
and every test starts from an empty copy of it (all points deleted), not a new collection.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from qdrant_client import QdrantClient, models

from issuescout.settings import Settings
from issuescout.storage.qdrant import ensure_collection

TEST_DENSE_DIM = 16


@pytest.fixture(scope="session")
def qdrant_client() -> Iterator[QdrantClient]:
    url = Settings(_env_file=None).qdrant_url
    client = QdrantClient(url=url)
    try:
        client.get_collections()
    except Exception as exc:
        message = f"Qdrant not reachable at {url}: {type(exc).__name__}"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    yield client
    client.close()


@pytest.fixture(scope="module")
def _module_collection(qdrant_client: QdrantClient) -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex[:12]}"
    ensure_collection(qdrant_client, TEST_DENSE_DIM, name)
    yield name
    if qdrant_client.collection_exists(name):
        qdrant_client.delete_collection(name)


@pytest.fixture
def collection(qdrant_client: QdrantClient, _module_collection: str) -> str:
    """The module's collection, emptied so no test sees another test's points."""
    qdrant_client.delete(
        _module_collection,
        points_selector=models.FilterSelector(filter=models.Filter()),
        wait=True,
    )
    return _module_collection
