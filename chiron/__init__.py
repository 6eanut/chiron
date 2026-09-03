"""CHIRON: Automated repair for RISC-V KVM fuzzing crashes.

CHIRON is a specification-grounded automated program repair framework that
bridges raw syzkaller crash artifacts to maintainer-acceptable Linux kernel
patches for the RISC-V KVM hypervisor. See the cscloud 2026 paper for the
full architecture.

This package is organized into four layers:

- ``chiron.core``        the online repair pipeline (diagnosis, synthesis,
                         repair, validation, orchestration)
- ``chiron.agents``      the model-agnostic LLM agent runtime and tool
                         registry
- ``chiron.knowledge``   the offline domain-knowledge builder and RAG store
- ``chiron.harness``     the tiered validation harness (checkpatch /
                         cross-compile / QEMU)
- ``chiron.util``        shared helpers
"""

__version__ = "0.1.0"