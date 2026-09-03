"""Application configuration for CHIRON.

Configuration is assembled from three layers, each overriding the previous:

1. Built-in defaults (with known-good paths for the rvfuzz-ci environment).
2. A YAML config file, if provided.
3. Environment variables, prefixed ``CHIRON_``.

Every mutable object is rebuilt rather than mutated, per the project's
immutability convention. Paths may be overridden to point at the user's
kernel tree, cross compiler, QEMU, and rootfs/image; missing platform pieces
are surfaced as :class:`EnvironmentError` only at the point of use, not here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

_ENV_PREFIX = "CHIRON_"
_LOG_DIR_NAME = "logs"
_KB_DIR_NAME = "knowledge"


@dataclass(frozen=True)
class LlmConfig:
    """OpenAI-compatible endpoint settings (works with DeepSeek-V3.2)."""

    base_url: str = "https://api.deepseek.com/v1"
    api_key_env: str = "DEEPSEEK_API_KEY"
    model: str = "deepseek-chat"
    temperature: float = 0.2
    max_retries: int = 3
    timeout_seconds: float = 180.0


@dataclass(frozen=True)
class PathsConfig:
    """Platform paths. ``kernel_dir`` is the Linux kernel source tree."""

    kernel_dir: str = "/home/jiakai/test-sysroot/linux"
    cross_gcc: str = "/usr/bin/riscv64-linux-gnu-gcc"
    qemu: str = "/home/jiakai/rvfuzz-ci/tools/qemu-mainline-install/bin/qemu-system-riscv64"
    image: str = ""  # kernel image (defaults to <kernel>/arch/riscv/boot/Image)
    rootfs: str = ""  # rootfs image; empty means QEMU boot is unavailable
    kernel_args: str = "root=/dev/vda console=ttyS0"


@dataclass(frozen=True)
class ValidationConfig:
    """Tiered-validation limits and switches."""

    defconfig: str = "defconfig"
    compile_timeout_seconds: float = 1800.0
    boot_timeout_seconds: float = 180.0
    checkpatch_script: str = ""  # default: <kernel>/scripts/checkpatch.pl
    repro_guest_fs: str = "/home"  # where the reproducer C file lands in the guest


@dataclass(frozen=True)
class KnowledgeConfig:
    """Offline knowledge-base construction settings."""

    vector_store: str = "chroma"
    persist_dir: str = ""  # default: <cwd>/.chiron/knowledge
    chunk_size: int = 1200
    chunk_overlap: int = 200
    embed_model: str = "default"
    mbox_archive_url: str = "https://lore.kernel.org/kvm-riscv/"
    max_messages: int = 0  # 0 = no cap (full archive); >0 for smoke crawls


@dataclass(frozen=True)
class AppConfig:
    """Top-level configuration bundle."""

    llm: LlmConfig = field(default_factory=LlmConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    repair_max_iterations: int = 5
    diagnose_max_evidence_queries: int = 8
    workdir: str = ""  # output/scratch dir; defaults to <cwd>/.chiron

    # -- derived helpers ---------------------------------------------------- #

    def checkout(self) -> str:
        """Absolute scratch/working directory for intermediate artifacts."""
        return self._resolve_dir(self.workdir or str(Path.cwd() / ".chiron"))

    def log_dir(self) -> str:
        return str(Path(self.checkout()) / _LOG_DIR_NAME)

    def kb_dir(self) -> str:
        configured = self.knowledge.persist_dir
        return self._resolve_dir(configured or str(Path(self.checkout()) / _KB_DIR_NAME))

    def kernel_image(self) -> str:
        if self.paths.image:
            return self.paths.image
        return str(Path(self.paths.kernel_dir) / "arch" / "riscv" / "boot" / "Image")

    def checkpatch_script(self) -> str:
        if self.validation.checkpatch_script:
            return self.validation.checkpatch_script
        return str(Path(self.paths.kernel_dir) / "scripts" / "checkpatch.pl")

    @staticmethod
    def _resolve_dir(value: str) -> str:
        return str(Path(value).expanduser().resolve())


def load_config(path: str | None = None, *, overrides: dict[str, Any] | None = None) -> AppConfig:
    """Build an :class:`AppConfig` from defaults, YAML, and env overrides.

    ``overrides`` accepts a flat dict mapping dotted keys (e.g.
    ``"paths.kernel_dir"``) to values, taking precedence over the config file.
    """
    merged: dict[str, Any] = {}
    if path:
        merged = _load_yaml(path)
    if overrides:
        merged = _apply_overrides(merged, overrides)
    merged = _apply_env(merged)

    cfg = _from_mapping(merged)
    _validate(cfg)
    return cfg


def _load_yaml(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Failed to read config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a mapping at top level")
    return data


def _apply_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for dotted, value in overrides.items():
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise ConfigError(f"Config override {dotted!r} must be a scalar value")
        _set_dotted(out, dotted, value)
    return out


def _apply_env(base: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        dotted = key[len(_ENV_PREFIX) :].lower().replace("__", ".").replace("_", ".")
        _set_dotted(out, dotted, value)
    return out


def _set_dotted(mapping: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = mapping
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise ConfigError(f"Config path {dotted!r} conflicts with a scalar value")
    node[parts[-1]] = value


def _from_mapping(mapping: dict[str, Any]) -> AppConfig:
    try:
        return AppConfig(
            llm=LlmConfig(**(mapping.get("llm") or {})),
            paths=PathsConfig(**(mapping.get("paths") or {})),
            validation=ValidationConfig(**(mapping.get("validation") or {})),
            knowledge=KnowledgeConfig(**(mapping.get("knowledge") or {})),
            repair_max_iterations=int(mapping.get("repair_max_iterations", 5)),
            diagnose_max_evidence_queries=int(mapping.get("diagnose_max_evidence_queries", 8)),
            workdir=str(mapping.get("workdir", "")),
        )
    except TypeError as exc:  # Unknown/unexpected key in a section
        raise ConfigError(f"Invalid configuration keys: {exc}") from exc


def _validate(cfg: AppConfig) -> None:
    if not cfg.llm.base_url:
        raise ConfigError("llm.base_url must not be empty")