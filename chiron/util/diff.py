"""Unified-diff parsing, application, and statistics helpers.

Patches produced by the repair agent are unified diffs; this module provides
the small set of operations CHIRON needs: validating a diff, applying it to a
kernel tree, and computing +/- statistics for reporting.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import ToolError
from .subprocess import CommandError, run_command


@dataclass(frozen=True)
class DiffStats:
    """Counts of added/removed/position lines in a patch."""

    added: int = 0
    removed: int = 0

    @property
    def summary(self) -> str:
        return f"+{self.added}/-{self.removed}"


_ADDED_PREFIX = "+"


class DiffError(ToolError):
    """A unified diff could not be parsed, applied, or reverted."""


def validate_diff(diff_text: str) -> None:
    """Raise :class:`DiffError` if ``diff_text`` is not a usable unified diff."""
    if not diff_text.strip():
        raise DiffError("Patch is empty")
    if "diff --git" not in diff_text and "@@ " not in diff_text:
        raise DiffError("Patch does not look like a unified diff (missing diff --git / @@ headers)")


def apply_diff(diff_text: str, *, kernel_dir: str, reverse: bool = False) -> None:
    """Apply ``diff_text`` into ``kernel_dir`` via ``patch``.

    ``reverse`` applies the inverse (used to roll back a candidate after
    validation). Raises :class:`DiffError` on any failure.
    """
    validate_diff(diff_text)
    _assert_patch_contained(diff_text, kernel_dir)
    argv = ["patch", "-p1", "--fuzz=0", "--forward"]
    if reverse:
        argv += ["-R"]
    argv += ["-d", kernel_dir]
    try:
        result = _run_with_stdin(argv, diff_text, cwd=kernel_dir)
    except CommandError as exc:
        raise DiffError(f"Failed to apply patch: {exc.output or exc}") from exc
    if not result.ok:
        raise DiffError(
            f"patch exited rc={result.returncode} applying the diff\n{result.stderr}"
        )


def diff_statistics(diff_text: str) -> DiffStats:
    """Compute additive/removal counts directly from the diff body."""
    added = 0
    removed = 0
    in_hunk = False
    for raw_line in diff_text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("@@ "):
            in_hunk = True
            continue
        if line.startswith("diff --git ") or (in_hunk and line.startswith("+++ ")):
            continue
        if not in_hunk:
            continue
        if line.startswith(_ADDED_PREFIX) and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return DiffStats(added=added, removed=removed)


def _run_with_stdin(argv: list[str], stdin_text: str, *, cwd: str) -> object:
    """Run ``argv`` feeding ``stdin_text`` on stdin with bounded capture."""

    class _Box:
        ok = True
        returncode = 0
        stdout = ""
        stderr = ""

    box = _Box()
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=120.0,
            check=False,
        )
        box.returncode = proc.returncode
        box.ok = proc.returncode == 0
        box.stdout = proc.stdout or ""
        box.stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        box.ok = False
        box.stderr = f"patch timed out: {exc}"
    except FileNotFoundError as exc:
        raise CommandError("patch executable not found") from exc
    return box


def _assert_patch_contained(diff_text: str, kernel_dir: str) -> None:
    """Reject a patch whose target paths escape ``kernel_dir``.

    ``patch -p1`` strips one leading component (e.g. ``b/``) from each target,
    so every ``+++``/``---`` path must still resolve inside the kernel tree.
    This stops a malicious or buggy agent diff from writing outside it. The
    check reuses :func:`safe_join`, so absolute paths and ``..`` escapes both
    raise.
    """
    targets = _diff_target_paths(diff_text)
    for rel in targets:
        try:
            safe_join(kernel_dir, rel)
        except ToolError as exc:
            raise DiffError(f"Patch target escapes kernel tree: {rel!r}") from exc


def _diff_target_paths(diff_text: str) -> list[str]:
    """Extract the ``-p1``-relative target paths a patch would touch."""
    targets: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("+++ ", "--- ")):
            path = line.split(None, 1)[1].strip() if len(line.split(None, 1)) > 1 else ""
            path = _strip_prefix(path)
            if path and not path.startswith("/dev/null"):
                targets.append(path)
    return targets


def _strip_prefix(path: str) -> str:
    """Remove a leading ``a/`` or ``b/`` tree prefix (as ``patch -p1`` does)."""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def require_git_repo(kernel_dir: str) -> None:
    """Raise if ``kernel_dir`` is not a git work tree."""
    try:
        result = run_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=kernel_dir, check=True)
    except CommandError as exc:
        raise ToolError(f"{kernel_dir} is not a git repository: {exc}") from exc
    if result.stdout.strip() != "true":
        raise ToolError(f"{kernel_dir} is not a git repository")


def safe_join(root: str, *parts: str) -> Path:
    """Join path parts and forbid escaping ``root`` (path-traversal guard).

    The joined candidate is resolved (normalizing ``..`` segments and following
    symlinks) *before* the containment check, so ``prefix/../../outside`` is
    detected rather than passing the lexical ``is_relative_to`` comparison.
    """
    candidate = (Path(root) / Path(*parts)).resolve()
    resolved_root = Path(root).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ToolError(f"Path escapes kernel root: {'/'.join(parts)}")
    return candidate