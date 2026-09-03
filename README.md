# CHIRON

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-GPL--2.0-blue)](LICENSE)
[![Paper](https://img.shields.io/badge/paper-cscloud%202026-brightgreen)](#status)

## Status

Implementation companion to the cscloud 2026 submission
"CHIRON: From Fuzzing Crashes to Upstream Patches: Automated Repair for
RISC-V KVM via Domain Knowledge, Multi-View Diagnosis, and Closed-Loop
Validation".
The badges above track the code, not the paper: the pointer and version are
pinned here for provenance and will be updated when the submission is public.

CHIRON is an automated-program-repair (APR) framework for crashes surfaced by
RISC-V KVM kernel fuzzing. Given a fuzzing crash artifact, CHIRON repairs the
kernel source by combining curated domain knowledge of known RISC-V KVM fault
families, retrieval-augmented generation (RAG) over a domain knowledge base,
collaborative multi-view diagnosis, and a validation-guided closed-loop repair
loop. It is the implementation companion to the cscloud 2026 paper describing
a domain-knowledge-driven APR approach for RISC-V KVM.

## Architecture

CHIRON is organized into four layers plus the end-to-end orchestrator:

- **`core`** - shared data models. `CrashArtifact` is the fuzzer input in the
  kaller/riscvkaller schema; `Diagnosis` is the structured output of the
  diagnosis stage; `RepairResult` covers a single repair + validation attempt.
- **`agents`** - the LLM client and a small suite of tools (source read,
  grep search, git blame) that the specialist diagnosis and repair agents call.
- **`knowledge`** - the domain knowledge base and RAG retrieval: the schema
  for curated `BugSignature` entries, a subsystem catalog, and a pluggable
  vector store (Chroma, or an in-memory fallback), plus an optional mbox archive
  parser for lore.kernel.org. The shipped signature catalog is intentionally
  **empty** — the paper's evaluated defect catalog is held out of this
  repository. Operators supply their own signatures; see
  [Knowledge provenance](#knowledge-provenance--evaluation-hold-out).
- **`harness`** - the tiered patch validation pipeline: checkpatch, RISC-V
  cross-compilation, and a QEMU boot that classifies runtime outcomes.

The framework is used in two phases, offline then online:

1. **Offline**: build the domain knowledge base from the curated signatures
   (and, optionally, an mbox archive of the kvm-riscv mailing list).
2. **Online**: ingest a crash, run multi-view diagnosis, generate a repair
   diff, validate it through the tiers, and iterate the diagnose -> repair ->
   validate loop until a candidate passes validation.

The two phases connect as a closed loop:

```
   OFFLINE                      ONLINE (closed loop)
   -------                      -----------------------
   curated KB + mbox
         |
         v
       RAG  -------------------> multi-view diagnosis
                                  (3 views + synthesizer)
                                         |
                                         v
                                     repair  <---------------+
                                         |                     |
                                         v                     |
                                tiered validation             |
                                style -> compile -> runtime    |
                                         |                     |
                          +---------------+                    |
                          |                                    |
                         candidate (pass)          failure (feedback
                                                    routed back to repair)
```

## Validation tiers

`harness/validate.py` runs a candidate diff through a fixed order of tiers;
each failure maps onto the fault taxonomy in `errors.py`:

1. **style** - `scripts/checkpatch.pl` conventions. Failing maps to
   `StyleFault`.
2. **compile** - cross-compile the patched kernel for RISC-V. Failing maps to
   `CompileFault`.
3. **runtime** - QEMU boot running the reproducer. If the original crash still
   reproduces, that is a `CrashRecurrenceFault`; if a different panic or
   warning appears, it is a `SecondaryFault`.

Every tier is soft-when-unavailable, but the ordering is fixed: style problems
are resolved before compiling, and a compile failure short-circuits the boot.
Feedback from a failing tier is fed back to the repair agent on the next
iteration.

## Installation

```bash
pip install -e .[dev]
```

This installs the `chiron` console entry point (from `chiron.cli:main`).

## Configuration

Configuration is assembled from three layers, each overriding the previous:
built-in defaults, a YAML config file, then `CHIRON_`-prefixed environment
variables. A full commented example lives in `examples/config.example.yaml`.

The main sections mirror the dataclasses in `chiron/config.py`:

- **`llm`** - OpenAI-compatible endpoint (`base_url`, `model`,
  `temperature`, timeouts, retries).
- **`paths`** - `kernel_dir`, `cross_gcc`, `qemu`, `rootfs`, `kernel_args`.
- **`validation`** - `defconfig`, compile/boot timeouts, `checkpatch_script`,
  `repro_guest_fs`.
- **`knowledge`** - `vector_store`, `persist_dir`, chunking, `embed_model`,
  and the optional `mbox_archive_url`.

Environment overrides use dotted paths with the `CHIRON_` prefix, for example
`CHIRON_PATHS_KERNEL_DIR=/path/to/linux`.

The LLM API key is read from the environment. Set it before running:

```bash
export DEEPSEEK_API_KEY=sk-...
```

The variable name itself is configurable via `llm.api_key_env`. CHIRON
requests it from the environment; it is never held in a config file or source.

## Usage

Build the offline knowledge base:

```bash
chiron build-kb --config config.yaml
```

Optionally include an mbox archive of the kvm-riscv list (bounded by
`knowledge.max_messages`, 0 for no cap):

```bash
chiron build-kb --config config.yaml --mbox kvm-riscv.mbox
```

Diagnose and repair a crash artifact (JSON in the kaller schema, for example
`examples/crash/kvm_vcpu_fault_synthetic.json`):

```bash
chiron run --config config.yaml --artifact crash.json
```

Smoke-test the offline knowledge and diagnosis plumbing without an API key:

```bash
chiron collect-demo
```

## Project layout

```
chiron/
  agents/     LLM client, task agent, source-read / grep / git tools
  core/       data models (CrashArtifact, Diagnosis, RepairResult)
  harness/    checkpatch, cross-compile, QEMU runner, tiered validation
  knowledge/  signatures, subsystem catalog, vector store, mbox parser, RAG
  util/       diff apply and subprocess helpers
  cli.py      console entry point (build-kb / run / collect-demo)
  config.py   layered configuration
  diagnose.py multi-view collaborative diagnosis
  pipeline.py closed-loop repair orchestration (Chiron)
  repair.py   repair agent
  errors.py   typed exceptions and the validation fault taxonomy
examples/
  config.example.yaml                     commented config template
  crash/kvm_vcpu_fault_synthetic.json     synthetic sample crash artifact
```

## Knowledge provenance & evaluation hold-out

The paper evaluates CHIRON on defects it reports as first-discovered
zero-days, and argues the results come from *domain-grounded reasoning* rather
than from retrieving memorised solutions. To keep that claim auditable, the
knowledge shipped in this repository is deliberately decoupled from the
evaluation:

- **Empty shipped catalog, not defect instances.** The released `knowledge`
  module ships the `BugSignature`/`FixPattern` *schema* but an **empty**
  `KNOWN_SIGNATURES`: no curated entry exists in this repository, so no shipped
  entry can match a defect or land on a specific crash site. Operators build
  their own catalog from upstream lore and commit patterns outside this repo.
- **No evaluated artifacts in-repo.** The five defects studied in the paper and
  their true crash signatures and reproducers are **held out**: they are not
  part of this release. The single crash under `examples/crash/` is an explicit
  synthetic illustration, not an evaluation case.
- **Enforced by a test.** `tests/test_knowledge_no_leakage.py` asserts the
  shipped catalog is empty and that the knowledge module and diagnosis prompts
  contain none of the identifiers/function shapes associated with the evaluated
  defects. It runs in CI. If a future change reintroduces evaluation material
  into the released knowledge base, the build fails.

The intent is that reviewing the repo tells a reviewer which fault families the
framework knows, while the held-out evaluation is what the paper reports — so a
5/5 result cannot be explained by the shipped knowledge already encoding the
answers.

## FAQ / troubleshooting

**What happens when checkpatch or QEMU is absent?** The harness tiers are soft:
a missing `checkpatch_script` or kernel tree logs and treats the corresponding
tier as passed, and a missing `rootfs`/`qemu` means the runtime tier is skipped
(soft pass). Only end-to-end `run` still requires the LLM API key.

**How does the memory backend fallback work?** `pipeline.Chiron._build_knowledge`
tries the configured Chroma store first; if construction fails with a
`ChironError`, it falls back to an in-memory store so repair can proceed
without a persistent vector database.

**What is the multi-view evidence contract?** `diagnose.py` runs three views
(focused_code, trigger, subsystem) and a synthesizer. A diagnosis passes only
if at least two views name the same suspect file and at least one view names a
fault class; otherwise the verdict is `unknown`. This prevents a single
single-vote guess from driving the repair.