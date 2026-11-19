"""MCP server "sdk-docs": version-scoped documentation tools over stdio.

    python -m sdk_engine.mcp_server          (spawned by an MCP client; speaks JSON-RPC on stdin/stdout)

Tools (each REQUIRES an explicit `version`; there is no default and no "all versions" mode):
    search_docs(query, version, top_k<=10, content_types?, endpoints?)   version-scoped retrieval
    lookup_changes(version, endpoint?)                                   changelog / deprecation lookup
    check_symbol(version, symbol, endpoint?)                             corpus-backed symbol check

Safety properties (all covered by tests):
* The version restriction is a metadata filter inside the vector-store query (see retrieval.py); the
  scope is re-checked on the way out and a violation is returned as an error, never as data.
* Inputs are bounded in the tool schema itself (query length, top_k, symbol shape).
* Output is capped (`max_tool_output_chars`, per-passage truncation) and says when it was truncated.
* The server never calls an LLM and exposes no write, delete, shell or file capability.
* The caller's trace context arrives in request `_meta` and is continued here, so server-side spans
  carry the SAME trace id as the client's request.
* Errors are returned as structured `{"ok": false, "error": {...}}` with a category; messages never echo
  secrets (they describe the request's shape, not its content).
* Retrieved text is DATA. This process cannot be instructed by it: there is nothing for it to execute.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Annotated, Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from .config import EngineConfig
from .retrieval import (
    ChromaRetriever, IndexMissing, RetrievalScope, ScopeViolation, boost_by_endpoint, make_embed_fn, open_collection,
)
from .symbols import SYMBOL_RE, SymbolIndex
from .trace import JsonlSink, TraceContext
from .versioning import VersionCatalog, detect_endpoints, normalize_version

SERVER_NAME = "sdk-docs"
MAX_PASSAGE_CHARS = 1500
MAX_TOP_K = 10

_lock = threading.Lock()
_state: Dict[str, Any] = {}


def _get_state() -> Dict[str, Any]:
    """Build (once) the catalog, vector-store handle, retriever and symbol index.

    IMPORTANT (found by a hanging protocol test; see docs/week12/phase3.md): this must run BEFORE
    `mcp.run()` starts reading stdin (see `main`). Importing chromadb -> numpy for the first time inside a
    tool call, while the stdio loop has a read pending on stdin, blocked the server on Windows until more
    input arrived. The legacy Week 9 server avoided this by importing at module load."""
    with _lock:
        if not _state:
            cfg = EngineConfig.from_env()
            catalog = VersionCatalog.from_page_metadata(cfg.page_metadata_path)
            col = open_collection(cfg)
            _state.update(
                cfg=cfg, catalog=catalog, col=col,
                retriever=ChromaRetriever(col, make_embed_fn(cfg.embedding_model), max_top_k=MAX_TOP_K, hybrid=cfg.hybrid_retrieval),
                symbols=SymbolIndex.from_collection(col, catalog),
                sink=JsonlSink(cfg.trace_dir, "server_spans.jsonl"),
            )
        return _state


def _trace_from(ctx: Optional[Context], sink: JsonlSink) -> TraceContext:
    carrier: Dict[str, Any] = {}
    try:
        meta = ctx.request_context.meta if ctx is not None else None
        if meta is not None:
            extra = getattr(meta, "model_extra", None) or {}
            carrier = {k: extra.get(k) for k in ("trace_id", "parent_span_id", "request_id")}
    except Exception:  # absent/odd metadata must never break a tool call
        carrier = {}
    return TraceContext.from_carrier(carrier, sink=sink, service="mcp_server")


def _err(category: str, message: str, trace: TraceContext, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "trace_id": trace.trace_id, "error": {"category": category, "message": message, **extra}}


def _state_or_error() -> "tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]":
    """(state, None) or (None, structured error). A broken index must surface as a tool error, not a crash/hang."""
    try:
        return _get_state(), None
    except Exception as exc:  # message is the exception CLASS only: paths and details stay in stderr
        print(f"sdk-docs: state unavailable ({type(exc).__name__}: {exc})", file=sys.stderr, flush=True)
        return None, {"ok": False, "trace_id": None, "error": {
            "category": "server_unavailable", "message": f"the documentation index could not be opened ({type(exc).__name__})"}}


def _resolve(version: str, state: Dict[str, Any], trace: TraceContext):
    supported, label = normalize_version(version, state["catalog"])
    if supported is None:
        return None, _err("unsupported_version",
                          f"version {label!r} has no documentation in the corpus; no nearby version was substituted",
                          trace, available_versions=list(state["catalog"].all_versions))
    return supported, None


def _passage_dict(p, budget: List[int]) -> Optional[Dict[str, Any]]:
    text = p.text[:MAX_PASSAGE_CHARS]
    if budget[0] - len(text) < 0:
        return None
    budget[0] -= len(text)
    return {
        "chunk_id": p.chunk_id, "text": text, "text_truncated": len(p.text) > MAX_PASSAGE_CHARS,
        "source_name": p.source_name, "source_type": p.source_type.value, "corpus": p.corpus,
        "api_version": p.api_version, "product": p.product.value if p.product else None, "page": p.page,
        "anchor": p.anchor, "endpoint": p.endpoint, "content_type": p.content_type, "has_diff": p.has_diff,
        "score": p.score, "dense_distance": p.dense_distance, "cross_version": p.cross_version,
    }


mcp = FastMCP(name=SERVER_NAME)


@mcp.tool()
def search_docs(
    query: Annotated[str, Field(min_length=1, max_length=1000, description="Natural-language question or keywords.")],
    version: Annotated[str, Field(min_length=1, max_length=32, description="REQUIRED. The caller's API/SDK version, e.g. 2022-11-28 or 3.x. Results come only from this version.")],
    top_k: Annotated[int, Field(ge=1, le=MAX_TOP_K, description="Maximum passages to return (1-10).")] = 5,
    content_types: Annotated[Optional[List[Literal["spec", "diff"]]], Field(max_length=2, description="Optionally restrict to 'spec' (reference text) or 'diff' (what-changed text).")] = None,
    endpoints: Annotated[Optional[List[Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]]], Field(max_length=10, description="Optionally restrict to endpoint ids, e.g. create_issue.")] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Search the official documentation for ONE version and return cited passages.

    The version is mandatory and is enforced as a metadata filter in the vector store: passages from any
    other version are never returned. Use lookup_changes for upgrade/deprecation information."""
    state, unavailable = _state_or_error()
    if unavailable:
        return unavailable
    trace = _trace_from(ctx, state["sink"])
    with trace.span("mcp.tool.search_docs", tool="search_docs", requested_version=version, top_k=top_k,
                    query_chars=len(query)) as sp:
        supported, err = _resolve(version, state, trace)
        if err:
            sp.set_status("error", "unsupported_version")
            return err
        try:
            scope = RetrievalScope(versions=(supported,),
                                   content_types=tuple(content_types) if content_types else None,
                                   endpoints=tuple(endpoints) if endpoints else None)
            res = state["retriever"].search(query, scope, top_k)
        except ScopeViolation as exc:
            sp.set_status("error", "ScopeViolation")
            sp.set(security_event="scope_violation", detail=str(exc))
            return _err("scope_violation", "a retrieved chunk was outside the allowed version scope; result withheld", trace)
        except (IndexMissing, ValueError) as exc:
            sp.set_status("error", type(exc).__name__)
            return _err("bad_request" if isinstance(exc, ValueError) else "index_missing", type(exc).__name__, trace)
        passages = boost_by_endpoint(res.passages, detect_endpoints(query, state["catalog"]))
        budget, out, truncated = [state["cfg"].max_tool_output_chars], [], False
        for p in passages:
            d = _passage_dict(p, budget)
            if d is None:
                truncated = True
                break
            out.append(d)
        sp.set(returned=len(out), truncated=truncated, chunk_ids=[d["chunk_id"] for d in out])
        return {"ok": True, "trace_id": trace.trace_id, "version": supported, "passages": out, "truncated": truncated,
                "stats": {"filtered_pool": res.stats.get("filtered_pool"), "where": res.stats.get("where")}}


