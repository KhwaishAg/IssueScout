"""Command-line interface. Exit codes follow SRS 8.6: 0 success, 1 runtime failure, 2 usage."""

import json
from typing import Annotated, Literal

import typer

from issuescout.doctor import all_reachable, run_checks
from issuescout.indexing.dense import DenseEncoder, EmbeddingCache, SentenceTransformerEncoder
from issuescout.indexing.pipeline import (
    CLI_INDEX_VERSION,
    IndexStats,
    index_repository,
    repo_id_for_name,
)
from issuescout.indexing.source import SourceError, default_name, is_remote, resolve_source
from issuescout.logging import configure_logging
from issuescout.retrieval.baseline import RankedFile, search_files
from issuescout.settings import Settings, get_settings
from issuescout.storage.qdrant import make_client

app = typer.Typer(help="IssueScout: find the code behind an issue.", no_args_is_help=True)

JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


@app.callback()
def main() -> None:
    """IssueScout command-line tools."""
    configure_logging(get_settings().log_level)


def make_encoder(settings: Settings) -> DenseEncoder:
    """The dense encoder for this run. A function so tests can substitute a fake."""
    return SentenceTransformerEncoder.from_settings(settings)


@app.command()
def doctor(as_json: JsonOption = False) -> None:
    """Check that every service is reachable and report the model cache."""
    results = run_checks(get_settings())
    ok = all_reachable(results)
    if as_json:
        typer.echo(json.dumps({"ok": ok, "checks": [r.as_dict() for r in results]}, indent=2))
    else:
        for r in results:
            typer.echo(f"[{r.status.upper():<4}] {r.name:<12} {r.detail}")
        typer.echo("All services reachable." if ok else "Some services are unreachable.")
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def index(
    source: Annotated[str, typer.Argument(help="Local directory or git URL to index.")],
    name: Annotated[
        str | None, typer.Option(help="Repository name (default: from source).")
    ] = None,
    sparse_only: Annotated[
        bool, typer.Option("--sparse-only", help="Skip embeddings; build a keyword-only index.")
    ] = False,
    chunking: Annotated[
        str, typer.Option(help="'ast' (functions and classes) or 'lines' (fixed windows).")
    ] = "ast",
    as_json: JsonOption = False,
) -> None:
    """Index a repository so it can be queried."""
    if chunking not in ("ast", "lines"):
        raise typer.BadParameter("must be 'ast' or 'lines'", param_hint="--chunking")
    settings = get_settings()
    try:
        root = resolve_source(source, settings)
    except SourceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    repo_name = name or (default_name(source) if is_remote(source) else root.name)
    try:
        encoder = None if sparse_only else make_encoder(settings)
        cache = None if sparse_only else EmbeddingCache(settings.state_dir / "embeddings")
        stats = index_repository(
            root,
            name=repo_name,
            settings=settings,
            client=make_client(settings),
            encoder=encoder,
            cache=cache,
            ast=chunking == "ast",
        )
    except Exception as exc:  # network and model errors: report plainly, exit 1
        typer.echo(f"error: indexing failed: {type(exc).__name__}: {exc}", err=True)
        typer.echo("hint: is the stack running? try `issuescout doctor`", err=True)
        raise typer.Exit(code=1) from exc
    _print_stats(stats, as_json)


def _print_stats(stats: IndexStats, as_json: bool) -> None:
    payload = {
        "name": stats.name,
        "repo_id": stats.repo_id,
        "commit": stats.commit_sha,
        "files": stats.files,
        "chunks": stats.chunks,
        "removed_stale": stats.removed_stale,
        "skipped": stats.skipped,
        "seconds": round(stats.seconds, 2),
        "chunks_per_second": round(stats.chunks_per_second, 1),
        "embedding_cache_hits": stats.embed_cache_hits,
        "embedding_cache_misses": stats.embed_cache_misses,
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return
    skipped = ", ".join(f"{k}={v}" for k, v in sorted(stats.skipped.items())) or "none"
    typer.echo(f"indexed {stats.name} @ {stats.commit_sha[:7]}")
    typer.echo(f"  files {stats.files}, chunks {stats.chunks}, removed stale {stats.removed_stale}")
    typer.echo(f"  skipped: {skipped}")
    typer.echo(f"  {stats.seconds:.1f}s ({stats.chunks_per_second:.1f} chunks/s)")


@app.command()
def query(
    text: Annotated[str, typer.Argument(help="Issue text or question to search for.")],
    repo: Annotated[str, typer.Option(help="Repository name given to `index`.")],
    mode: Annotated[
        str, typer.Option(help="'sparse' (keywords) or 'dense' (embeddings).")
    ] = "sparse",
    top: Annotated[int, typer.Option(min=1, help="Files to show.")] = 10,
    as_json: JsonOption = False,
) -> None:
    """Print the files and functions most likely related to the text."""
    if mode not in ("sparse", "dense"):
        raise typer.BadParameter("must be 'sparse' or 'dense'", param_hint="--mode")
    search_mode: Literal["sparse", "dense"] = "dense" if mode == "dense" else "sparse"
    settings = get_settings()
    try:
        files = search_files(
            text,
            client=make_client(settings),
            repo_id=repo_id_for_name(repo),
            index_version=CLI_INDEX_VERSION,
            mode=search_mode,
            settings=settings,
            encoder=make_encoder(settings) if search_mode == "dense" else None,
        )[:top]
    except Exception as exc:
        typer.echo(f"error: query failed: {type(exc).__name__}: {exc}", err=True)
        typer.echo("hint: has this repository been indexed? try `issuescout index`", err=True)
        raise typer.Exit(code=1) from exc
    _print_files(files, repo, as_json)


def _print_files(files: list[RankedFile], repo: str, as_json: bool) -> None:
    if as_json:
        payload = [
            {
                "rank": i,
                "path": f.path,
                "score": f.score,
                "symbols": [
                    {
                        "symbol": s.symbol,
                        "kind": s.kind,
                        "start_line": s.start_line,
                        "end_line": s.end_line,
                    }
                    for s in f.symbols
                ],
            }
            for i, f in enumerate(files, 1)
        ]
        typer.echo(json.dumps(payload, indent=2))
        return
    if not files:
        typer.echo(f"no results (is {repo!r} indexed? run `issuescout index <path> --name {repo}`)")
        return
    for i, f in enumerate(files, 1):
        typer.echo(f"{i:>2}. {f.path}  (score {f.score:.3f})")
        for s in f.symbols:
            typer.echo(f"      {s.symbol}  L{s.start_line}-{s.end_line}  [{s.kind}]")


if __name__ == "__main__":
    app()
