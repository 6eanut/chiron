"""Core data models shared across CHIRON's stages.

The :class:`CrashArtifact` is the input CHIRON ingests: a fuzzer-discovered
kernel crash described by the kaller/riscvkaller schema (title, description,
one or more log/report dumps, and machine info). The :class:`Diagnosis` is the
structured output of the multi-view diagnosis stage, and :class:`RepairResult`
covers the repair + validation outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CrashArtifact:
    """A fuzzing crash to diagnose and repair."""

    description: str = ""
    title: str = ""
    repository: str = ""
    commit: str = ""
    machine_info: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "CrashArtifact":
        """Build an artifact from a kaller-style dict, tolerating missing keys."""
        logs = _as_string_list(raw.get("log"))
        reports = _as_string_list(raw.get("report"))
        # A bare list of strings as the top-level value is treated as logs.
        if not logs and not reports:
            logs = _as_string_list(raw.get("logs"))
        return cls(
            description=raw.get("description", "") or "",
            title=raw.get("title", "") or "",
            repository=raw.get("repository", "") or "",
            commit=raw.get("commit", "") or "",
            machine_info=dict(raw.get("machineInfo") or raw.get("machine_info") or {}),
            logs=logs,
            reports=reports,
        )

    def signature_text(self) -> str:
        """Concatenate the observable signal used for retrieval and matching."""
        parts = [self.title, self.description, *self.logs, *self.reports]
        return "\n".join(p for p in parts if p)


@dataclass
class ViewEvidence:
    """Evidence collected by a single diagnosis view."""

    view: str                 # "focused_code" | "trigger" | "subsystem" | "synthesis"
    text: str = ""
    suspect_file: str = ""
    suspect_lines: tuple[int, int] = (0, 0)
    confidence: float = 0.0


@dataclass
class Diagnosis:
    """Structured output of the multi-view diagnosis stage."""

    signature_id: str = "unknown"
    subject: str = ""
    root_cause: str = ""
    evidence: list[ViewEvidence] = field(default_factory=list)
    suspect_file: str = ""
    suspect_lines: tuple[int, int] = (0, 0)
    verdict: str = "pending"    # style|compile|crash_recurrence|secondary_runtime_fault|clean

    def add_evidence(self, view: ViewEvidence) -> None:
        self.evidence.append(view)


@dataclass
class RepairResult:
    """The outcome of a single repair attempt."""

    diff: str = ""
    patch_applied: bool = False
    fault_category: str = "unknown"     # style|compile|crash_recurrence|secondary_runtime_fault|clean
    message: str = ""
    test_output: str = ""
    iteration: int = 0


def _as_string_list(value: Any) -> list[str]:
    """Normalize a ``log``/``report`` field to a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item)
            elif isinstance(item, bytes):
                text = item.decode("utf-8", errors="replace")
                if text.strip():
                    out.append(text)
        return out
    return []


__all__ = ["CrashArtifact", "Diagnosis", "RepairResult", "ViewEvidence", "_as_string_list"]