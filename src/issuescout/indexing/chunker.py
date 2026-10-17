"""Chunking (SRS 4.3 steps 3-5; FR-IDX-04, 07, 08, 09, 10).

Python files are parsed with tree-sitter into function, method, class-header and module
chunks. Everything else, and any Python file that fails to parse, becomes overlapping
line windows. Line numbers are 1-based and inclusive, matching GitHub permalinks.

Oversized chunks are split on a 400-token window with 60 tokens of overlap, then snapped
outward to whole lines, so a window can exceed 400 tokens by up to one partial line at each
end. Whole lines keep line ranges exact and the text readable.
"""

import time
from pathlib import PurePosixPath

import structlog
import tree_sitter_python
from tree_sitter import Language, Node, Parser, Tree

from issuescout.indexing.filters import detect_language, is_doc, is_test_path
from issuescout.indexing.models import Chunk, ChunkKind
from issuescout.indexing.tokens import TokenCounter, count_tokens, token_line_indexes
from issuescout.settings import Settings

log = structlog.get_logger()

PARSE_TIMEOUT_SECONDS = 2.0  # NFR-SEC-12
_PARSER = Parser(Language(tree_sitter_python.language()))


def module_name(path: str) -> str:
    """``pkg/mod.py`` -> ``pkg.mod``; ``pkg/__init__.py`` -> ``pkg``."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _split_lines(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def chunk_file(
    path: str,
    source: bytes,
    settings: Settings,
    count: TokenCounter = count_tokens,
) -> list[Chunk]:
    """Turn one file into chunks. Never raises on bad input."""
    text = source.decode("utf-8", errors="replace").replace("\r\n", "\n")
    lines = _split_lines(text)
    language = detect_language(path)
    if language == "python":
        tree_chunks = _chunk_python(path, text, lines, settings, count)
        if tree_chunks is not None:
            return tree_chunks
    return line_windows(path, lines, language, settings, count)


def line_windows(
    path: str,
    lines: list[str],
    language: str,
    settings: Settings,
    count: TokenCounter = count_tokens,
) -> list[Chunk]:
    """FR-IDX-09: windows of ``window_lines`` lines overlapping by ``window_overlap_lines``."""
    kind: ChunkKind = "doc" if is_doc(path) else "window"
    size, step = settings.window_lines, settings.window_lines - settings.window_overlap_lines
    chunks: list[Chunk] = []
    start = 0
    while start < len(lines):
        end = min(start + size, len(lines))
        body = "\n".join(lines[start:end])
        if body.strip():
            chunks.append(
                Chunk(
                    path=path,
                    language=language,
                    kind=kind,
                    symbol=path,
                    parent_symbol=None,
                    signature="",
                    start_line=start + 1,
                    end_line=end,
                    text=body,
                    token_count=count(body),
                    is_test=is_test_path(path),
                )
            )
        if end == len(lines):
            break
        start += step
    return chunks


def _parse(text: str) -> Tree | None:
    """Parse, returning None if it took longer than ``PARSE_TIMEOUT_SECONDS``.

    The limit is checked after the parse, not enforced during it: py-tree-sitter's
    ``progress_callback`` crashes the interpreter on Windows (access violation), so a hard
    cancel is not available here. Input size is already capped at ``max_file_bytes`` by the
    file filters; P3.5 adds a process-level time limit in the worker.
    """
    started = time.monotonic()
    tree = _PARSER.parse(text.encode("utf-8"))
    if time.monotonic() - started > PARSE_TIMEOUT_SECONDS:
        return None
    return tree


def _chunk_python(
    path: str,
    text: str,
    lines: list[str],
    settings: Settings,
    count: TokenCounter,
) -> list[Chunk] | None:
    """AST chunks, or None when the file must fall back to line windows (FR-IDX-10)."""
    # Keep ``tree`` referenced until the walk ends: nodes do not keep it alive, and using
    # a node after its tree is collected is an access violation, not a Python error.
    tree = _parse(text)
    if tree is None:
        log.warning("parse_fallback", path=path, reason="timeout")
        return None
    root = tree.root_node
    if root.has_error:
        log.warning("parse_fallback", path=path, reason="syntax_error")
        return None

    builder = _Builder(path, text, lines, settings, count)
    builder.walk_module(root)
    del tree
    return builder.chunks


class _Builder:
    def __init__(
        self, path: str, text: str, lines: list[str], settings: Settings, count: TokenCounter
    ) -> None:
        self.path = path
        self.text = text
        self.lines = lines
        self.settings = settings
        self.count = count
        self.module = module_name(path)
        self.is_test = is_test_path(path)
        self.chunks: list[Chunk] = []

    # -- walking ---------------------------------------------------------------------

    def walk_module(self, root: Node) -> None:
        leftovers: list[Node] = []
        scope = [self.module] if self.module else []
        for child in root.named_children:
            if not self._definition(child, scope, self.module or None, False):
                leftovers.append(child)
        self._module_chunk(leftovers)

    def _definition(self, node: Node, scope: list[str], parent: str | None, in_class: bool) -> bool:
        """Emit chunks if ``node`` is a function or class. Return False for anything else."""
        start_row = node.start_point[0]  # includes decorators
        definition = node
        if node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is None:
                return False
            definition = inner
        name_node = definition.child_by_field_name("name")
        if name_node is None or name_node.text is None:
            return False
        name = name_node.text.decode("utf-8", errors="replace")
        symbol = ".".join([*scope, name])
        end_row = self._end_row(definition)
        signature = self._signature(definition)

        if definition.type == "function_definition":
            kind: ChunkKind = "method" if in_class else "function"
            self._emit(kind, symbol, parent, signature, list(range(start_row, end_row + 1)))
            return True
        if definition.type == "class_definition":
            self._class(definition, symbol, parent, signature, start_row)
            return True
        return False

    def _class(
        self, node: Node, symbol: str, parent: str | None, signature: str, start_row: int
    ) -> None:
        body = node.child_by_field_name("body")
        methods: list[str] = []
        header_end = node.start_point[0] + signature.count("\n")
        children: list[Node] = []
        if body is not None:
            children = list(body.named_children)
            docstring = self._docstring_rows(children)
            if docstring is not None:
                header_end = docstring[1]
        scope = symbol.split(".")
        for child in children:
            target = (
                child.child_by_field_name("definition")
                if child.type == "decorated_definition"
                else child
            )
            if target is not None and target.type == "function_definition":
                n = target.child_by_field_name("name")
                if n is not None and n.text is not None:
                    methods.append(n.text.decode("utf-8", errors="replace"))
        header = "\n".join(self.lines[start_row : header_end + 1])
        if methods:
            header += "\n# methods: " + ", ".join(methods)
        self.chunks.append(
            Chunk(
                path=self.path,
                language="python",
                kind="class",
                symbol=symbol,
                parent_symbol=parent,
                signature=signature,
                start_line=start_row + 1,
                end_line=header_end + 1,
                text=header,
                token_count=self.count(header),
                is_test=self.is_test,
            )
        )
        for child in children:
            self._definition(child, scope, symbol, True)

    # -- helpers ---------------------------------------------------------------------

    @staticmethod
    def _end_row(node: Node) -> int:
        # Points are read by index, never `.row` / `.column`: on tree-sitter 0.26 the
        # attribute getters return mis-counted references, so large rows (> 256) come back
        # as wrong values or crash the interpreter. Indexing is stable.
        row, col = node.end_point[0], node.end_point[1]
        return row - 1 if col == 0 and row > node.start_point[0] else row

    def _signature(self, definition: Node) -> str:
        body = definition.child_by_field_name("body")
        stop = body.start_byte if body is not None else definition.end_byte
        raw = self.text.encode("utf-8")[definition.start_byte : stop]
        return raw.decode("utf-8", errors="replace").strip()

    @staticmethod
    def _docstring_rows(children: list[Node]) -> tuple[int, int] | None:
        if children and children[0].type == "expression_statement":
            inner = children[0].named_children
            if inner and inner[0].type == "string":
                return children[0].start_point[0], _Builder._end_row(children[0])
        return None

    def _module_chunk(self, leftovers: list[Node]) -> None:
        rows: list[int] = []
        for node in leftovers:
            rows.extend(range(node.start_point[0], self._end_row(node) + 1))
        if not "\n".join(self.lines[r] for r in rows).strip():
            return
        name = self.module or PurePosixPath(self.path).stem
        self._emit("module", name, None, "", rows)

    def _emit(
        self,
        kind: ChunkKind,
        symbol: str,
        parent: str | None,
        signature: str,
        rows: list[int],
    ) -> None:
        """Add a chunk over source ``rows``, splitting it into token windows if too long.

        ``rows`` need not be contiguous (the module chunk skips definitions), so line
        numbers always come from ``rows``, never from a position in the text.
        """
        body_lines = [self.lines[r] for r in rows]
        body = "\n".join(body_lines)
        if self.count(body) <= self.settings.chunk_max_tokens:
            self.chunks.append(
                self._chunk(kind, symbol, parent, signature, rows[0], rows[-1], body)
            )
            return

        owners = token_line_indexes(body_lines)
        size = self.settings.chunk_max_tokens
        step = size - self.settings.chunk_overlap_tokens
        spans: list[tuple[int, int]] = []
        for s in range(0, len(owners), step):
            e = min(s + size, len(owners))
            spans.append((owners[s], owners[e - 1]))
            if e == len(owners):
                break
        for part, (first, last) in enumerate(spans):
            window = "\n".join(body_lines[first : last + 1])
            if signature and first > 0:
                window = f"{signature}\n{window}"  # FR-IDX-08: every window carries the signature
            self.chunks.append(
                self._chunk(
                    kind,
                    symbol,
                    parent,
                    signature,
                    rows[first],
                    rows[last],
                    window,
                    part=part,
                    parts=len(spans),
                )
            )

    def _chunk(
        self,
        kind: ChunkKind,
        symbol: str,
        parent: str | None,
        signature: str,
        start_row: int,
        end_row: int,
        text: str,
        part: int | None = None,
        parts: int | None = None,
    ) -> Chunk:
        return Chunk(
            path=self.path,
            language="python",
            kind=kind,
            symbol=symbol,
            parent_symbol=parent,
            signature=signature,
            start_line=start_row + 1,
            end_line=end_row + 1,
            text=text,
            token_count=self.count(text),
            is_test=self.is_test,
            part=part,
            parts=parts,
        )
