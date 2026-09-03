"""Cross-compilation validation tier.

Builds the RISC-V kernel with the cross toolchain. ``defconfig`` succeeds first,
then the ``Image`` target. A failed build surfaces as a :class:`CompileFault`
whose detail text is the tail of the build log (including the failing line) so
the repair agent can react.

Like the other platform-bound tiers this is *soft*: if the cross compiler or
kernel tree is absent the tier degrades (logged, returns ``None``) rather than
hard-failing. A present-but-failing build is a genuine defect and raises.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import AppConfig
from ..errors import CompileFault
from ..logging_utils import get_logger
from ..util.subprocess import CommandError, make_env, run_command, verify_platform

log = get_logger(__name__)

_DIAG_BYTES = 8_192  # tail of build output included in a CompileFault


def cross_compile_kernel(*, config: AppConfig) -> None:
    """Configure and build the patched RISC-V kernel.

    Returns ``None`` on success (or graceful degradation); raises
    :class:`CompileFault` when the toolchain is present but the build fails.
    """
    kernel_dir = config.paths.kernel_dir
    cross_gcc = config.paths.cross_gcc

    try:
        verify_platform(cross_gcc)
    except CommandError as exc:
        log.warning("cross-compiler unavailable (%s); skipping compile tier", exc)
        return None
    if not Path(kernel_dir).is_dir():
        log.warning("kernel tree %s missing; skipping compile tier", kernel_dir)
        return None

    prefix = _cross_prefix(cross_gcc)
    env = make_env()
    timeout = config.validation.compile_timeout_seconds

    defconfig_cmd = ["make", "ARCH=riscv", f"CROSS_COMPILE={prefix}", config.validation.defconfig]
    _run_tier(config, defconfig_cmd, kernel_dir, env, timeout, phase="defconfig")

    nproc = os.cpu_count() or 4
    build_cmd = ["make", f"-j{nproc}", "ARCH=riscv", f"CROSS_COMPILE={prefix}", "Image"]
    _run_tier(config, build_cmd, kernel_dir, env, timeout, phase="Image")

    log.info("kernel cross-compile succeeded")
    return None


def defconfig_built_path(config: AppConfig) -> str:
    """Return the path of the generated defconfig, if present."""
    return str(
        Path(config.paths.kernel_dir)
        / "arch"
        / "riscv"
        / "configs"
        / config.validation.defconfig
    )


def _run_tier(
    config: AppConfig,
    argv: list[str],
    cwd: str,
    env: dict[str, str],
    timeout: float,
    *,
    phase: str,
) -> None:
    try:
        result = run_command(argv, cwd=cwd, env=env, timeout_seconds=timeout, check=False)
    except CommandError as exc:
        raise CompileFault(_tail(exc.output)) from exc
    if not result.ok():
        log.error("%s build failed (rc=%s)", phase, result.returncode)
        raise CompileFault(_tail(result.all_output()))


def _cross_prefix(cross_gcc: str) -> str:
    """Derive the CROSS_COMPILE prefix, e.g. ``riscv64-linux-gnu-gcc`` ->
    ``riscv64-linux-gnu-``."""
    base = Path(cross_gcc).name
    if base.endswith("-gcc"):
        return base[: -len("-gcc")] + "-"
    return base.rsplit("-", 1)[0] + "-" if "-" in base else base


def _tail(output: str) -> str:
    """Return the last ~8KB of build output, keeping the error line visible."""
    text = output.strip()
    if len(text) <= _DIAG_BYTES:
        return text
    return text[-_DIAG_BYTES:]


__all__ = ["cross_compile_kernel", "defconfig_built_path"]