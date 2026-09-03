"""Safe subprocess execution helpers.

Wraps ``subprocess`` with explicit timeouts, working directories, environment
control, and bounded output capture so callers never block indefinitely or
ingest unbounded output.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import ChironError

_MAX_OUTPUT_BYTES = 2_000_000  # ~2 MiB cap on captured output


@dataclass(frozen=True)
class ProcResult:
    """Outcome of a subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str

    def ok(self) -> bool:
        return self.returncode == 0

    def all_output(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()


class CommandError(ChironError):
    """A subprocess was killed (timeout) or could not be started."""

    def __init__(self, message: str, *, returncode: int | None = None, output: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.output = output


def run_command(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 300.0,
    check: bool = False,
) -> ProcResult:
    """Run ``argv`` and return its captured result.

    On timeout or spawn failure raises :class:`CommandError` unless ``check``
    is set, in which case a non-zero exit also raises.
    """
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"Executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(
            f"Command timed out after {timeout_seconds}s: {argv[0]}",
            output=_truncate(exc.stdout or "") + _truncate(exc.stderr or ""),
        ) from exc

    result = ProcResult(
        returncode=proc.returncode,
        stdout=_truncate(proc.stdout or ""),
        stderr=_truncate(proc.stderr or ""),
    )
    if check and not result.ok():
        raise CommandError(
            f"Command failed (rc={result.returncode}): {' '.join(argv)}",
            returncode=result.returncode,
            output=result.all_output(),
        )
    return result


def verify_platform(binary: str) -> None:
    """Confirm a required binary/script path exists and is executable."""
    path = Path(binary).expanduser()
    if not path.exists():
        raise CommandError(f"Required platform tool not found: {binary}")
    if path.is_file() and not os_access_x(path):
        raise CommandError(f"Required platform tool is not executable: {binary}")


def os_access_x(path: Path) -> bool:
    """Return whether ``path`` is executable by the current user."""
    return path.is_file() and (path.stat().st_mode & 0o111) != 0


def make_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a minimal, deterministic environment for subprocesses."""
    base = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    if extra:
        base.update(extra)
    return base


def _truncate(text: str) -> str:
    if len(text.encode("utf-8", errors="replace")) <= _MAX_OUTPUT_BYTES:
        return text
    head = int(_MAX_OUTPUT_BYTES * 0.8)
    tail = int(_MAX_OUTPUT_BYTES * 0.2)
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]