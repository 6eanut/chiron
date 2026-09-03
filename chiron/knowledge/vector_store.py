"""RAG vector store with graceful backend fallback.

The knowledge base uses a vector store so diagnosis agents can retrieve the
most relevant fix discussions and patch references for a crash signature.
Chromadb is the primary backend; when it is not installed in an environment,
a pure-Python :class:`MemoryStore` falls back so the framework deploys
anywhere the OpenAI-compatible endpoint is reachable.

The interface is intentionally small so either backend is interchangeable:

* ``add(ids, texts, metadatas)``
* ``query(text, top_k=...)`` -> ``list[ScoredDoc]``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..errors import KnowledgeError
from ..logging_utils import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ScoredDoc:
    """A retrieved document with its relevance score."""

    id: str
    text: str
    metadata: dict[str, Any]
    score: float


class MemoryStore:
    """A minimal in-memory store used when chromadb is unavailable.

    Retrieval is lexical (token overlap) rather than vectorial, which is
    sufficient for keyword-shaped crash signatures in small archives.
    """

    def __init__(self) -> None:
        self._items: list[tuple[str, str, dict[str, Any]]] = []

    def add(self, ids: Sequence[str], texts: Sequence[str], metadatas: Sequence[dict] | None = None) -> None:
        metadatas = metadatas or [{} for _ in texts]
        for ident, text, meta in zip(ids, texts, metadatas):
            self._items.append((ident, text, dict(meta)))

    def query(self, text: str, *, top_k: int = 5) -> list[ScoredDoc]:
        tokens = set(_tokenize(text))
        if not tokens:
            return []
        scored: list[ScoredDoc] = []
        for ident, item_text, meta in self._items:
            item_tokens = set(_tokenize(item_text))
            overlap = len(tokens & item_tokens)
            if overlap == 0:
                continue
            score = overlap / max(1, len(item_tokens))
            scored.append(ScoredDoc(id=ident, text=item_text, metadata=meta, score=score))
        scored.sort(key=lambda d: d.score, reverse=True)
        return scored[:top_k]


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


class ChromaStore:
    """A chromadb-backed persistent vector store.

    Chromadb and its default embedding provider are loaded lazily so an
    environment without them can still import this module.
    """

    def __init__(self, *, persist_dir: str, collection: str = "knowledge"):
        try:
            import chromadb  # type: ignore
        except ImportError as exc:  # pragma: no cover - env dependent
            raise KnowledgeError("chromadb is not installed") from exc
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(name=collection)

    def add(self, ids: Sequence[str], texts: Sequence[str], metadatas: Sequence[dict] | None = None) -> None:
        metadatas = metadatas or [{} for _ in texts]
        self._collection.add(ids=list(ids), documents=list(texts), metadatas=[dict(m) for m in metadatas])

    def query(self, text: str, *, top_k: int = 5) -> list[ScoredDoc]:
        result = self._collection.query(query_texts=[text], n_results=top_k)
        out: list[ScoredDoc] = []
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for ident, doc, meta, dist in zip(ids, docs, metas, distances):
            out.append(ScoredDoc(id=ident or "", text=doc or "", metadata=dict(meta or {}), score=dist or 0.0))
        return out


def open_store(*, backend: str, persist_dir: str) -> MemoryStore | ChromaStore:
    """Open the configured store, falling back to memory on missing chromadb."""
    if backend == "chroma":
        try:
            return ChromaStore(persist_dir=persist_dir)
        except KnowledgeError:
            log.warning("chromadb unavailable; falling back to in-memory store")
    return MemoryStore()


__all__ = ["ChromaStore", "MemoryStore", "ScoredDoc", "open_store"]