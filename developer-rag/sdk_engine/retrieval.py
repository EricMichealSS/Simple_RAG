"""Version-scoped retrieval. The version restriction is a metadata filter applied in the vector
store query AND to the sparse leg's candidate pool - it is never left to the answer prompt.

Differences from the legacy ``rag_hybrid.retrieve`` (which stays untouched; see package docstring):

* ``where`` is applied to BOTH legs. Legacy: filtered queries silently dropped BM25/RRF, and the
  unfiltered BM25 index spanned every version. Here BM25 is built only over the already-filtered
  pool, so a wrong-version chunk cannot enter through the sparse leg.
* Sparse-only hits carry ``dense_distance=None`` instead of a placeholder 1.0 (the Week 5 Mode 5 bug).
* Defence in depth: after retrieval, every passage is re-checked against the scope; a mismatch
  raises :class:`ScopeViolation` (fail closed) rather than being returned.
* Missing/unknown ``api_version`` metadata can never satisfy a version filter, so such chunks are
  excluded from version-specific answers by construction.
* A missing collection is an error (``get_collection``), not a silently created empty one.

Heavy dependencies (chromadb client, sentence-transformers, cross-encoder) are only touched in the
``open_*``/``make_*`` factory functions, so everything else is unit-testable with fakes.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, model_validator

from .config import EngineConfig
from .schemas import Passage, SourceType
from .versioning import product_for_version

EmbedFn = Callable[[str], Sequence[float]]
RerankFn = Callable[[str, Sequence[Passage]], Sequence[float]]

_VALID_CONTENT_TYPES = frozenset({"spec", "diff"})
RRF_K = 60
MAX_SPARSE_POOL = 5000  # beyond this the sparse leg is skipped (and reported) rather than scanning everything


class ScopeViolation(RuntimeError):
    """A retrieved chunk fell outside the requested scope. Never swallowed, never returned."""


class IndexMissing(RuntimeError):
    pass


class RetrievalScope(BaseModel):
    """What a query is allowed to see. Immutable; validated at construction."""

    model_config = ConfigDict(frozen=True)

    corpus: Literal["official", "upload"] = "official"
    mode: Literal["strict", "comparison"] = "strict"
    versions: Tuple[str, ...] = ()
    content_types: Optional[Tuple[str, ...]] = None
    endpoints: Optional[Tuple[str, ...]] = None   # narrow to specific corpus endpoints (metadata filter)
    owner_id: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self) -> "RetrievalScope":
        if self.endpoints is not None:
            if not self.endpoints or not all(re.fullmatch(r"[a-z0-9_]{1,64}", e or "") for e in self.endpoints):
                raise ValueError("endpoints must be a non-empty tuple of [a-z0-9_] identifiers")
        if self.content_types is not None:
            bad = set(self.content_types) - _VALID_CONTENT_TYPES
            if bad or not self.content_types:
                raise ValueError(f"content_types must be a non-empty subset of {sorted(_VALID_CONTENT_TYPES)}")
        if self.corpus == "official":
            if self.owner_id is not None:
                raise ValueError("an official-corpus scope must not carry an owner_id")
            if len(set(self.versions)) != len(self.versions) or not all(self.versions):
                raise ValueError("versions must be distinct, non-empty strings")
            if self.mode == "strict" and len(self.versions) != 1:
                raise ValueError("strict scope needs exactly one version (a normal answer never mixes versions)")
            if self.mode == "comparison" and len(self.versions) < 2:
                raise ValueError("comparison scope needs at least two versions")
        else:
            if not self.owner_id or not self.owner_id.strip():
                raise ValueError("an upload scope requires an owner_id; uploads are never global")
            if self.versions:
                raise ValueError("an upload scope does not take SDK versions")
            if self.mode != "strict":
                raise ValueError("comparison mode applies to the official corpus only")
        return self


def owner_key(owner_id: str) -> str:
    """Opaque, stable key for an upload owner. The raw owner id (which may be an email) is never stored in the
    index, in metadata, or in a collection name; only this hash is."""
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]


def build_where(scope: RetrievalScope) -> Dict[str, Any]:
    """The metadata filter handed to the vector store. This IS the version / owner enforcement."""
    if scope.corpus == "upload":
        return {"owner_id": owner_key(scope.owner_id)}
    clauses: List[Dict[str, Any]] = [{"api_version": {"$in": list(scope.versions)}}]
    if scope.content_types:
        clauses.append({"content_type": {"$in": list(scope.content_types)}})
    if scope.endpoints:
        clauses.append({"endpoint": {"$in": list(scope.endpoints)}})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def upload_collection_name(prefix: str, owner_id: str) -> str:
    """Stable, filesystem-safe per-owner collection name; the raw owner id never appears in it."""
    return f"{prefix}{owner_key(owner_id)}"


# --------------------------------------------------------------------------------------------
# Sparse leg: BM25 over the FILTERED pool only
# --------------------------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class _BM25:
    def __init__(self, docs: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf: List[Dict[str, int]] = []
        self.lens: List[int] = []
        df: Dict[str, int] = {}
        for d in docs:
            toks = _tokens(d)
            counts: Dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            self.lens.append(len(toks))
            for t in counts:
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        self.avgdl = (sum(self.lens) / n) if n else 1.0
        self.idf = {t: math.log((n - f + 0.5) / (f + 0.5) + 1) for t, f in df.items()}

    def ranked(self, query: str, limit: int) -> List[int]:
        q = _tokens(query)
        scored: List[Tuple[float, int]] = []
        for i, counts in enumerate(self.tf):
            s = 0.0
            for t in q:
                f = counts.get(t)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avgdl))
            if s > 0:  # zero overlap is noise, not a ranking
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [i for _, i in scored[:limit]]


def _rrf(rank_lists: Sequence[Sequence[str]], k: int = RRF_K) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for ranks in rank_lists:
        for pos, cid in enumerate(ranks):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + pos + 1)
    return scores


# --------------------------------------------------------------------------------------------
# Retriever
# --------------------------------------------------------------------------------------------
class RetrievalResult(BaseModel):
    passages: List[Passage]
    stats: Dict[str, Any]


def _to_passage(chunk_id: str, text: str, meta: Dict[str, Any], scope: RetrievalScope, dense_distance: Optional[float]) -> Passage:
    version = meta.get("api_version")
    content_type = str(meta.get("content_type", ""))
    if scope.corpus == "upload":
        source_type = SourceType.UPLOAD
    else:
        source_type = SourceType.CHANGELOG if content_type == "diff" or meta.get("has_diff") is True else SourceType.OFFICIAL_DOC
    return Passage(
        chunk_id=chunk_id,
        text=text,
        source_name=str(meta.get("source_file", "")),
        source_type=source_type,
        corpus=scope.corpus,
        api_version=version if isinstance(version, str) else None,
        product=product_for_version(version) if isinstance(version, str) else None,
        page=meta.get("page") if isinstance(meta.get("page"), int) else None,
        anchor=str(meta.get("anchor", "")),
        endpoint=str(meta.get("endpoint", "")),
        content_type=content_type,
        has_diff=bool(meta.get("has_diff", False)),
        dense_distance=dense_distance,
        owner_id=meta.get("owner_id") if scope.corpus == "upload" else None,
        injection_flags=[f for f in str(meta.get("injection_flags", "")).split(",") if f],
        cross_version=(scope.mode == "comparison" and isinstance(version, str) and version != scope.versions[0]),
    )


def boost_by_endpoint(passages: Sequence[Passage], endpoints: Sequence[str]) -> List[Passage]:
    """Stable partition: passages for the endpoints the question is about come first, order otherwise kept.

    A boost, not a filter: nothing is dropped, so an alias false positive can never remove the right
    chunk. Measured on the golden set (docs/week12/phase2.md): hit@1 rose from 22/31 to 28/31 for
    dense retrieval and from 15/31 to 24/31 for hybrid.
    """
    wanted = set(endpoints)
    if not wanted:
        return list(passages)
    return [p for p in passages if p.endpoint in wanted] + [p for p in passages if p.endpoint not in wanted]


def enforce_scope(passages: Sequence[Passage], scope: RetrievalScope) -> None:
    """Fail closed if any passage is outside the scope. Called on every result set."""
    for p in passages:
        if p.corpus != scope.corpus:
            raise ScopeViolation(f"chunk {p.chunk_id!r} is from corpus {p.corpus!r}, scope allows {scope.corpus!r}")
        if scope.corpus == "official":
            if p.api_version not in scope.versions:
                raise ScopeViolation(
                    f"chunk {p.chunk_id!r} has api_version {p.api_version!r}, outside scope {list(scope.versions)}"
                )
        elif p.owner_id != owner_key(scope.owner_id):
            raise ScopeViolation(f"chunk {p.chunk_id!r} does not belong to the requesting owner")


class ChromaRetriever:
    """Hybrid (dense + BM25 + RRF) retrieval with the scope enforced as a metadata filter."""

    def __init__(
        self,
        collection: Any,
        embed_fn: EmbedFn,
        *,
        reranker: Optional[RerankFn] = None,
        max_top_k: int = 20,
        hybrid: bool = True,
    ):
        self._col = collection
        self._embed = embed_fn
        self._rerank = reranker
        self._max_top_k = max_top_k
        self._hybrid = hybrid

    def fetch(self, scope: RetrievalScope, limit: int = 50) -> RetrievalResult:
        """Metadata-only listing (no embedding, no ranking) for deterministic lookups such as the changelog.
        Ordered by the scope's version order, then page, then chunk id; scope is enforced exactly as in search()."""
        limit = max(1, min(int(limit), 200))
        where = build_where(scope)
        pool = self._col.get(where=where, include=["documents", "metadatas"], limit=limit)
        passages = [_to_passage(i, d, m or {}, scope, None) for i, d, m in zip(pool["ids"], pool["documents"], pool["metadatas"])]
        order = {v: n for n, v in enumerate(scope.versions)}
        passages.sort(key=lambda p: (order.get(p.api_version or "", 99), p.page or 0, p.chunk_id))
        enforce_scope(passages, scope)
        return RetrievalResult(passages=passages, stats={"where": where, "returned": len(passages), "limit": limit})

    def search(self, query: str, scope: RetrievalScope, top_k: int = 5) -> RetrievalResult:
        k = max(1, min(int(top_k), self._max_top_k))
        where = build_where(scope)
        stats: Dict[str, Any] = {
            "where": where, "requested_top_k": top_k, "top_k": k, "clamped": k != top_k,
            "hybrid": self._hybrid, "reranked": False,
        }

        pool = self._col.get(where=where, include=["documents", "metadatas"])
        pool_ids: List[str] = list(pool["ids"])
        stats["filtered_pool"] = len(pool_ids)
        if not pool_ids:
            return RetrievalResult(passages=[], stats=stats)
        docs = {i: d for i, d in zip(pool_ids, pool["documents"])}
        metas = {i: m or {} for i, m in zip(pool_ids, pool["metadatas"])}

        n_dense = min(max(k * 5, 25), len(pool_ids))
        dense = self._col.query(
            query_embeddings=[list(self._embed(query))], n_results=n_dense, where=where,
            include=["documents", "metadatas", "distances"],
        )
        dense_ids = list(dense["ids"][0])
        dense_dist = {i: d for i, d in zip(dense_ids, dense["distances"][0])}
        for i, d, m in zip(dense_ids, dense["documents"][0], dense["metadatas"][0]):
            docs.setdefault(i, d)
            metas.setdefault(i, m or {})
        stats["dense_candidates"] = len(dense_ids)

        rank_lists: List[List[str]] = [dense_ids]
        if self._hybrid:
            if len(pool_ids) <= MAX_SPARSE_POOL:
                bm25 = _BM25([docs[i] for i in pool_ids])
                sparse_ids = [pool_ids[j] for j in bm25.ranked(query, 60)]
                rank_lists.append(sparse_ids)
                stats["sparse_candidates"] = len(sparse_ids)
            else:
                stats["sparse_skipped"] = f"filtered pool {len(pool_ids)} > {MAX_SPARSE_POOL}"
        fused = _rrf(rank_lists)
        ordered = sorted(fused, key=lambda c: (-fused[c], c))[: max(k * 3, k)]

        passages = [_to_passage(c, docs[c], metas[c], scope, dense_dist.get(c)) for c in ordered]
        for p in passages:
            p.score = fused[p.chunk_id]

        if self._rerank is not None and passages:
            scores = list(self._rerank(query, passages))
            if len(scores) != len(passages):
                raise RuntimeError("reranker returned a score count different from its input")
            for p, s in zip(passages, scores):
                p.score = float(s)
            passages.sort(key=lambda p: (-(p.score or 0.0), p.chunk_id))
            stats["reranked"] = True

        passages = passages[:k]
        enforce_scope(passages, scope)  # fail closed
        stats["returned"] = len(passages)
        return RetrievalResult(passages=passages, stats=stats)


