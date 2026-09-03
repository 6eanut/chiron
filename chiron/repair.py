"""Validation-guided iterative repair (the paper's third pillar).

Given a :class:`Diagnosis` that named a suspect site, the repair agent:

1. asks the LLM to emit a *minimal* unified diff,
2. machine-validates the diff (well-formed, non-empty, bounded size),
3. hands the diff to the validation harness; on failure feedback, iterates,
   narrowing the diff toward the fault class ``style``/``compile``/
   ``crash_recurrence``/``secondary_runtime_fault``,
4. finally drafts a kernel-conforming commit message with a ``Fixes:`` tag and
   an AI-assist provenance trailer.

Only the LLM-generated text carries no dates or secrets; the diff and message
are handled as inert data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .agents import TaskAgent
from .agents.client import LlmClient
from .core import Diagnosis, RepairResult
from .util.diff import DiffError, apply_diff, diff_statistics, validate_diff

__all__ = ["RepairAgent"]


_FIXES_RE = re.compile(r"^Fixes:\s*([0-9a-f]{7,40})$", re.MULTILINE | re.IGNORECASE)


@dataclass
class RepairAgent:
    """Produces a repair patch and an upstream-style commit message.

    ``client`` is the shared OpenAI-compatible LLM; ``kernel_root`` bounds the
    tree where the diff is applied.
    """

    client: LlmClient
    kernel_root: str
    max_tokens: int = 2000

    # -- candidate generation --------------------------------------------- #

    def produce_diff(self, diagnosis: Diagnosis, *, feedback: str = "") -> str:
        """Ask the LLM for a minimal diff targeting the diagnosed fault."""
        agent = TaskAgent(
            client=self.client,
            role="repair",
            system_prompt=(
                "You are a kernel bug-fixing agent. Produce a MINIMAL unified "
                "diff that fixes the diagnosed crash. Constrain every edit to "
                "the diagnosed file. Never rewrite whole files or add unrelated "
                "refactors. Return ONLY the diff text."
            ),
            tools=(),
            max_tokens=self.max_tokens,
        )
        prompt = _diagnosis_blurb(diagnosis)
        if feedback:
            prompt += f"\n\nA previous attempt was rejected. Feedback:\n{feedback}\n"
        result = agent.run(prompt)
        diff = _extract_diff(result.text)
        validate_diff(diff)
        return diff

    # -- candidate validation --------------------------------------------- #

    def apply(self, diff: str) -> RepairResult:
        """Apply ``diff`` into the kernel tree, recording success."""
        apply_diff(diff, kernel_dir=self.kernel_root)
        stats = diff_statistics(diff)
        return RepairResult(diff=diff, patch_applied=True, message=stats.summary)

    # -- final artifact ---------------------------------------------------- #

    def commit_message(self, diagnosis: Diagnosis) -> str:
        """Draft a kernel-conforming commit message with Fixes/provenance trailer."""
        subject = (diagnosis.subject or "KVM: fix fuzzing crash").strip()
        fixes = _FIXES_RE.search(diagnosis.root_cause)
        fixes_tag = ("Fixes: " + fixes.group(1) + "\n") if fixes else ""
        root_cause = diagnosis.root_cause or "A fuzz-driven KVM RISC-V crash was fixed by this change."
        body = (
            f"{subject}\n\n"
            f"{root_cause.strip()}\n\n"
            f"{fixes_tag}"
            f"Link: https://github.com/6eanut/chiron (AI-assisted patch).\n"
        )
        return "\n".join(line for line in body.splitlines() if line != "") + "\n"


def _extract_diff(text: str) -> str:
    """Return the diff block from ``text``, tolerating surrounding prose."""
    start = text.find("diff --git")
    if start == -1:
        raise DiffError("No unified diff found in the repair agent output")
    return text[start:].rstrip()


def _diagnosis_blurb(diagnosis: Diagnosis) -> str:
    return (
        f"Diagnosis: signature={diagnosis.signature_id}, verdict={diagnosis.verdict}\n"
        f"Suspected file: {diagnosis.suspect_file}@{diagnosis.suspect_lines}\n"
        f"{diagnosis.root_cause}\n\nDiag. evidence:\n{_evidence_join(diagnosis)}"
    )


def _evidence_join(diagnosis: Diagnosis) -> str:
    blocks = []
    for view in diagnosis.evidence:
        blocks.append(str(getattr(view, "text", view)))
    return "\n---\n".join(blocks or (diagnosis.root_cause,))