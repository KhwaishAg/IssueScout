"""Prepare a SWE-bench Lite split for evaluation (FR-EVL-01, FR-EVL-02; phase plan P1.6).

* Load the instances and clone each repository once as a bare mirror.
* Order each repository's instances by the commit time of their base commit.
* Verify that every gold file exists at its base commit and derive the gold functions.
* Walk the instances in that order, advancing one index per repository from commit to
  commit, and hand each instance to a callback while the index sits at its base commit.
"""

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from issuescout.evaluation.commit_sync import CommitIndexer, SyncStats
from issuescout.evaluation.configs import AblationConfig
from issuescout.evaluation.gitstore import RepoMirror
from issuescout.evaluation.swebench import EvalInstance, Split, gold_functions, load_instances
from issuescout.indexing.dense import DenseEncoder, EmbeddingCache
from issuescout.indexing.pipeline import CLI_INDEX_VERSION, repo_id_for_name
from issuescout.settings import Settings
from issuescout.storage.qdrant import QdrantClient, path_has_points


@dataclass(frozen=True)
class PreparedInstance:
    instance: EvalInstance  # gold_functions filled in
    order: int  # position among the repository's instances, oldest base commit first
    commit_time: int
    gold_files_present: bool  # every gold file exists at the base commit


def mirror_for(repo: str, settings: Settings) -> RepoMirror:
    """Clone ``owner/name`` as a bare mirror under the state directory (once)."""
    path = settings.state_dir / "swebench" / f"{repo.replace('/', '__')}.git"
    return RepoMirror.ensure(f"https://github.com/{repo}.git", path)


def prepare_split(
    split: Split,
    settings: Settings,
    instances: list[EvalInstance] | None = None,
    mirror_factory: Callable[[str], RepoMirror] | None = None,
) -> list[PreparedInstance]:
    """Order instances per repository and compute and verify their gold labels."""
    instances = instances if instances is not None else load_instances(split)
    factory = mirror_factory or (lambda repo: mirror_for(repo, settings))
    by_repo: dict[str, list[EvalInstance]] = defaultdict(list)
    for inst in instances:
        by_repo[inst.repo].append(inst)

    prepared: list[PreparedInstance] = []
    for repo in sorted(by_repo):
        mirror = factory(repo)
        group = by_repo[repo]
        for inst in group:
            mirror.ensure_commit(inst.base_commit)
        times = mirror.commit_times([i.base_commit for i in group])
        ordered = sorted(group, key=lambda i: (times[i.base_commit], i.instance_id))
        for order, inst in enumerate(ordered):
            sources = {
                path: data
                for path in inst.gold_files
                if (data := mirror.read_file(inst.base_commit, path)) is not None
            }
            funcs = gold_functions(inst.patch, sources, settings)
            prepared.append(
                PreparedInstance(
                    instance=replace(inst, gold_functions=tuple(funcs)),
                    order=order,
                    commit_time=times[inst.base_commit],
                    gold_files_present=len(sources) == len(inst.gold_files) > 0,
                )
            )
    return prepared


def write_instances_json(prepared: list[PreparedInstance], path: Path) -> None:
    """Gold labels and ordering, without problem text, as a reviewable record."""
    rows = [
        {
            "instance_id": p.instance.instance_id,
            "repo": p.instance.repo,
            "base_commit": p.instance.base_commit,
            "created_at": p.instance.created_at,
            "order_in_repo": p.order,
            "gold_files": list(p.instance.gold_files),
            "gold_functions": list(p.instance.gold_functions),
        }
        for p in prepared
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


Visit = Callable[[PreparedInstance, SyncStats], None]


def walk_split(
    prepared: list[PreparedInstance],
    settings: Settings,
    *,
    config: AblationConfig,
    client: QdrantClient,
    encoder: DenseEncoder | None,
    cache: EmbeddingCache | None,
    visit: Visit,
    mirror_factory: Callable[[str], RepoMirror] | None = None,
) -> None:
    """Advance each repository's index through its instances in commit order.

    ``visit`` runs for every instance while the index sits exactly at that instance's
    base commit, which is the only moment queries for it are valid.
    """
    factory = mirror_factory or (lambda repo: mirror_for(repo, settings))
    state_file = settings.state_dir / "eval" / f"{config.collection}.json"
    indexers: dict[str, CommitIndexer] = {}
    for item in sorted(prepared, key=lambda p: (p.instance.repo, p.order)):
        repo = item.instance.repo
        if repo not in indexers:
            indexers[repo] = CommitIndexer(
                factory(repo),
                repo_name=repo,
                settings=settings,
                client=client,
                encoder=encoder if config.needs_dense else None,
                cache=cache if config.needs_dense else None,
                collection=config.collection,
                state_file=state_file,
                ast=config.chunking == "ast",
            )
        stats = indexers[repo].advance(item.instance.base_commit)
        visit(item, stats)


def gold_files_indexed(item: PreparedInstance, client: QdrantClient, collection: str) -> bool:
    """True if every gold file has chunks in the index (used to verify indexing)."""
    repo_id = repo_id_for_name(item.instance.repo)
    return all(
        path_has_points(
            client,
            repo_id=repo_id,
            index_version=CLI_INDEX_VERSION,
            path=path,
            collection=collection,
        )
        for path in item.instance.gold_files
    )
