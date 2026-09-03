"""Retrieval-Augmented Generation over the domain knowledge base.

:class:`KnowledgeBase` wraps a :class:`vector_store` and exposes two roles:

* **builder** - ingest the curated spec catalog and an mbox archive into the
  store so agents can retrieve them later;
* **retriever** - for a given crash signature and subsystem, return the most
  relevant reference documents formatted as a compact prompt block.

This is the *domain knowledge* pillar feeding CHIRON's diagnosis agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..errors import KnowledgeError
from ..logging_utils import get_logger
from .mlist import build_context_documents, parse_mbox
from .specs import BugSignature, KNOWN_SIGNATURES, match_signature
from .vector_store import ScoredDoc, open_store

log = get_logger(__name__)


@dataclass(frozen=True)
class Retrieval:
    """The result of retrieving reference material for a signature."""

    signature: BugSignature | None
    docs: list[ScoredDoc] = ()
    klass_by_doc: tuple[str, ...] = ()


class KnowledgeBase:
    """A self-contained RAG store for CHIRON's domain knowledge."""

    def __init__(
        self,
        *,
        backend: str = "memory",
        persist_dir: str = "",
        collection: str = "knowledge",
        signatures: tuple[BugSignature, ...] = KNOWN_SIGNATURES,
        _store=None,
    ):
        self.signatures = signatures
        self._store = _store or open_store(backend=backend, persist_dir=persist_dir)
        self._indexed: list[str] = []

    # -- builder ------------------------------------------------------------ #

    def add_spec_signatures(self) -> int:
        """Index the curated defect catalog as reference documents."""
        ids: list[str] = []
        texts: list[str] = []
        metas: list[dict[str, Any]] = []
        for sig in self.signatures:
            ids.append(f"spec:{sig.id}")
            texts.append(
                f"[Signature {sig.id}] subsystem={sig.subsystem}\n"
                f"hint={sig.hint}\nfault_class={sig.fault_class}\n"
                f"fix={sig.fix.name}: {sig.fix.description}\n"
                f"guidance={sig.fix.guidance}"
            )
            metas.append({"kind": "signature", "subsystem": sig.subsystem, "sig_id": sig.id})
        self._store.add(ids, texts, metas)
        self._indexed.extend(ids)
        return len(ids)

    def add_mbox(self, mbox_path: str, *, max_messages: int = 0) -> int:
        """Parse and index a local mbox archive of ``kvm-riscv`` discussion."""
        docs = parse_mbox(mbox_path, max_messages=max_messages)
        chunks = build_context_documents(docs)
        if not chunks:
            log.warning("mbox %s produced no documents", mbox_path)
            return 0
        self._store.add(
            [c["id"] for c in chunks],
            [c["text"] for c in chunks],
            [{"kind": "mlist", "commit_refs": c["commit_refs"]} for c in chunks],
        )
        self._indexed.extend(c["id"] for c in chunks)
        return len(chunks)

    # -- retriever ---------------------------------------------------------- #

    def retrieve(self, signature_text: str, *, top_k: int = 5) -> list[ScoredDoc]:
        query = signature_text
        return self._store.query(query, top_k=top_k)

    def build_prompt(self, signature_text: str, *, top_k: int = 4) -> str:
        """Render the top retrievals as a compact context block for an agent."""
        sig = match_signature(signature_text, self.signatures)
        docs = self.retrieve(signature_text, top_k=top_k)
        lines: list[str] = []
        if sig is not None:
            lines.append(f"# Matched signature: {sig.id} ({sig.subsystem})")
            lines.append(f"Hint: {sig.hint}")
        if docs:
            lines.append("# Reference documents")
            for d in docs:
                lines.append(f"## {d.id} [score={d.score:.2f}]")
                lines.append(d.text.strip())
        return "\n".join(lines)

    @property
    def indexed_count(self) -> int:
        return len(self._indexed)


def build_knowledge_base(
    *,
    backend: str,
    persist_dir: str,
    mbox_path: str = "",
    max_messages: int = 0,
    signatures: tuple[BugSignature, ...] = KNOWN_SIGNATURES,
) -> KnowledgeBase:
    """Build and populate a knowledge base from the spec catalog and an mbox."""
    kb = KnowledgeBase(backend=backend, persist_dir=persist_dir, signatures=signatures)
    n_spec = kb.add_spec_signatures()
    log.info("indexed %d spec signatures", n_spec)
    if mbox_path:
        try:
            n_mlist = kb.add_mbox(mbox_path, max_messages=max_messages)
            log.info("indexed %d mlist chunks", n_mlist)
        except KnowledgeError as exc:
            log.warning("skipping mbox ingestion: %s", exc)
    return kb


__all__ = ["KnowledgeBase", "Retrieval", "build_knowledge_base"]