@mcp.tool()
def lookup_changes(
    version: Annotated[str, Field(min_length=1, max_length=32, description="REQUIRED. The caller's current version.")],
    endpoint: Annotated[Optional[str], Field(pattern=r"^[a-z0-9_]{1,64}$", description="Optionally restrict to one endpoint id, e.g. create_issue.")] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Changelog / deprecation lookup for a caller on `version`.

    Returns the official 'what changed' text that applies to that version AND to NEWER versions of the same
    product, each item labelled with the version it applies to and marked cross_version=true when it is about
    a newer version than the caller's (an upgrade heads-up, never the caller's own behaviour). Also returns
    explicit deprecation statements found in that text, with the replacement when the docs name one."""
    state, unavailable = _state_or_error()
    if unavailable:
        return unavailable
    trace = _trace_from(ctx, state["sink"])
    with trace.span("mcp.tool.lookup_changes", tool="lookup_changes", requested_version=version, endpoint=endpoint) as sp:
        supported, err = _resolve(version, state, trace)
        if err:
            sp.set_status("error", "unsupported_version")
            return err
        catalog = state["catalog"]
        if endpoint is not None and endpoint not in catalog.endpoint_product:
            sp.set_status("error", "unknown_endpoint")
            return _err("unknown_endpoint", f"endpoint {endpoint!r} is not documented", trace,
                        available_endpoints=sorted(catalog.endpoint_product))
        ordered = list(catalog.versions[catalog.product_of(supported)])
        versions = tuple(ordered[ordered.index(supported):])
        eps = (endpoint,) if endpoint else None
        scope = (RetrievalScope(mode="comparison", versions=versions, content_types=("diff",), endpoints=eps) if len(versions) > 1
                 else RetrievalScope(versions=versions, content_types=("diff",), endpoints=eps))
        try:
            res = state["retriever"].fetch(scope, limit=50)
        except ScopeViolation as exc:
            sp.set_status("error", "ScopeViolation")
            sp.set(security_event="scope_violation", detail=str(exc))
            return _err("scope_violation", "a stored chunk was outside the allowed scope; result withheld", trace)
        budget, changes, truncated = [state["cfg"].max_tool_output_chars], [], False
        for p in res.passages:
            d = _passage_dict(p, budget)
            if d is None:
                truncated = True
                break
            d["applies_to_version"] = p.api_version
            d["cross_version"] = p.api_version != supported
            changes.append(d)
        deprecations = []
        for v in versions:
            deprecations += [x for x in state["symbols"].deprecations_for(v, endpoint)]
        for x in deprecations:
            x["cross_version"] = x["version"] != supported
        sp.set(returned=len(changes), deprecations=len(deprecations), truncated=truncated, scope_versions=list(versions))
        return {"ok": True, "trace_id": trace.trace_id, "caller_version": supported, "changes": changes,
                "deprecations": deprecations, "truncated": truncated}


@mcp.tool()
def check_symbol(
    version: Annotated[str, Field(min_length=1, max_length=32, description="REQUIRED. The caller's version.")],
    symbol: Annotated[str, Field(min_length=1, max_length=64, description="A parameter, field, CLI flag or config key, e.g. assignee or --generate-markdown.")],
    endpoint: Annotated[Optional[str], Field(pattern=r"^[a-z0-9_]{1,64}$", description="Optionally restrict to one endpoint id.")] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Does `symbol` exist in the documentation for `version`? Corpus-backed, evidence-labelled.

    status is one of: deprecated (docs say so, with replacement), present_in_docs, absent_per_docs (the NEXT
    version's docs state it did not exist before), or not_found (weak: absence from an indexed excerpt is not
    proof it does not exist). Never reports 'removed' - the corpus does not say that."""
    state, unavailable = _state_or_error()
    if unavailable:
        return unavailable
    trace = _trace_from(ctx, state["sink"])
    with trace.span("mcp.tool.check_symbol", tool="check_symbol", requested_version=version, endpoint=endpoint,
                    symbol_chars=len(symbol)) as sp:
        supported, err = _resolve(version, state, trace)
        if err:
            sp.set_status("error", "unsupported_version")
            return err
        if not SYMBOL_RE.match(symbol):
            sp.set_status("error", "bad_symbol")
            return _err("bad_symbol", "symbol must look like an identifier, flag or config key", trace)
        if endpoint is not None and endpoint not in state["catalog"].endpoint_product:
            sp.set_status("error", "unknown_endpoint")
            return _err("unknown_endpoint", f"endpoint {endpoint!r} is not documented", trace,
                        available_endpoints=sorted(state["catalog"].endpoint_product))
        finding = state["symbols"].check(supported, symbol, endpoint)
        sp.set(status=finding.status, evidence=[e.chunk_id for e in finding.evidence])
        return {"ok": True, "trace_id": trace.trace_id, **finding.model_dump()}


def main() -> None:
    # Initialise native-heavy state BEFORE the stdio loop starts reading stdin (see _get_state). If it fails
    # the server still starts; every tool then returns a structured `server_unavailable` error.
    try:
        _get_state()
    except Exception as exc:
        print(f"sdk-docs: start-up initialisation failed ({type(exc).__name__}: {exc})", file=sys.stderr, flush=True)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
