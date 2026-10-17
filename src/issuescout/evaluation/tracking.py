"""Log evaluation runs to MLflow with their full configuration (FR-EVL-08).

A run is only comparable if it records everything that could change its numbers: the
configuration, every tunable setting that affects retrieval, the model revision, and the
exact code version. This module also enforces the rule that the test split is run once
per frozen configuration, so a disappointing test number cannot be "fixed" by rerunning.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import mlflow
from git import InvalidGitRepositoryError, Repo
from mlflow.entities import Run

from issuescout.evaluation.runner import RunResult
from issuescout.settings import Settings

EXPERIMENT = "issuescout-retrieval"


class EvalGuardError(Exception):
    """A run was refused because it would break an evaluation rule."""


def code_version(start: Path | None = None) -> tuple[str, bool]:
    """``(commit sha, working tree has uncommitted changes)`` of the code being run."""
    try:
        repo = Repo(start or Path.cwd(), search_parent_directories=True)
        return str(repo.head.commit.hexsha), bool(repo.is_dirty(untracked_files=False))
    except (InvalidGitRepositoryError, ValueError):
        return "unknown", True


def run_params(result: RunResult, settings: Settings, model_id: str | None) -> dict[str, Any]:
    """Everything that determines the retrieval outcome, as flat MLflow parameters."""
    cfg = result.config
    params: dict[str, Any] = {
        "config": cfg.name,
        "config_description": cfg.description,
        "chunking": cfg.chunking,
        "mode": cfg.mode,
        "split": result.split,
        "collection": cfg.collection,
        "dense_top_k": settings.dense_top_k,
        "sparse_top_k": settings.sparse_top_k,
        "max_symbols_per_file": settings.max_symbols_per_file,
        "chunk_max_tokens": settings.chunk_max_tokens,
        "chunk_overlap_tokens": settings.chunk_overlap_tokens,
        "window_lines": settings.window_lines,
        "window_overlap_lines": settings.window_overlap_lines,
        "sparse_k1": settings.sparse_k1,
        "sparse_b": settings.sparse_b,
        "sparse_avgdl": settings.sparse_avgdl,
        "query": "raw problem statement (no query builder)",
    }
    if cfg.needs_dense:
        params["embedding_model"] = model_id or settings.embedding_model
        params["embedding_max_seq_length"] = settings.embedding_max_seq_length
    return params


def config_hash(params: dict[str, Any]) -> str:
    """Short fingerprint of the parameters, to group runs of the same configuration."""
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def check_test_split_allowed(
    split: str,
    config_name: str,
    freeze_id: str | None,
    dirty: bool,
    existing: Sequence[Run],
) -> None:
    """Rule: tune on dev only; run the test split once per frozen configuration.

    A test run needs a ``freeze_id`` naming the frozen configuration, a clean working tree
    (so the run is tied to a commit), and no earlier test run with the same freeze ID and
    configuration.
    """
    if split != "test":
        return
    if not freeze_id:
        raise EvalGuardError(
            "the test split needs --freeze-id naming the frozen configuration; "
            "tune on the dev split and freeze first"
        )
    if dirty:
        raise EvalGuardError("the test split needs a clean working tree, so the run has a commit")
    for run in existing:
        tags = run.data.tags
        if (
            tags.get("split") == "test"
            and tags.get("freeze_id") == freeze_id
            and tags.get("config") == config_name
            and run.info.status == "FINISHED"
        ):
            raise EvalGuardError(
                f"{config_name} was already run on the test split for freeze {freeze_id!r} "
                f"(run {run.info.run_id}); change the configuration on dev and freeze again"
            )


def _metric_name(name: str) -> str:
    return name.replace("@", "_at_")


def find_existing_runs(tracking_uri: str, experiment: str = EXPERIMENT) -> list[Run]:
    mlflow.set_tracking_uri(tracking_uri)
    found = mlflow.search_experiments(filter_string=f"name = '{experiment}'")
    if not found:
        return []
    runs: list[Run] = mlflow.search_runs(
        experiment_ids=[found[0].experiment_id], output_format="list"
    )
    return runs


def instance_record(i: Any) -> dict[str, Any]:
    return {
        "instance_id": i.instance_id,
        "repo": i.repo,
        "gold_files": list(i.gold_files),
        "gold_functions": list(i.gold_functions),
        "gold_file_rank": i.gold_file_rank,
        "top_files": list(i.ranked_files[:10]),
        "top_functions": list(i.ranked_functions[:10]),
        "search_ms": round(i.search_ms, 1),
        "index_points": i.index_points,
        "sync_mode": i.sync_mode,
    }


def log_run(
    result: RunResult,
    settings: Settings,
    *,
    model_id: str | None,
    freeze_id: str | None = None,
    experiment: str = EXPERIMENT,
) -> str:
    """Write one finished run to MLflow and return its run ID."""
    commit, dirty = code_version()
    params = run_params(result, settings, model_id)
    digest = config_hash(params)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(experiment)
    name = f"{result.config.name}-{result.split}"
    tags = {
        "config": result.config.name,
        "split": result.split,
        "config_hash": digest,
        "code_commit": commit,
        "code_dirty": str(dirty).lower(),
    }
    if freeze_id:
        tags["freeze_id"] = freeze_id
    with mlflow.start_run(run_name=name, tags=tags) as run:
        mlflow.log_params(params)
        # MLflow metric names cannot contain "@": recall@10 is logged as recall_at_10.
        mlflow.log_metrics({_metric_name(k): float(v) for k, v in result.metrics.items()})
        mlflow.log_metrics({f"latency_{k}_ms": v for k, v in result.latency_ms.items()})
        mlflow.log_metrics({f"index_{k}": v for k, v in result.indexing.items()})
        records = [instance_record(i) for i in result.instances]
        mlflow.log_dict({"instances": records}, "per_instance.json")
        misses = [r for r in records if r["gold_file_rank"] is None or r["gold_file_rank"] > 10]
        mlflow.log_dict(
            {"misses": misses}, "misses.json"
        )  # FR-EVL-14: every miss with the rank at which the gold file appeared
        mlflow.log_dict(result.per_repo, "per_repo.json")
        return str(run.info.run_id)
