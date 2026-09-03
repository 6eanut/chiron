"""Kernel mailing-list archive ingestion.

CHIRON's knowledge builder amasses evidence from the RISC-V KVM review
traffic on ``lore.kernel.org`` (an ``mbox`` of ``kvm-riscv``). This module
parses that mbox into normalized documents, threads them by ``In-Reply-To``,
and extracts the upstream commit references that link a discussion to the fix
that eventually landed.

Network fetches are optional and all I/O is local otherwise, so the builder
stays usable (and testable) without an archive present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from mailbox import mbox as Mbox
from pathlib import Path
from typing import Iterator, Sequence

from ..errors import KnowledgeError
from ..logging_utils import get_logger

log = get_logger(__name__)

_FIXES_RE = re.compile(r"^Fixes:\s*([0-9a-f]{7,40})", re.MULTILINE | re.IGNORECASE)
_COMMIT_REF_RE = re.compile(r"\bcommit\s+([0-9a-f]{7,40})", re.IGNORECASE)


@dataclass(frozen=True)
class MboxDocument:
    """One normalized message from the archive."""

    doc_id: str          # Message-ID, used as the vector-store id
    subject: str
    body: str
    from_addr: str
    date: datetime
    in_reply_to: str = ""
    # Upstream commit hashes referenced in the message body.
    commit_refs: tuple[str, ...] = ()


def iter_mail_messages(mbox_path: str | Path) -> Iterator[Message]:
    """Yield parsed email messages from a local ``mbox`` file."""
    path = Path(mbox_path)
    if not path.exists():
        raise KnowledgeError(f"mbox not found: {path}")
    box = Mbox(str(path))
    parser = BytesParser(policy=policy.default)
    for raw in box:
        parsed = parser.parsebytes(raw.as_bytes())
        if parsed:
            yield parsed


def parse_mbox(mbox_path: str | Path, *, max_messages: int = 0) -> list[MboxDocument]:
    """Parse an mbox into normalized documents; ``max_messages`` caps ingestion (0 = all)."""
    docs: list[MboxDocument] = []
    for idx, msg in enumerate(iter_mail_messages(mbox_path)):
        if max_messages and idx >= max_messages:
            break
        date = _parse_date(msg.get("Date", ""))
        subject = _header_text(msg, "Subject")
        body = _body_text(msg)
        if not subject and not body:
            continue
        doc_id = msg.get("Message-ID", f"msg-{idx}").strip()
        docs.append(
            MboxDocument(
                doc_id=doc_id,
                subject=subject,
                body=body,
                from_addr=_header_text(msg, "From"),
                date=date,
                in_reply_to=_header_text(msg, "In-Reply-To"),
                commit_refs=_extract_commit_refs(body),
            )
        )
    return docs


def build_context_documents(docs: Sequence[MboxDocument], *, window: int = 3) -> list[dict]:
    """Expand each doc into RAG-ready chunks, threading adjacent replies.

    Each message keeps its subject and body; companion replies expand to
    contextual windows that improve retrieval of a threaded fix discussion.
    """
    chunks: list[dict] = []
    for i, doc in enumerate(docs):
        if not doc.body.strip():
            continue
        context = " ".join(d.subject for d in docs[max(0, i - window) : i])
        chunks.append(
            {
                "id": f"{doc.doc_id}-self",
                "text": f"{doc.subject}\n{doc.body}",
                "commit_refs": doc.commit_refs,
            }
        )
        for j in range(i + 1, min(i + window + 1, len(docs))):
            follow = docs[j]
            if not follow.body.strip():
                continue
            chunks.append(
                {
                    "id": f"{doc.doc_id}-reply-{j}",
                    "text": f"On {follow.subject}\n{follow.body}",
                    "commit_refs": follow.commit_refs,
                }
            )
    return chunks


def _parse_date(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _header_text(msg: Message, name: str) -> str:
    value = msg.get(name)
    if value is None:
        return ""
    return str(value).strip()


def _body_text(msg: Message) -> str:
    parts: list[str] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("text/plain", "text/x-patch"):
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode("utf-8", errors="replace"))
    return "\n".join(p for p in parts if p)


def _extract_commit_refs(body: str) -> tuple[str, ...]:
    refs: list[str] = []
    for match in _FIXES_RE.findall(body) or _COMMIT_REF_RE.findall(body):
        ref = _normalize_commit(match)
        if ref and ref not in refs:
            refs.append(ref)
    return tuple(refs)


def _normalize_commit(ref: str) -> str:
    if re.fullmatch(r"[0-9a-f]{7,40}", ref):
        return ref
    return ""


__all__ = ["MboxDocument", "build_context_documents", "iter_mail_messages", "parse_mbox"]