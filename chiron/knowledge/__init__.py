"""CHIRON knowledge package: domain knowledge base and RAG retrieval."""

from .mlist import MboxDocument, build_context_documents, parse_mbox
from .rag import KnowledgeBase, Retrieval, build_knowledge_base
from .specs import BugSignature, FixPattern, KNOWN_SIGNATURES, SUBSYSTEM_CATALOG, match_signature
from .vector_store import ChromaStore, MemoryStore, ScoredDoc, open_store

__all__ = [
    "BugSignature",
    "ChromaStore",
    "FixPattern",
    "KNOWN_SIGNATURES",
    "KnowledgeBase",
    "MboxDocument",
    "MemoryStore",
    "Retrieval",
    "SUBSYSTEM_CATALOG",
    "ScoredDoc",
    "build_context_documents",
    "build_knowledge_base",
    "match_signature",
    "open_store",
    "parse_mbox",
]