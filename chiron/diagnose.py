"""Multi-view collaborative diagnosis (the paper's second pillar).

CHIRON diagnoses a crash by having three specialist agents inspect it from
complementary angles and then a synthesizer enforce a *tripartite evidence
contract*. The three views are:

* **focused_code** - inspect the strongest crash-site symbol, its file, and the
  surrounding lines with source + git context;
* **trigger** - reconstruct the triggering sequence from the log/report dumps
  (the ioctl / SBI call / guest instruction path);
* **subsystem** - identify the owning subsystem (KVM core / RISC-V KVM /
  interrupt controller / MM), pull the
  relevant focus files, and nearest known defect family via RAG.

The synthesizer merges these into a single structured :class:`Diagnosis`, but
only when at least two views agree on the fault site and one names the fault
class explicitly. That contract prevents a single-vote guess from passing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .agents import LlmClient, TaskAgent, build_toolkit
from .agents.tools import Tool, ToolContext
from .core import CrashArtifact, Diagnosis, ViewEvidence
from .knowledge import KnowledgeBase
from .logging_utils import get_logger

log = get_logger(__name__)

# Prompts are plain strings; they carry no data, secrets, or dates.
FOCUSED_PROMPT = """\
You are the focused-code view of a CHIRON diagnosis task. Your job is to locate \
the exact fault site in the kernel source, using the read/search/git tools.

The crash report and any retrieved reference material follow. Use the tools to: \
read the suspected file around the crash symbol, and blame the lines to see who \
changed them last. Then return STRICT JSON with exactly these keys:
{
  "suspect_file": "<kernel-relative path>",
  "suspect_lines": [start, end],
  "root_cause": "<one-paragraph prose>",
  "confidence": 0.0..1.0,
  "fault_class": "style|compile|crash_recurrence|secondary_runtime_fault|unknown"
}
"""

TRIGGER_PROMPT = """\
You are the trigger-view analysis. Reconstruct the *reproducing sequence* from \
the crash log/report: which guest instruction, ioctl, SBI call, or context \
switch preceded the fault. Return STRICT JSON with exactly these keys:
{
  "trigger_sequence": "<prose walkthrough>",
  "fault_class": "style|compile|crash_recurrence|secondary_runtime_fault|unknown",
  "confidence": 0.0..1.0
}
"""

SUBSYSTEM_PROMPT = """\
You are the subsystem-view analysis. Determine which kernel subsystem owns this \
fault (KVM core, RISC-V KVM, interrupt controller, mm), and which fault class it \
falls into. If a knowledge reference block is given, map the crash to the \
signature family named there; otherwise classify by fault class alone. Return \
STRICT JSON with exactly these keys:
{
  "subsystem": "<name>",
  "signature_id": "<family id from the knowledge block, or \"unknown\">",
  "suspect_file": "<kernel-relative path>",
  "root_cause": "<one paragraph>",
  "confidence": 0.0..1.0,
  "fault_class": "style|compile|crash_recurrence|secondary_runtime_fault|unknown"
}
"""

SYNTHESIS_PROMPT = """\
You are the synthesizer that enforces CHIRON's tripartite evidence contract. You \
receive the three view outputs (focused_code, trigger, subsystem). A diagnosis \
may pass ONLY if BOTH hold:
  (a) at least two views name the same suspect_file,
  (b) at least one view names a fault_class (not 'unknown').
If the contract fails, set verdict="unknown". Return STRICT JSON with exactly \
these keys: {"signature_id","subject","root_cause","suspect_file","suspect_lines":\
[start,end],"verdict","contract_ok":bool,"notes"}
"""


@dataclass
class DiagnosisPipeline:
    """Runs the three view agents, then the synthesizer, over a crash."""

    client: LlmClient
    knowledge: KnowledgeBase
    kernel_root: str
    default_max_tokens: int = 1600

    def __post_init__(self) -> None:
        ctx = ToolContext(kernel_root=self.kernel_root, roots=(self.kernel_root,))
        self._toolkit = tuple(build_toolkit(ctx))

    def _view(self, role: str, prompt: str, user_text: str, tools: tuple[Tool, ...]) -> dict[str, Any]:
        agent = TaskAgent(
            client=self._client,
            role=role,
            system_prompt=prompt,
            tools=tools,
            max_tokens=self.default_max_tokens,
        )
        result = agent.run(user_text)
        try:
            payload = json.loads(result.text.strip() or "{}")
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {"_error": result.text}

    def diagnose(self, artifact: CrashArtifact) -> Diagnosis:
        kb_prompt = ""
        try:
            kb_prompt = self.knowledge.build_prompt(artifact.signature_text(), top_k=2)
        except Exception as exc:  # knowledge retrieval must never block diagnosis
            log.warning("knowledge retrieval failed: %s", exc)

        signal = artifact.signature_text()
        kb_block = kb_prompt or "# No knowledge reference available."

        focused = self._view(
            "focused_code", FOCUSED_PROMPT, f"{signal}\n\n{kb_block}", self._toolkit
        )
        trigger = self._view("trigger", TRIGGER_PROMPT, signal, ())
        subsystem_view = self._view(
            "subsystem", SUBSYSTEM_PROMPT, f"{signal}\n\n{kb_block}", self._toolkit
        )

        sys_input = (
            "FOCUSED_CODE:\n{fc}\n\nTRIGGER:\n{tr}\n\nSUBSYSTEM:\n{ss}\n\nSIGNAL:\n{sig}"
        ).format(fc=focused, tr=trigger, ss=subsystem_view, sig=signal[:4000])
        synthesis = self._view("synthesis", SYNTHESIS_PROMPT, sys_input, ())

        suspended_lines = _lines_from(synthesis.get("suspect_lines"))
        diag = Diagnosis(
            signature_id=synthesis.get("signature_id", "unknown"),
            subject=artifact.title or artifact.description,
            root_cause=synthesis.get("root_cause", ""),
            suspect_file=synthesis.get("suspect_file", ""),
            suspect_lines=suspended_lines,
            verdict=synthesis.get("verdict", "unknown"),
        )
        diag.add_evidence(ViewEvidence(view="focused_code", text=str(focused)))
        diag.add_evidence(ViewEvidence(view="trigger", text=str(trigger)))
        diag.add_evidence(ViewEvidence(view="subsystem", text=str(subsystem_view)))
        diag.add_evidence(ViewEvidence(view="synthesis", text=str(synthesis)))
        return diag


def _lines_from(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return (0, 0)
    try:
        start = int(value[0])
        end = int(value[1])
    except (TypeError, ValueError):
        return (0, 0)
    return (max(0, start), max(0, end))


__all__ = ["DiagnosisPipeline"]