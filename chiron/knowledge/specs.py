"""Domain-knowledge schema for CHIRON's knowledge base.

The knowledge base encodes the *domain knowledge* pillar of the paper: a
curated catalog of *known* RISC-V KVM fault families, their observable crash
signatures, and the canonical shape of an upstream-class fix for each. These
entries generalise well-documented fault families that predate this work and
are compiled from public kernel-lore discussion and upstream commit patterns.

Provenance and scope. This release ships the *schema* for the domain
knowledge base but an **empty** signature catalog. ``KNOWN_SIGNATURES`` is
deliberately empty: the paper's evaluated defects are held out of this release
by design, and no curated ``BugSignature`` entry is shipped, so no entry in
this repository can ever be a memorised answer to an evaluated case. The
dataclasses (:class:`BugSignature`, :class:`FixPattern`, :class:`Subsystem`)
and ``SUBSYSTEM_CATALOG`` are reference material that documents the *shape* of
the knowledge base for reproducibility; operators populate their own signature
catalog (from upstream lore and commit patterns) outside this repository.

``match_signature`` is a substring matcher over an operator-supplied catalog;
with the default empty ``KNOWN_SIGNATURES`` it matches nothing unless a
non-empty catalog is passed in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "BugSignature",
    "FixPattern",
    "Subsystem",
    "SUBSYSTEM_CATALOG",
    "KNOWN_SIGNATURES",
    "match_signature",
]


@dataclass(frozen=True)
class FixPattern:
    """A reusable shape for the fix that resolves a signature's fault class."""

    name: str
    description: str = ""
    # How changes must be applied (e.g. "precede the TLB flush with a map-sync").
    guidance: str = ""


@dataclass(frozen=True)
class BugSignature:
    """A known fault family with its observable crash signature."""

    id: str                        # e.g. an operator-assigned family id
    subsystem: str                 # e.g. "arch/riscv/kvm"
    title_patterns: tuple[str, ...]  # substrings matched against crash titles/logs
    fault_class: str = "crash_recurrence"  # style|compile|crash_recurrence|secondary_runtime_fault
    hint: str = ""                 # one-line pointer for the repair agent
    fix: FixPattern = field(default_factory=FixPattern)


# Canonical fault classes; kept in sync with errors.ValidationFault categories.
FAULT_CLASSES = ("style", "compile", "crash_recurrence", "secondary_runtime_fault")


@dataclass(frozen=True)
class Subsystem:
    """A CHIRON-managed kernel subsystem scope."""

    name: str
    root: str               # path under kernel_root
    focus_files: tuple[str, ...] = ()


SUBSYSTEM_CATALOG: tuple[Subsystem, ...] = (
    Subsystem(name="riscv-kvm", root="arch/riscv/kvm",
              focus_files=("vm.c", "mmu.c", "vmid.c", "vcpu.c", "vmcontext.c")),
    Subsystem(name="riscv-sbi", root="arch/riscv/kvm/aia",
              focus_files=("aia.c", "sbi.c")),
    Subsystem(name="riscv-tlb", root="arch/riscv/kvm",
              focus_files=("mmu.c", "tlb.c")),
    Subsystem(name="generic-kvm", root="virt/kvm",
              focus_files=()),
    Subsystem(name="core-mm", root="mm",
              focus_files=()),
)


# Intentionally empty. See the module docstring "Provenance and scope": the
# released companion ships the schema but holds the evaluated defect catalog
# out of the repository entirely. Operators supply their own signatures.
KNOWN_SIGNATURES: tuple[BugSignature, ...] = ()


def match_signature(
    text: str, signatures: tuple[BugSignature, ...] = KNOWN_SIGNATURES
) -> BugSignature | None:
    """Return the first signature whose title pattern appears in ``text``."""
    lowered = text.lower()
    for sig in signatures:
        for pattern in sig.title_patterns:
            if re.search(re.escape(pattern.lower()), lowered):
                return sig