"""User-uploaded documents: validated, hashed, chunked with the project's structure-aware chunker, indexed into a
PRIVATE per-owner collection, searchable through the same retrieval interface, listable and deletable.

Isolation (three independent layers, each tested):
  1. a separate collection per owner: `uploads_<sha256(owner)[:24]>`; never the official collection (which also lives in
     a physically separate store);
  2. every chunk carries `owner_id = owner_key(owner)` and every query filters on it;
  3. `retrieval.enforce_scope` re-checks each returned passage and fails closed (ScopeViolation).
The raw owner id (possibly an email) is never stored in the index, metadata or collection name.

Handling of the file itself: processed IN MEMORY (see extract.py); the original bytes are not retained, no temp
file is written, nothing is executed. Only extracted chunks and their provenance are stored.

Deduplication is by content hash PER OWNER: re-uploading identical bytes is a no-op ("duplicate"). It never looks at
other owners' collections, so an upload can never reveal that someone else has the same file.

Deletion: rows are removed immediately (the document is no longer searchable) and, if the client offers `compact()`,
the store file is compacted. Production uses `sqlite_store.SqliteUploadClient`, where this is a verified physical purge
(see tests/test_sqlite_store.py). With any other client (e.g. Chroma, whose index files were measured to retain text
residue) deletion is LOGICAL ONLY and `DeleteResult.purged` is False: the store says so instead of claiming otherwise.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import injection
from .chunking import anchor_for_section, chunk_structured
from .config import EngineConfig
from .extract import UploadError, extract, file_extension, sanitize_filename, validate_content
from .retrieval import (
    ChromaRetriever, EmbedFn, RetrievalResult, RetrievalScope, owner_key, upload_collection_name,
)

OWNER_RE = re.compile(r"^[A-Za-z0-9_.@\-]{1,64}$")
DOC_ID_RE = re.compile(r"^[0-9a-f]{16}$")
INDEX_VERSION = "upload-v1"
EmbedMany = Callable[[Sequence[str]], List[List[float]]]


@dataclass(frozen=True)
class UploadResult:
    status: str                 # "indexed" | "duplicate"
    doc_id: str
    filename: str
    file_type: str
    chunks: int
    pages: Optional[int]
    content_hash: str
    flagged_chunks: int = 0               # chunks containing instruction-like text (kept as data, marked)
    flag_patterns: tuple = ()


@dataclass(frozen=True)
class DocInfo:
    doc_id: str
    filename: str
    file_type: str
    chunks: int
    pages: Optional[int]
    uploaded_at: str
    content_hash: str


@dataclass(frozen=True)
class DeleteResult:
    chunks: int            # chunks removed from the owner's collection
    purged: bool           # True only when the store file was also compacted (physical purge); False = logical delete only


def validate_owner(owner_id: object) -> str:
    if not isinstance(owner_id, str) or not OWNER_RE.match(owner_id):
        raise UploadError("bad_owner", "owner id must be 1-64 characters from [A-Za-z0-9_.@-]")
    return owner_id


class UploadStore:
    def __init__(self, client: Any, embed_query: EmbedFn, embed_many: EmbedMany, cfg: EngineConfig):
        self._client, self._embed_q, self._embed_many, self._cfg = client, embed_query, embed_many, cfg

    # ------------------------------------------------------------------ plumbing
    def _name(self, owner_id: str) -> str:
        name = upload_collection_name(self._cfg.upload_collection_prefix, validate_owner(owner_id))
        if name == self._cfg.official_collection:  # defence in depth: uploads can never target the official corpus
            raise UploadError("bad_owner", "invalid collection mapping")
        return name

    def _collection(self, owner_id: str, create: bool = False) -> Optional[Any]:
        name = self._name(owner_id)
        if create:
            return self._client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
        try:
            return self._client.get_collection(name)
        except Exception:
            return None

    def _compact(self) -> bool:
        compact = getattr(self._client, "compact", None)
        return bool(compact()) if callable(compact) else False

    # ------------------------------------------------------------------ ingest
    def ingest(self, owner_id: str, filename: str, data: bytes) -> UploadResult:
        cfg = self._cfg
        validate_owner(owner_id)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise UploadError("empty_file", "the file is empty")
        if len(data) > cfg.max_upload_bytes:
            raise UploadError("file_too_large", f"file is {len(data)} bytes; the limit is {cfg.max_upload_bytes}")
        data = bytes(data)
        clean = sanitize_filename(filename)
        ext = file_extension(clean)
        validate_content(ext, data)

        content_hash = hashlib.sha256(data).hexdigest()
        doc_id = content_hash[:16]
        col = self._collection(owner_id, create=True)

        existing = col.get(include=["metadatas"])
        docs = {m.get("doc_id") for m in existing["metadatas"] if m}
        if doc_id in docs:
            first = next(m for m in existing["metadatas"] if m and m.get("doc_id") == doc_id)
            return UploadResult("duplicate", doc_id, first.get("source_file", clean), ext,
                                sum(1 for m in existing["metadatas"] if m and m.get("doc_id") == doc_id),
                                first.get("pages"), content_hash)
        if len(docs) >= cfg.max_docs_per_owner:
            raise UploadError("quota_exceeded", f"at most {cfg.max_docs_per_owner} documents per owner; delete one first")

        doc = extract(ext, data, max_pages=cfg.max_pdf_pages, max_chars=cfg.max_extracted_chars)
        key = owner_key(owner_id)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ids: List[str] = []
        texts: List[str] = []
        metas: List[Dict[str, Any]] = []
        n = 0
        for unit in doc.units:
            for ch in chunk_structured(unit.text, normalize=(ext == "pdf")):
                anchor = anchor_for_section(ch.get("section")) or "top"
                meta: Dict[str, Any] = {
                    "owner_id": key, "doc_id": doc_id, "content_hash": content_hash, "source_file": clean,
                    "file_type": ext, "anchor": anchor, "section": str(ch.get("section") or "")[:200], "chunk": n,
                    "content_type": "upload", "uploaded_at": now, "index_version": INDEX_VERSION,
                }
                if unit.page is not None:
                    meta["page"] = unit.page
                found = injection.flag_names(injection.scan_text(ch["text"]))
                if found:
                    meta["injection_flags"] = ",".join(sorted(set(found)))
                ids.append(f"upl:{doc_id}:p{unit.page or 0}:{anchor}:{n}")
                texts.append(ch["text"])
                metas.append(meta)
                n += 1
                if n > cfg.max_chunks_per_doc:
                    raise UploadError("too_many_chunks", f"document produces more than {cfg.max_chunks_per_doc} chunks")
        if not texts:
            raise UploadError("no_extractable_text", "the file has no extractable text")
        pages = max((u.page for u in doc.units if u.page is not None), default=None)
        for m in metas:
            if pages is not None:
                m["pages"] = pages
        col.add(ids=ids, documents=texts, metadatas=metas, embeddings=self._embed_many(texts))
        flagged = [m["injection_flags"] for m in metas if m.get("injection_flags")]
        patterns = tuple(sorted({p for f in flagged for p in f.split(",")}))
        return UploadResult("indexed", doc_id, clean, ext, len(texts), pages, content_hash, len(flagged), patterns)

    # ------------------------------------------------------------------ list / delete
    def list(self, owner_id: str) -> List[DocInfo]:
        col = self._collection(owner_id)
        if col is None:
            return []
        got = col.get(include=["metadatas"])
        grouped: Dict[str, Dict[str, Any]] = {}
        for m in got["metadatas"]:
            if not m:
                continue
            g = grouped.setdefault(m["doc_id"], {"meta": m, "chunks": 0})
            g["chunks"] += 1
        out = [DocInfo(doc_id=d, filename=g["meta"].get("source_file", ""), file_type=g["meta"].get("file_type", ""),
                       chunks=g["chunks"], pages=g["meta"].get("pages"), uploaded_at=g["meta"].get("uploaded_at", ""),
                       content_hash=g["meta"].get("content_hash", "")) for d, g in grouped.items()]
        return sorted(out, key=lambda d: (d.uploaded_at, d.doc_id))

    def delete(self, owner_id: str, doc_id: str) -> DeleteResult:
        if not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id):
            raise UploadError("bad_doc_id", "document id must be 16 hex characters")
        col = self._collection(owner_id)
        if col is None:
            raise UploadError("not_found", "no such document")
        ids = col.get(where={"doc_id": doc_id})["ids"]
        if not ids:
            raise UploadError("not_found", "no such document")
        col.delete(ids=ids)                       # no longer searchable, right now
        return DeleteResult(len(ids), self._compact())

    def delete_all(self, owner_id: str) -> DeleteResult:
        col = self._collection(owner_id)
        if col is None:
            return DeleteResult(0, False)
        n = col.count()
        self._client.delete_collection(self._name(owner_id))
        return DeleteResult(n, self._compact())

    # ------------------------------------------------------------------ search
    def search(self, owner_id: str, query: str, top_k: int = 5) -> RetrievalResult:
        """Same retrieval interface and the same scope enforcement as the official corpus, on the owner's collection."""
        col = self._collection(owner_id)
        scope = RetrievalScope(corpus="upload", owner_id=validate_owner(owner_id))
        if col is None:
            return RetrievalResult(passages=[], stats={"filtered_pool": 0, "no_uploads": True})
        retr = ChromaRetriever(col, self._embed_q, max_top_k=self._cfg.max_top_k, hybrid=self._cfg.hybrid_retrieval)
        return retr.search(query, scope, top_k)
