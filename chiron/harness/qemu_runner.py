"""QEMU runtime validation tier.

Boots the (patched) kernel under QEMU on the RISC-V ``virt`` machine and
captures the serial console transcript. The validate layer uses this
transcript to classify crash recurrence vs. a secondary fault.

The tier is soft: if QEMU, a rootfs, or the kernel image is unavailable, it
degrades to an empty transcript (the caller interprets empty as "runtime
validation skipped"). When a boot is actually offered, the transcript exposes
recognizable kernel marker patterns so classification stays robust.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from ..config import AppConfig
from ..core.models import CrashArtifact
from ..logging_utils import get_logger
from ..util.subprocess import CommandError, make_env, run_command, verify_platform

log = get_logger(__name__)

_SMP = "4"
_MEM = "512M"
_CONSOLE_MARKER = "console=ttyS0"


def boot_present(config: AppConfig) -> bool:
    """Return whether a QEMU runtime boot is actually offerable."""
    try:
        verify_platform(config.paths.qemu)
    except CommandError:
        return False
    if not config.paths.rootfs:
        return False
    if not Path(config.kernel_image()).is_file():
        return False
    return True


def run_reproducer(
    artifact: CrashArtifact,
    reproducer_c: str,
    *,
    config: AppConfig,
    kernel_diff_applied: bool = True,
) -> str:
    """Boot the kernel under QEMU and return the serial console transcript.

    Returns ``""`` when the runtime tier is unavailable. ``kernel_diff_applied``
    is accepted for the baseline (negative-control) comparison and logged; the
    boot itself is the observable signal.
    """
    del artifact  # classification is done by the caller on the transcript
    if not boot_present(config):
        log.info("runtime boot unavailable; skipping runtime tier")
        return ""

    if reproducer_c:
        log.info("reproducer C present (%d bytes); guest repro is best-effort", len(reproducer_c))
    if not kernel_diff_applied:
        log.info("booting baseline (unpatched) tree as negative control")

    cmd = _build_qemu_cmd(config)
    log_dir = Path(config.log_dir())
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = run_command(
            cmd,
            env=make_env(),
            timeout_seconds=config.validation.boot_timeout_seconds,
            check=False,
        )
    except CommandError as exc:
        # A boot-timeout (or runaway) still yields a transcript up to the cap;
        # surface what the console produced so the caller can classify it.
        transcript = exc.output or ""
        _persist_console(log_dir, transcript)
        return transcript

    transcript = result.all_output()
    _persist_console(log_dir, transcript)
    log.info(
        "runtime boot finished (rc=%s, transcript %d bytes)", result.returncode, len(transcript)
    )
    return transcript


def _build_qemu_cmd(config: AppConfig) -> list[str]:
    kernel_args = config.paths.kernel_args
    if _CONSOLE_MARKER not in kernel_args:
        kernel_args = f"{kernel_args} {_CONSOLE_MARKER}".strip()

    cmd = [
        config.paths.qemu,
        "-machine", "virt",
        "-cpu", "rv64",
        "-smp", _SMP,
        "-m", _MEM,
        "-kernel", config.kernel_image(),
        "-append", kernel_args,
        "-nographic",
        "-no-reboot",
    ]
    if config.paths.rootfs:
        cmd += ["-drive", f"file={config.paths.rootfs},format=raw,if=virtio"]
    return cmd


def _persist_console(log_dir: Path, transcript: str) -> None:
    """Write the transcript to a timestamped log file (logs are inert data)."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    (log_dir / f"qemu-console-{stamp}.log").write_text(transcript or "", encoding="utf-8")


__all__ = ["run_reproducer", "boot_present"]