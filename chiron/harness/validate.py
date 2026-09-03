"""Tiered patch validation (paper §III-D).

Validates a candidate repair diff in a fixed order:

1. **style**    - checkpatch.pl conventions;
2. **compile**  - RISC-V cross-compilation;
3. **runtime**  - QEMU boot, classifying crash recurrence vs. a secondary fault.

Each tier is soft-when-unavailable but the *ordering* is fixed: style problems
are resolved before compiling, and a compile failure short-circuits the boot.
Failures map onto the :class:`ValidationFault` taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import AppConfig
from ..core.models import CrashArtifact
from ..errors import (
    CompileFault,
    CrashRecurrenceFault,
    SecondaryFault,
    StyleFault,
    ValidationFault,
)
from ..logging_utils import get_logger
from ..util.diff import apply_diff
from .checkpatch import run_checkpatch
from .cross_compile import cross_compile_kernel
from .qemu_runner import boot_present, run_reproducer

log = get_logger(__name__)

# Kernel marker patterns that indicate a non-clean runtime outcome.
_SECONDARY_MARKERS = (
    "Kernel panic",
    "BUG:",
    "Oops:",
    "general protection fault",
    "Unable to handle kernel",
    "WARNING: CPU",
    "dump_stack",
)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a full validation run."""

    passed: bool
    fault: ValidationFault | None = None
    tiers_run: tuple[str, ...] = ()
    console_transcript: str = ""

    def message(self) -> str:
        if self.passed:
            return "all validation tiers passed"
        if self.fault is not None:
            return self.fault.feedback_text()
        return "validation failed (no fault details)"


def validate_patch(
    diff_text: str,
    *,
    config: AppConfig,
    artifact: CrashArtifact | None = None,
    reproducer_c: str | None = None,
    apply: bool = False,
) -> ValidationResult:
    """Validate ``diff_text`` through the tiered harness.

    ``apply`` optionally applies the diff to the kernel tree first so the
    compile/runtime tiers exercise the patched tree. Returns a
    :class:`ValidationResult`; only the documented optional apply mutates state.
    """
    if apply:
        apply_diff(diff_text, kernel_dir=config.paths.kernel_dir)

    tiers: list[str] = []

    # Tier 1: style
    tiers.append("style")
    try:
        run_checkpatch(diff_text, config=config)
    except StyleFault as exc:
        return _fail(exc, tiers)

    # Tier 2: compile
    tiers.append("compile")
    try:
        cross_compile_kernel(config=config)
    except CompileFault as exc:
        return _fail(exc, tiers)

    # Tier 3: runtime
    tiers.append("runtime")
    if boot_present(config):
        transcript = run_reproducer(artifact, reproducer_c or "", config=config)
        if artifact is not None and _mentions_original_bug(transcript, artifact):
            return _fail(CrashRecurrenceFault(transcript), tiers, transcript)
        if _shows_timeout_or_secondary(transcript, artifact):
            return _fail(SecondaryFault(transcript), tiers, transcript)
    else:
        log.info("runtime tier unavailable; passed (soft)")
        transcript = ""

    log.info("validation passed (tiers=%s)", ",".join(tiers))
    return ValidationResult(passed=True, tiers_run=tuple(tiers), console_transcript=transcript)


def _fail(fault: ValidationFault, tiers: list[str], transcript: str = "") -> ValidationResult:
    log.info("validation failed on tier '%s': %s", fault.category, fault)
    return ValidationResult(
        passed=False,
        fault=fault,
        tiers_run=tuple(tiers),
        console_transcript=transcript,
    )


def _mentions_original_bug(transcript: str, artifact: CrashArtifact) -> bool:
    """True if the transcript still shows a marker attributable to the bug."""
    markers = _original_markers(artifact)
    if not markers:
        return False
    return any(marker in transcript for marker in markers)


def _shows_timeout_or_secondary(transcript: str, artifact: CrashArtifact | None) -> bool:
    """True if the transcript shows a *different* panic/error than the original."""
    if not transcript.strip():
        # Empty/hung boot with the tier offered is a runtime anomaly.
        return True
    if artifact is not None and _mentions_original_bug(transcript, artifact):
        return False
    return any(marker in transcript for marker in _SECONDARY_MARKERS)


def _original_markers(artifact: CrashArtifact) -> tuple[str, ...]:
    """Contract the artifact's signal into short, matchable strings."""
    seen: list[str] = []
    title = (artifact.title or "").strip()
    if len(title) >= 8:
        seen.append(title)
    for block in (artifact.logs or []) + (artifact.reports or []):
        for line in str(block).splitlines():
            line = line.strip()
            # Keep only substantive, distinctive lines as match fingerprints.
            if 8 <= len(line) <= 160 and line not in seen:
                seen.append(line)
        if len(seen) >= 8:
            break
    return tuple(seen)


__all__ = ["validate_patch", "ValidationResult"]