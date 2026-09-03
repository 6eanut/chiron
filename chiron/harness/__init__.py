"""CHIRON harness package: tiered validation of candidate repair diffs.

Re-exports the four validation tiers and the orchestrating result type:
:func:`run_checkpatch`, :func:`cross_compile_kernel`, :func:`run_reproducer`,
:func:`validate_patch`, and :class:`ValidationResult`.
"""

from .checkpatch import run_checkpatch
from .cross_compile import cross_compile_kernel
from .qemu_runner import boot_present, run_reproducer
from .validate import ValidationResult, validate_patch

__all__ = [
    "run_checkpatch",
    "cross_compile_kernel",
    "run_reproducer",
    "boot_present",
    "validate_patch",
    "ValidationResult",
]