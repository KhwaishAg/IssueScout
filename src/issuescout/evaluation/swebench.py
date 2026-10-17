"""SWE-bench Lite instances and their gold labels (SRS 10.1, 10.2; FR-EVL-01).

An instance is a real GitHub issue (the query) plus the commit it was filed against and
the patch that fixed it. The gold file is the file the reference patch edits. The gold
functions are the functions containing the patch's changed lines at the base commit.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from issuescout.indexing.chunker import chunk_file
from issuescout.settings import Settings

Split = Literal["dev", "test"]
DATASET = "princeton-nlp/SWE-bench_Lite"

_FILE_HEADER = re.compile(r"^--- (?:a/(?P<old>.+)|/dev/null)\n\+\+\+ (?:b/(?P<new>.+)|/dev/null)$")
_HUNK_HEADER = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")


@dataclass(frozen=True)
class EvalInstance:
    instance_id: str
    repo: str  # "owner/name"
    base_commit: str
    problem_statement: str
    created_at: str
    gold_files: tuple[str, ...]
    split: Split
    gold_functions: tuple[str, ...] = field(default=())  # "path::qualified.symbol"
    patch: str = field(default="", repr=False)  # reference fix; used only to derive gold labels


def load_instances(split: Split) -> list[EvalInstance]:
    """Load one SWE-bench Lite split from Hugging Face (cached locally after the first call)."""
    from datasets import load_dataset  # type: ignore[import-untyped]  # heavy; only needed here

    rows = load_dataset(DATASET, split=split)
    return [
        EvalInstance(
            instance_id=r["instance_id"],
            repo=r["repo"],
            base_commit=r["base_commit"],
            problem_statement=r["problem_statement"],
            created_at=r["created_at"],
            gold_files=tuple(gold_files_from_patch(r["patch"])),
            split=split,
            patch=r["patch"],
        )
        for r in rows
    ]


def _is_file_header(lines: list[str], i: int) -> bool:
    """``---`` / ``+++`` / ``@@`` in a row. A removed line that reads ``-- text`` (a SQL or
    Lua comment) also starts with ``--- `` once the diff adds its marker, so the header
    must be confirmed by the lines after it."""
    return (
        i + 2 < len(lines)
        and lines[i].startswith("--- ")
        and lines[i + 1].startswith("+++ ")
        and lines[i + 2].startswith("@@")
    )


def patch_files(patch: str) -> list[tuple[str | None, str | None]]:
    """``(old_path, new_path)`` for every file in a unified diff; None means /dev/null."""
    pairs: list[tuple[str | None, str | None]] = []
    lines = patch.split("\n")
    for i in range(len(lines) - 1):
        if lines[i].startswith("--- ") and lines[i + 1].startswith("+++ "):
            match = _FILE_HEADER.match(lines[i] + "\n" + lines[i + 1])
            if match:
                pairs.append((match.group("old"), match.group("new")))
    return pairs


def gold_files_from_patch(patch: str) -> list[str]:
    """Files the patch edits, as they exist at the base commit.

    Deleted files count; newly added files do not exist at the base commit, so they cannot
    be retrieved and are left out.
    """
    return [old for old, _new in patch_files(patch) if old is not None]


def changed_old_lines(patch: str) -> dict[str, set[int]]:
    """1-based line numbers, in each file at the base commit, that the patch changes.

    A removed or replaced line is changed. A pure insertion has no old line of its own, so
    it is attributed to the old line just before it (the code it was added after); at the
    very top of a file that is line 1. Context lines are not changes.
    """
    result: dict[str, set[int]] = {}
    current: set[int] | None = None
    old_line = 0
    lines = patch.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_file_header(lines, i):
            match = _FILE_HEADER.match(line + "\n" + lines[i + 1])
            current = None
            if match and match.group("old") is not None:
                current = result.setdefault(match.group("old"), set())
            i += 2
            continue
        hunk = _HUNK_HEADER.match(line)
        if hunk:
            old_line = int(hunk.group("old"))
        elif current is not None and old_line:
            if line.startswith("-"):
                current.add(old_line)
                old_line += 1
            elif line.startswith("+"):
                current.add(max(old_line - 1, 1))
            elif line.startswith(" ") or line == "":
                # Context. An empty line is a blank context line whose leading space was
                # stripped on the way (editors and mail clients do this to patches).
                old_line += 1
        i += 1
    return result


def gold_functions(
    patch: str,
    base_sources: dict[str, bytes],
    settings: Settings,
) -> list[str]:
    """``path::symbol`` of every function or method that contains a changed line.

    ``base_sources`` maps each gold path to its content at the base commit. The same
    chunker that builds the index defines the functions, so a gold name is exactly a name
    the retriever can return. A change outside any function (a module constant, a class
    attribute) has no gold function; such instances are scored on files only.
    """
    changed = changed_old_lines(patch)
    found: list[str] = []
    for path, lines in changed.items():
        source = base_sources.get(path)
        if source is None:
            continue
        for chunk in chunk_file(path, source, settings):
            if chunk.kind not in ("function", "method"):
                continue
            if any(chunk.start_line <= n <= chunk.end_line for n in lines):
                item = f"{path}::{chunk.symbol}"
                if item not in found:
                    found.append(item)
    return found
