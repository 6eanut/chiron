"""Closed-loop repair orchestration (the paper's online pillar).

:class:`Chiron` ties the offline knowledge base, multi-view diagnosis, repair
agent, and tiered validation harness into the iterative diagnose -> repair ->
validate loop that narrows a candidate patch toward a clean validation.
"""

from __future__ import annotations

import os
from typing import Any

from .agents import LlmClient
from .config import AppConfig, load_config
from .core.models import CrashArtifact, RepairResult
from .diagnose import DiagnosisPipeline
from .errors import LlmError, ChironError
from .harness.validate import validate_patch
from .knowledge.rag import build_knowledge_base
from .logging_utils import get_logger
from .repair import RepairAgent

log = get_logger(__name__)


class Chiron:
    """The end-to-end repair orchestrator."""

    def __init__(self, *, config: AppConfig, api_key: str | None = None):
        self._config = config
        api_key = api_key or os.environ.get(config.llm.api_key_env, "").strip()
        if not api_key:
            raise LlmError(
                f"LLM API key not set: define {config.llm.api_key_env!r} in the environment "
                "(never hardcode secrets in source)"
            )
        self._client = LlmClient(config.llm, api_key=api_key)
        self._kb = self._build_knowledge(config)
        self._diagnoser = DiagnosisPipeline(self._client, self._kb, config.paths.kernel_dir)
        self._repair = RepairAgent(client=self._client, kernel_root=config.paths.kernel_dir)

    @staticmethod
    def _build_knowledge(config: AppConfig):
        """Build the offline KB, falling back to an in-memory store on failure."""
        try:
            return build_knowledge_base(
                backend=config.knowledge.vector_store,
                persist_dir=config.kb_dir(),
                mbox_path="",
                signatures=__import__("chiron.knowledge", fromlist=("KNOWN_SIGNATURES",))
                .KNOWN_SIGNATURES,
            )
        except ChironError as exc:
            log.warning("vector knowledge base unavailable; using in-memory store: %s", exc)
            return build_knowledge_base(backend="memory", persist_dir="")

    def run_artifact(self, artifact: CrashArtifact) -> list[RepairResult]:
        """Run the closed loop for a single crash; return all attempts' results."""
        diagnosis = self._diagnoser.diagnose(artifact)
        log.info(
            "diagnosed signature=%s suspect=%s verdict=%s",
            diagnosis.signature_id, diagnosis.suspect_file, diagnosis.verdict,
        )

        feedback = ""
        results: list[RepairResult] = []
        for iteration in range(1, self._config.repair_max_iterations + 1):
            diff = self._repair.produce_diff(diagnosis, feedback=feedback)
            try:
                result = validate_patch(
                    diff,
                    config=self._config,
                    artifact=artifact,
                    reproducer_c="",
                    apply=True,
                )
            except ChironError as exc:
                log.error("validation harness failed: %s", exc)
                raise

            if result.passed:
                results.append(self._pass_result(diff, iteration, result.console_transcript))
                log.info("iteration %d passed; patch accepted", iteration)
                break

            fault = result.fault
            feedback = fault.feedback_text() if fault is not None else result.message()
            category = fault.category if fault is not None else "unknown"
            results.append(
                RepairResult(
                    diff=diff,
                    patch_applied=False,
                    fault_category=category,
                    message=result.message(),
                    test_output=fault.feedback_text() if fault is not None else "",
                    iteration=iteration,
                )
            )
            log.info("iteration %d failed (fault=%s)", iteration, category)
        return results

    def main_flow(self, main_cfg: str | None = None, artifact_cfg: dict[str, Any] | None = None):
        """Run from a YAML config path and a crash-artifact mapping."""
        config = load_config(main_cfg or None)
        artifact = CrashArtifact.from_mapping(artifact_cfg or {})
        return self.__class__(config=config).run_artifact(artifact)

    @staticmethod
    def _pass_result(diff: str, iteration: int, transcript: str) -> RepairResult:
        return RepairResult(
            diff=diff,
            patch_applied=True,
            fault_category="clean",
            message="patch validated against all available tiers",
            test_output=transcript or "all validation tiers passed",
            iteration=iteration,
        )


__all__ = ["Chiron"]