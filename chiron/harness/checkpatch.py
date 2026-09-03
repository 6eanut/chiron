"""checkpatch.pl style-validation tier.

Runs the kernel's ``scripts/checkpatch.pl`` against a candidate unified diff
and maps style violations to a :class:`StyleFault` so the repair agent can
address them on the next iteration.

An absent checkpatch script degrades the tier gracefully (logged, no hard
failure): style checking is only as good as the platform piece being present.
A genuinely broken script call (spawn failure, timeout, or a non-clean exit
with no report) is surfaced as a hard style-tier failure.
"""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from ..errors import StyleFault
from ..logging_utils import get_logger
from ..util.subprocess import CommandError, make_env, run_command, verify_platform

log = get_logger(__name__)

_CHECKPATCH_TIMEOUT = 120.0
_REPORT_CHARS = 16_000  # cap the style report fed back to the agent


def run_checkpatch(diff_text: str, *, config: AppConfig) -> None:
    """Validate ``diff_text`` against checkpatch.pl.

    Raises :class:`StyleFault` on style problems. Returns normally on a clean
    report or when the script is absent (graceful degradation).
    """
    script = config.checkpatch_script()
    try:
        verify_platform(script)
    except CommandError as exc:
        log.warning("checkpatch unavailable (%s); skipping style tier", exc)
        return

    diff_path = _stage_diff(diff_text, config)
    try:
        result = run_command(
            [script, "--no-tree", str(diff_path)],
            env=make_env(),
            timeout_seconds=_CHECKPATCH_TIMEOUT,
            check=False,
        )
    except CommandError as exc:
        # A real tool failure (broken script / timeout) is a style-tier fault.
        raise StyleFault(exc.output or str(exc)) from exc

    report = _clean_report(result.stdout + "\n" + result.stderr)
    if result.ok():
        log.info("checkpatch: clean report")
        return

    log.warning("checkpatch: style violations detected")
    if report:
        raise StyleFault(report)
    raise StyleFault("checkpatch.pl exited non-zero but produced no report text")


def _stage_diff(diff_text: str, config: AppConfig) -> Path:
    """Persist the diff to a temp file for checkpatch to read as its argument."""
    log_dir = Path(config.log_dir())
    log_dir.mkdir(parents=True, exist_ok=True)
    diff_path = log_dir / "candidate.patch"
    diff_path.write_text(diff_text, encoding="utf-8")
    return diff_path


def _clean_report(raw: str) -> str:
    """Strip shell noise, preserving every emitted checkpatch line."""
    valid_prefixes = (
        "WARNING:",
        "ERROR:",
        "CHECK:",
        "NOTE:",
        "FILE:",
        "total:",
        "ERROR",
    )
    kept = "\n".join(
        line for line in raw.splitlines() if line.strip().startswith(valid_prefixes)
    )
    return kept.strip()[:_REPORT_CHARS]


__all__ = ["run_checkpatch"]