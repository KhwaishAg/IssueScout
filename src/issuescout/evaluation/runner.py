"""Run one retrieval configuration over a prepared split (FR-EVL-02 to 04, 13, 14).

For each instance, once its repository's index sits at the instance's base commit, the
issue text is used as the query and the resulting file and function rankings are scored
against the gold labels. The runner measures retrieval quality, search latency and
indexing cost together, so one run is one complete, comparable record.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field

from issuescout.evaluation.commit_sync import SyncStats
from issuescout.evaluation.configs import AblationConfig
from issuescout.evaluation.metrics import evaluate, mean, recall_at_k
from issuescout.evaluation.prepare import PreparedInstance, walk_split
from issuescout.indexing.dense import DenseEncoder, EmbeddingCache
from issuescout.indexing.pipeline import CLI_INDEX_VERSION, repo_id_for_name
from issuescout.retrieval.baseline import rank_files, rank_functions, retrieve_hits
from issuescout.settings import Settings
from issuescout.storage.qdrant import QdrantClient, count_points

SHOWN = 20  # ranked items kept per instance in the record


@dataclass(frozen=True)
class InstanceResult:
    instance_id: str
    repo: str
    gold_files: tuple[str, ...]
    gold_functions: tuple[str, ...]
    ranked_files: tuple[str, ...]  # best first, at most SHOWN
    ranked_functions: tuple[str, ...]
    gold_file_rank: int | None  # 1-based rank of the first gold file; None if not retrieved
    search_ms: float
    index_points: int  # chunks in the repository's index at this commit
    sync_mode: str


@dataclass
class RunResult:
    config: AblationConfig
    split: str
    instances: list[InstanceResult] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    per_repo: dict[str, dict[str, float]] = field(default_factory=dict)
    latency_ms: dict[str, float] = field(default_factory=dict)
    indexing: dict[str, float] = field(default_factory=dict)


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100); simple and exact on small samples."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = max(1, -(-(int(p) * len(ordered)) // 100))  # integer ceil(p/100 * n), at least 1
    return ordered[min(rank, len(ordered)) - 1]


def score(instances: list[InstanceResult], function_level: bool = True) -> dict[str, float]:
    """Headline metrics: file-level Hit@1/5, Recall@10, MRR, nDCG@10, function Recall@10.

    ``function_level=False`` omits function recall: line-window chunking (A0) has no
    function chunks, so the metric is not applicable, and reporting 0.0 would be wrong.
    """
    metrics = evaluate([i.ranked_files for i in instances], [set(i.gold_files) for i in instances])
    with_functions = [i for i in instances if i.gold_functions]
    if function_level and with_functions:
        metrics["function_recall@10"] = mean(
            [recall_at_k(i.ranked_functions, set(i.gold_functions), 10) for i in with_functions]
        )
    metrics["n_instances"] = float(len(instances))
    metrics["n_with_gold_functions"] = float(len(with_functions)) if function_level else 0.0
    return metrics


def run_config(
    prepared: list[PreparedInstance],
    settings: Settings,
    *,
    config: AblationConfig,
    split: str,
    client: QdrantClient,
    encoder: DenseEncoder | None,
    cache: EmbeddingCache | None,
) -> RunResult:
    """Index the split with ``config`` and score every instance."""
    result = RunResult(config=config, split=split)
    syncs: list[SyncStats] = []

    def visit(item: PreparedInstance, stats: SyncStats) -> None:
        inst = item.instance
        repo_id = repo_id_for_name(inst.repo)
        started = time.perf_counter()
        hits = retrieve_hits(
            inst.problem_statement,
            client=client,
            repo_id=repo_id,
            index_version=CLI_INDEX_VERSION,
            mode=config.mode,
            settings=settings,
            encoder=encoder,
            collection=config.collection,
        )
        search_ms = (time.perf_counter() - started) * 1000
        files = [f.path for f in rank_files(hits, settings.max_symbols_per_file)]
        gold = set(inst.gold_files)
        rank = next((i for i, path in enumerate(files, 1) if path in gold), None)
        syncs.append(stats)
        result.instances.append(
            InstanceResult(
                instance_id=inst.instance_id,
                repo=inst.repo,
                gold_files=inst.gold_files,
                gold_functions=inst.gold_functions,
                ranked_files=tuple(files[:SHOWN]),
                ranked_functions=tuple(rank_functions(hits)[:SHOWN]),
                gold_file_rank=rank,
                search_ms=search_ms,
                index_points=count_points(client, repo_id=repo_id, collection=config.collection),
                sync_mode=stats.mode,
            )
        )

    walk_split(
        prepared,
        settings,
        config=config,
        client=client,
        encoder=encoder,
        cache=cache,
        visit=visit,
    )

    function_level = config.chunking == "ast"
    result.metrics = score(result.instances, function_level)
    by_repo: dict[str, list[InstanceResult]] = defaultdict(list)
    for inst_result in result.instances:
        by_repo[inst_result.repo].append(inst_result)
    result.per_repo = {
        repo: score(items, function_level) for repo, items in sorted(by_repo.items())
    }

    times = [i.search_ms for i in result.instances]
    result.latency_ms = {"search_p50": percentile(times, 50), "search_p95": percentile(times, 95)}

    chunks = sum(s.chunks for s in syncs)
    seconds = sum(s.seconds for s in syncs)
    points = [i.index_points for i in result.instances]
    result.indexing = {
        "chunks_indexed": float(chunks),
        "index_seconds": seconds,
        "chunks_per_second": chunks / seconds if seconds else 0.0,
        "full_builds": float(sum(1 for s in syncs if s.mode == "full")),
        "incremental_syncs": float(sum(1 for s in syncs if s.mode == "incremental")),
        "index_points_mean": mean([float(p) for p in points]),
        "index_points_max": float(max(points)),
    }
    return result
