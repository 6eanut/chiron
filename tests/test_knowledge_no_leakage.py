"""No-leakage guard for the shipped companion knowledge base.

The paper reports five first-discovered zero-day RISC-V KVM defects and argues
the 5/5 results come from CHIRON's domain-grounded reasoning rather than from a
knowledge base that already encodes the answers. To keep that argument
auditable, the *shipped* ``KNOWN_SIGNATURES`` and the diagnosis prompts must
never carry identifiers or crash shapes tied to those evaluated defects.

This test encodes that invariant: if a future change reintroduces evaluation
material into the released knowledge, CI fails. See the README section
"Knowledge provenance & evaluation hold-out".
"""

from __future__ import annotations

import inspect

import chiron.diagnose as diagnose_mod
from chiron.knowledge import KNOWN_SIGNATURES, FixPattern

# Identifiers / tokens associated with the evaluated defect set (paper §V).
# These are the *old* crash-site tokens that were removed because they exactly
# named an evaluation case (e.g. the shipped IMSIC double-free artifact). They
# must never reappear in the released knowledge or prompts.
_EVAL_LEAK_TOKENS = (
    "imsic",           # eval interrupt-controller crash site
    "kvm_riscv_aia",   # eval crash function prefix
    "double_free",     # eval double-free case
    "double-free",
    "canonical_fixes",  # fabricated "Fixes:" hashes removed from FixPattern
    "0000000",          # leftover fabricated hash pattern
)


def _signature_blob() -> str:
    """Join every shipped signature (id, patterns, hints, fix) into one blob."""
    parts: list[str] = []
    for sig in KNOWN_SIGNATURES:
        parts.append(sig.id)
        parts.extend(sig.title_patterns)
        parts.append(sig.hint)
        parts.append(sig.subsystem)
        parts.append(sig.fix.name)
        parts.append(sig.fix.description)
        parts.append(sig.fix.guidance)
    return "\n".join(parts).lower()


def _prompt_blob() -> str:
    """Concatenate the shipped diagnosis prompt source as a blob."""
    return inspect.getsource(diagnose_mod)


def _assert_no_scan_leak(blob: str) -> None:
    lowered = blob.lower()
    leaks = [t for t in _EVAL_LEAK_TOKENS if t.lower() in lowered]
    assert not leaks, f"shipped knowledge/prompts contain evaluation-leak tokens: {leaks}"


def test_known_signatures_have_no_eval_tokens() -> None:
    _assert_no_scan_leak(_signature_blob())


def test_diagnosis_prompts_have_no_eval_tokens() -> None:
    _assert_no_scan_leak(_prompt_blob())


def test_shipped_signature_catalog_is_empty() -> None:
    """The released catalog ships no curated defect entries at all.

    With the evaluated defects held out, the companion must not bundle any
    ``BugSignature`` that could serve as a memorised answer. This is the
    strongest form of the no-leakage invariant: an empty catalog cannot encode
    an evaluation case.
    """
    assert KNOWN_SIGNATURES == (), (
        "KNOWN_SIGNATURES must be empty in the shipped companion; the "
        "evaluated defect catalog is held out of this repository"
    )


def test_canonical_fixes_field_removed() -> None:
    """The fabricated 'Fixes:'-hash field must not exist on FixPattern."""
    assert not hasattr(FixPattern, "canonical_fixes"), (
        "canonical_fixes was removed because it pre-encoded the ground-truth "
        "answer hash for evaluated defects"
    )