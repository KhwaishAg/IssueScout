"""Command-line interface. Exit codes follow SRS 8.6: 0 success, 1 runtime failure."""

import json
from typing import Annotated

import typer

from issuescout.doctor import all_reachable, run_checks
from issuescout.settings import get_settings

app = typer.Typer(help="IssueScout: find the code behind an issue.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """IssueScout command-line tools."""


@app.command()
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
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
