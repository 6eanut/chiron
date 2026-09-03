"""Typed exceptions for CHIRON.

Every failure mode in the framework maps to a distinct exception type so the
orchestrator can classify outcomes without string matching. The validation
fault taxonomy from the paper (style / compilation / crash recurrence /
secondary runtime fault) is represented by :class:`ValidationFault` and its
subclasses.
"""

from __future__ import annotations


class ChironError(Exception):
    """Base class for all CHIRON errors."""


class ConfigError(ChironError):
    """Invalid or missing configuration."""


class LlmError(ChironError):
    """An LLM request failed, was malformed, or exceeded budget."""


class ToolError(ChironError):
    """A tool invocation failed or returned unusable output."""


class KnowledgeError(ChironError):
    """Knowledge-base construction or retrieval failed."""


class ArtifactError(ChironError):
    """A crash artifact could not be parsed or is malformed."""


class EnvironmentError(ChironError):
    """A required platform piece (kernel tree, compiler, QEMU, image) is
    missing or unusable. This is distinct from a patch defect."""


# --------------------------------------------------------------------------- #
# Validation-fault taxonomy (paper §III-D)
# --------------------------------------------------------------------------- #


class ValidationFault(ChironError):
    """A candidate patch failed a validation tier.

    ``category`` is one of the four classes defined in the paper:
    ``"style"``, ``"compile"``, ``"crash_recurrence"``, ``"secondary_fault"``.
    """

    category: str

    def __init__(self, message: str, *, detail: str = "", diagnostics: str = ""):
        super().__init__(message)
        self.detail = detail
        self.diagnostics = diagnostics

    def feedback_text(self) -> str:
        """Render a structured feedback block fed back to the repair agent."""
        return (
            f"[{self.category.upper()}] {self}\n"
            f"{self.detail}\n{self.diagnostics}"
        ).strip()


class StyleFault(ValidationFault):
    """checkpatch.pl reported style or convention violations."""

    category = "style"

    def __init__(self, report: str):
        super().__init__(
            "Patch failed checkpatch style/convention checks.",
            detail=report,
        )


class CompileFault(ValidationFault):
    """The patched kernel failed to cross-compile for RISC-V."""

    category = "compile"

    def __init__(self, diagnostic: str):
        super().__init__(
            "Patched kernel failed target cross-compilation.",
            detail=diagnostic,
        )


class CrashRecurrenceFault(ValidationFault):
    """The patch did not eliminate the original defect in QEMU."""

    category = "crash_recurrence"

    def __init__(self, console: str):
        super().__init__(
            "Original crash still reproduced during QEMU execution.",
            detail=console,
        )


class SecondaryFault(ValidationFault):
    """The patch fixed the original defect but introduced a new fault."""

    category = "secondary_fault"

    def __init__(self, console: str):
        super().__init__(
            "Primary crash neutralized, but a secondary panic/warning appeared.",
            detail=console,
        )