# --------------------------------------------------------------------------------------------
# Production wiring (the only functions that import heavy dependencies)
# --------------------------------------------------------------------------------------------
@lru_cache(maxsize=2)
def _sentence_model(name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name)


def _hash_embed(text: str, dim: int = 64) -> List[float]:
    """Deterministic bag-of-words embedding. TEST USE ONLY (model name "hash-test"): lets protocol and
    isolation tests build and query an index in a subprocess without downloading a model."""
    vec = [0.0] * dim
    for tok in re.findall(r"[a-z0-9_]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def make_embed_fn(model_name: str) -> EmbedFn:
    if model_name == "hash-test":
        return _hash_embed
    model = _sentence_model(model_name)
    return lambda q: model.encode([q], normalize_embeddings=True)[0].tolist()


def make_embed_many_fn(model_name: str) -> Callable[[Sequence[str]], List[List[float]]]:
    """Batch embedder for indexing (queries use make_embed_fn)."""
    if model_name == "hash-test":
        return lambda texts: [_hash_embed(t) for t in texts]
    model = _sentence_model(model_name)
    return lambda texts: model.encode(list(texts), batch_size=16, normalize_embeddings=True, show_progress_bar=False).tolist()


def make_reranker_fn(model_name: str) -> RerankFn:
    from sentence_transformers import CrossEncoder

    ce = CrossEncoder(model_name)
    return lambda q, passages: [float(s) for s in ce.predict([(q, p.text) for p in passages])]


def open_collection(config: EngineConfig, name: Optional[str] = None) -> Any:
    import chromadb

    target = name or config.official_collection
    client = chromadb.PersistentClient(path=str(config.chroma_path))
    try:
        return client.get_collection(target)
    except Exception as exc:  # chromadb raises different types across versions
        raise IndexMissing(
            f"vector collection {target!r} not found under {config.chroma_path}. "
            f"Build it first (see README: indexing)."
        ) from exc
