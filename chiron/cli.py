"""Command-line interface for CHIRON.

Three subcommands:

* ``build-kb``   - construct the offline knowledge base (spec signatures, and
                   an mbox archive when a path is supplied) and print its size;
* ``run``        - diagnose and repair a crash artifact;
* ``collect-demo`` - a smoke path proving knowledge + diagnosis plumbing loads
                   without requiring an API key.

User-facing summary lines go to stdout (this is a CLI); everything else is
logged through ``chiron`` loggers.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path

from .config import load_config
from .core.models import CrashArtifact
from .errors import ArtifactError, ChironError
from .knowledge.rag import build_knowledge_base
from .knowledge.specs import KNOWN_SIGNATURES
from .logging_utils import get_logger
from .pipeline import Chiron

log = get_logger("cli")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except ChironError as exc:
        log.error("chiron error: %s", exc)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "build-kb":
        return _build_kb(args)
    if args.command == "collect-demo":
        return _collect_demo(args)
    return _run(args)


# -- build-kb -------------------------------------------------------------- #


def _build_kb(args: argparse.Namespace) -> int:
    config = load_config(args.config or None)
    kb = build_knowledge_base(
        backend=config.knowledge.vector_store,
        persist_dir=config.kb_dir(),
        mbox_path=args.mbox or "",
        max_messages=config.knowledge.max_messages,
        signatures=KNOWN_SIGNATURES,
    )
    count = kb.indexed_count
    print(f"knowledge base ready: {count} indexed documents")
    return 0


# -- collect-demo ---------------------------------------------------------- #


def _collect_demo(args: argparse.Namespace) -> int:
    # No API key required: exercise only the offline knowledge/diagnosis path.
    config = load_config(args.config or None)
    kb = build_knowledge_base(backend="memory", persist_dir="", signatures=KNOWN_SIGNATURES)
    sample = "KVM: race in kvm_vcpu_block | panic in kvm_riscv"
    prompt = kb.build_prompt(sample, top_k=2)
    print(f"[collect-demo] signature path resolves: {bool(prompt.strip())}")
    print(f"[collect-demo] config.kernel_dir={config.paths.kernel_dir}")
    return 0


# -- run ------------------------------------------------------------------- #


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config or None)
    artifact = _load_artifact(args)

    max_iters = args.max_iterations or config.repair_max_iterations
    if max_iters != config.repair_max_iterations:
        config = dataclasses.replace(config, repair_max_iterations=max_iters)

    api_key = os.environ.get(config.llm.api_key_env, "") or None
    orchestrator = Chiron(config=config, api_key=api_key)

    results = orchestrator.run_artifact(artifact)
    for result in results:
        log.info(
            "iteration %d fault_category=%s patch_applied=%s",
            result.iteration, result.fault_category, result.patch_applied,
        )

    if results and results[-1].patch_applied:
        patch_path = _write_patch(results[-1], config)
        print(f"repair accepted; patch written to {patch_path}")
    else:
        print(f"repair not accepted after {len(results)} iteration(s)")
    return 0


def _load_artifact(args: argparse.Namespace) -> CrashArtifact:
    if args.artifact:
        path = Path(args.artifact).expanduser().resolve()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ArtifactError(f"Failed to read artifact {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ArtifactError(f"Artifact {path} must be a JSON object (kaller schema)")
        return CrashArtifact.from_mapping(raw)
    return CrashArtifact.from_mapping({"title": args.title or "", "log": args.log or ""})


def _write_patch(result, config) -> str:
    out_dir = Path(config.checkout()) / "patches"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"accepted-iter{result.iteration}.patch"
    path.write_text(result.diff, encoding="utf-8")
    return str(path)


# -- parser ---------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chiron",
        description="Automated repair for RISC-V KVM fuzzing crashes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    kb = sub.add_parser("build-kb", help="build the offline knowledge base")
    kb.add_argument("--config", default=None, help="YAML config path")
    kb.add_argument("--mbox", default=None, help="path to a kvm-riscv mbox archive")

    run = sub.add_parser("run", help="diagnose and repair a crash artifact")
    run.add_argument("--config", default=None, help="YAML config path")
    run.add_argument("--artifact", default=None, help="path to a JSON crash artifact")
    run.add_argument("--title", default="", help="crash title (fallback if no --artifact)")
    run.add_argument("--log", default="", help="crash log text (fallback if no --artifact)")
    run.add_argument("--max-iterations", type=int, default=None, help="repair loop bound")
    run.add_argument("--repro-c", default=None, help="path to a reproducer C file")

    demo = sub.add_parser("collect-demo", help="smoke-test offline knowledge path (no API key)")
    demo.add_argument("--config", default=None, help="YAML config path")

    return parser


if __name__ == "__main__":  # pragma: no cover - console entry
    raise SystemExit(main())


__all__ = ["main"]