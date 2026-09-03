"""Tool registry and toolkit builder for the diagnosis agents.

Tools give the LLM a safe, read-only window into a kernel source tree:

* ``search``  - grep the tree for a symbol, string, or pattern
* ``read``    - print a source range as numbered lines, capped at a size limit
* ``git_blame``- show the last commit (+ author, subject) touching a line range
* ``git_log`` - recent commit history for a path
* ``symbol``  - resolve a C identifier (function/macro/struct) to a location

Every tool that touches the filesystem resolves its target through
:func:`_resolve_read_path`, which enforces containment inside the kernel root
so an agent cannot read arbitrary host files (path-traversal guard).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..util.diff import require_git_repo, safe_join
from ..util.subprocess import run_command

__all__ = [
    "Tool",
    "ToolContext",
    "build_toolkit",
    "format_tools_schema",
]

# Lines read by the ``read`` tool per request, and the guard for any single file.
DEFAULT_MAX_LINES = 400
MAX_FILE_BYTES = 1 << 20  # 1 MiB

# git blame --porcelain line format: "<40-hex-sha> <orig-line> <final-line> [num-group]".
_BLAME_LINE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)")


@dataclass(frozen=True)
class Tool:
    """A single callable an LLM may invoke."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any]], str]

    def to_openai_schema(self) -> dict[str, Any]:
        """Return the OpenAI function-calling schema entry for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolContext:
    """Shared state and host capabilities available to the tools."""

    kernel_root: str
    roots: tuple[str, ...] = field(default_factory=tuple)
    max_file_bytes: int = MAX_FILE_BYTES
    max_read_lines: int = DEFAULT_MAX_LINES
    timeout_seconds: float = 60.0

    @property
    def search_root(self) -> str:
        """Highest-priority search root; falls back to the kernel root."""
        return self.roots[0] if self.roots else self.kernel_root


def _capped(stream: bytes) -> str:
    """Decode bytes to UTF-8 text, truncating to a sane console width."""
    text = stream.decode("utf-8", errors="replace")
    if len(text) > 4000:
        return text[:4000] + f"\n... [truncated {len(text)-4000} bytes]"
    return text


def _make_search_tool(ctx: ToolContext) -> Tool:
    def search(params: dict[str, Any]) -> str:
        pattern = str(params["pattern"])
        path = str(params.get("path", ""))
        root = safe_join(ctx.search_root, path)
        # Force ripgrep-style line output; fall back to grep when rg is absent.
        # The "--" separator keeps a pattern that starts with "-" from being
        # parsed as a ripgrep option (argument-injection guard).
        argv = ["rg", "-n", "--no-heading", "--color", "never", "--", pattern, root]
        result = run_command(argv, cwd=ctx.search_root, timeout_seconds=ctx.timeout_seconds)
        if result.returncode == 0:
            return _capped(result.stdout)
        # rg returns 1 when no matches; treat that as an empty result not an error.
        if result.returncode == 1:
            return "No matches."
        return "Error:\n" + _capped(result.stderr)

    return Tool(
        name="search",
        description=(
            "Grep the kernel source tree for a symbol, string literal, or regex pattern. "
            "Returns matching lines with file:line prefixes. Use to locate a function, "
            "struct, or the site of a crash before reading it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to match"},
                "path": {
                    "type": "string",
                    "description": "Optional relative subpath to restrict the search",
                },
            },
            "required": ["pattern"],
        },
        fn=search,
    )


def _make_read_tool(ctx: ToolContext) -> Tool:
    def read(params: dict[str, Any]) -> str:
        rel = str(params["path"])
        start = int(params.get("start", 1))
        length = int(params.get("length", ctx.max_read_lines))
        length = max(1, min(length, 2000))
        target = _resolve_read_path(ctx.kernel_root, rel)
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            if ctx.max_file_bytes and _file_size_le(handle):
                return _Error(f"File exceeds {ctx.max_file_bytes} bytes; refusing to read")
            lines = handle.readlines()
        window = lines[start - 1 : start - 1 + length]
        width = len(str(start - 1 + length))
        numbered = [f"{i+start:>{width}} | {line.rstrip()}" for i, line in enumerate(window)]
        return "\n".join(numbered)

    return Tool(
        name="read",
        description=(
            "Print a range of a kernel source file with line numbers. Provide 'path' "
            "relative to the kernel root, plus optional 'start' (1-based) and 'length'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the kernel root"},
                "start": {"type": "integer", "description": "First line to print (1-based)"},
                "length": {"type": "integer", "description": "Number of lines to print"},
            },
            "required": ["path"],
        },
        fn=read,
    )


def _make_git_blame_tool(ctx: ToolContext) -> Tool:
    def blame(params: dict[str, Any]) -> str:
        rel = str(params["path"])
        start = int(params.get("start", 1))
        end = int(params.get("end", start))
        target = _resolve_read_path(ctx.kernel_root, rel)
        require_git_repo(ctx.kernel_root)
        argv = ["git", "-C", ctx.kernel_root, "blame", "-L", f"{start},{end}", "--porcelain", target]
        result = run_command(argv, cwd=ctx.kernel_root, timeout_seconds=ctx.timeout_seconds)
        if result.returncode != 0:
            return "Error:\n" + _capped(result.stderr)
        return _blame_commit(result.stdout, start)

    return Tool(
        name="git_blame",
        description=(
            "Show the last commit that touched each of the given source lines. Use to "
            "find which upstream change introduced a line under suspicion. Lines count "
            "from 1. Returns '<line>: <short-sha> <author> <subject>' per line."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the kernel root"},
                "start": {"type": "integer", "description": "First line (1-based)"},
                "end": {"type": "integer", "description": "Last line (inclusive)"},
            },
            "required": ["path", "start", "end"],
        },
        fn=blame,
    )


def _make_git_log_tool(ctx: ToolContext) -> Tool:
    def git_log(params: dict[str, Any]) -> str:
        rel = str(params["path"])
        target = _resolve_read_path(ctx.kernel_root, rel)
        require_git_repo(ctx.kernel_root)
        argv = ["git", "-C", ctx.kernel_root, "log", "-n", "20", "--oneline", "--", target]
        result = run_command(argv, cwd=ctx.kernel_root, timeout_seconds=ctx.timeout_seconds)
        if result.returncode != 0:
            return "Error:\n" + _capped(result.stderr)
        return _capped(result.stdout)

    return Tool(
        name="git_log",
        description=(
            "Show the most recent commits that touched the given file or directory. "
            "Use to understand the change history around a fault site."
        ),
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the kernel root"}},
            "required": ["path"],
        },
        fn=git_log,
    )


def _make_symbol_tool(ctx: ToolContext) -> Tool:
    def symbol(params: dict[str, Any]) -> str:
        ident = str(params["name"])
        for path in ctx.roots:
            if path:
                found = _symbol_in_tree(path, ident)
                if found:
                    return found
        if ctx.kernel_root and ctx.kernel_root not in ctx.roots:
            found = _symbol_in_tree(ctx.kernel_root, ident)
            if found:
                return found
        return f"No definition of {ident!r} found in the kernel tree."

    return Tool(
        name="symbol",
        description=(
            "Resolve a C identifier (function, macro, or struct name) to its defining "
            "file and line. Returns the first matching 'file:line' across the tree."
        ),
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "C identifier to locate"}},
            "required": ["name"],
        },
        fn=symbol,
    )


def _symbol_in_tree(root: str, ident: str) -> str:
    """Return a single 'file:line' for the definition of ``ident`` in ``root``."""
    argv = ["rg", "-n", "--no-heading", "--color", "never", rf"\b{re.escape(ident)}\s*\(", root]
    result = run_command(argv, cwd=root, timeout_seconds=60.0)
    if result.returncode != 0:
        return ""
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        if ":" in line:
            return line
    return ""


def _file_size_le(handle: Any) -> bool:
    """Return True if the file already exceeds the byte cap without reading it."""
    try:
        handle.seek(0, 2)  # SEEK_END
        size = handle.tell() if hasattr(handle, "tell") else 0
        return size > MAX_FILE_BYTES
    except OSError:
        return False


def _resolve_read_path(kernel_root: str, rel: str) -> str:
    """Resolve a kernel-relative path, rejecting escapes outside the root."""
    return safe_join(kernel_root, rel)


def _blame_commit(stdout: bytes, first_line: int) -> str:
    """Collapse ``git blame --porcelain`` output into a compact per-line summary."""
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    out: list[str] = []
    sha: str | None = None
    author = ""
    subject = ""
    cur_line = first_line
    for idx, line in enumerate(lines):
        match = _BLAME_LINE.match(line)
        if match:
            if sha is not None:
                out.append(f"{first_line + cur_line - first_line}: {sha[:12]} {author} {subject}")
            sha = match.group(1)
            cur_line = int(match.group(3))
            author = ""
            subject = ""
        elif line.startswith("author "):
            author = line[len("author ") :]
        elif line.startswith("summary "):
            subject = line[len("summary ") :]
        elif line == "" or (idx > 0 and not line.strip()):
            continue
    if sha is not None:
        out.append(f"{first_line + cur_line - first_line}: {sha[:12]} {author} {subject}")
    return "\n".join(out)


def _Error(message: str) -> str:
    """Small namespace helper to return a readable error as tool output."""
    return f"Error: {message}"


def build_toolkit(ctx: ToolContext) -> Sequence[Tool]:
    """Build the full read-only toolkit bound to ``ctx``."""
    return [
        _make_search_tool(ctx),
        _make_read_tool(ctx),
        _make_git_blame_tool(ctx),
        _make_git_log_tool(ctx),
        _make_symbol_tool(ctx),
    ]


def format_tools_schema(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Return the OpenAI tool-calling schema for a list of :class:`Tool`."""
    return [tool.to_openai_schema() for tool in tools]