"""P2.1: the retrieval-mode switch and the ablation configurations."""

import pytest

from issuescout.evaluation.configs import CONFIGS
from issuescout.retrieval.baseline import IMPLEMENTED_MODES, retrieve_hits
from issuescout.settings import Settings


def test_a3_reuses_the_index_of_a2() -> None:
    assert CONFIGS["A3"].collection == CONFIGS["A2"].collection
    assert CONFIGS["A3"].mode == "hybrid" and CONFIGS["A3"].needs_dense
    assert CONFIGS["A3"].chunking == CONFIGS["A2"].chunking == "ast"


def test_other_configurations_keep_separate_indexes() -> None:
    collections = {CONFIGS[n].collection for n in ("A0", "A1", "A2")}
    assert len(collections) == 3


def test_three_modes_exist_and_the_later_two_are_refused_not_faked() -> None:
    assert IMPLEMENTED_MODES == ("sparse", "dense", "hybrid")
    for mode in ("hybrid_rerank", "full"):
        with pytest.raises(NotImplementedError, match="not built yet"):
            retrieve_hits(
                "q",
                client=None,  # type: ignore[arg-type]
                repo_id=1,
                index_version=1,
                mode=mode,
                settings=Settings(_env_file=None),
            )


def test_dense_and_hybrid_need_an_encoder() -> None:
    for mode in ("dense", "hybrid"):
        with pytest.raises(ValueError, match="needs an encoder"):
            retrieve_hits(
                "q",
                client=None,  # type: ignore[arg-type]
                repo_id=1,
                index_version=1,
                mode=mode,
                settings=Settings(_env_file=None),
            )
