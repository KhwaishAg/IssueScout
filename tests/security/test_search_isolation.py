"""NFR-SEC-06: the repository filter cannot be bypassed.

All vector searches must go through ``storage.qdrant.search_code``, which always filters
by repository and index version. This test reads the source tree and fails if any other
module talks to Qdrant's query API directly, so a future shortcut shows up in CI instead
of in another customer's comment.
"""

import re
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "issuescout"
ALLOWED = SRC / "storage" / "qdrant.py"

# Any call that reads points without going through search_code.
QUERY_CALL = re.compile(
    r"\b\w*client\w*\.(query_points|query_batch_points|search|search_batch|recommend|"
    r"discover|scroll|retrieve)\s*\(",
    re.IGNORECASE,
)
QDRANT_IMPORT = re.compile(r"^\s*(from|import)\s+qdrant_client\b", re.MULTILINE)


def direct_query_calls(source: str) -> list[str]:
    return [m.group(0) for m in QUERY_CALL.finditer(source)]


def test_scanner_detects_a_bypass() -> None:
    """The check must be able to fail, or it proves nothing."""
    assert direct_query_calls("hits = client.query_points('code_chunks', query=v)")
    assert direct_query_calls("self.qdrant_client.search(collection, vec)")
    assert not direct_query_calls("matches = pattern.search(text)")
    assert not direct_query_calls("hits = search_code(client, repo_id=1, index_version=1)")


def test_only_the_storage_module_queries_qdrant() -> None:
    offenders = {
        str(path.relative_to(SRC)): calls
        for path in SRC.rglob("*.py")
        if path != ALLOWED and (calls := direct_query_calls(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_only_the_storage_package_imports_qdrant_client() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if SRC / "storage" not in path.parents
        and QDRANT_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_search_code_has_no_default_for_the_filter_arguments() -> None:
    import inspect

    from issuescout.storage.qdrant import search_code

    params = inspect.signature(search_code).parameters
    for name in ("repo_id", "index_version"):
        assert params[name].default is inspect.Parameter.empty, name
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
