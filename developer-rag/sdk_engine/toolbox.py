"""The graph's bridge to MCP tools: decide -> validate -> call -> parse -> re-check scope.

Every call goes through the same four gates:
  1. a ToolDecision is built by RULES (a model could propose one later; it would pass the same gates);
  2. tool_policy.validate_decision checks it against the DISCOVERED, operator-authorized registry and the
     tool's JSON Schema, and the caller-owned `version` argument is ENFORCED (a decision cannot change it);
  3. the call runs under the client's timeout / output cap and carries the trace context;
  4. results are parsed into Pydantic `Passage`s and the scope is RE-CHECKED here. The server is another
     process; if it returns a chunk from the wrong version that is a security event (ScopeViolation, fail
     closed), not something to paper over by falling back.

Anything that is not a security event (unauthorized/unknown tool, timeout, transport error, malformed
result, rejected arguments) raises ToolFailure so the graph can fall back to the in-process retriever.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from .mcp_client import ToolGateway
from .retrieval import RetrievalScope, enforce_scope
from .schemas import Passage, Product, SourceType
from .symbols import SymbolFinding
from .tool_policy import ToolDecision, ToolRejected, validate_decision
from .trace import TraceContext

_PASSAGE_FIELDS = ("chunk_id", "text", "source_name", "source_type", "corpus", "api_version", "product", "page", "anchor",
                   "endpoint", "content_type", "has_diff", "score", "dense_distance", "cross_version")


class ToolFailure(Exception):
    def __init__(self, category: str, message: str = ""):
        super().__init__(message or category)
        self.category = category


def _invoke(gateway: ToolGateway, trace: TraceContext, tool: str, arguments: Dict[str, Any], enforced: Dict[str, Any],
            rationale: str) -> Dict[str, Any]:
    decision = ToolDecision(tool=tool, arguments=arguments, made_by="rule", rationale=rationale)
    try:
        spec, args = validate_decision(decision, gateway.tools(), enforced=enforced)
    except ToolRejected as rejected:
        raise ToolFailure(rejected.category, str(rejected))
    result = gateway.call(spec.name, args, trace=trace)
    if not result.ok:
        raise ToolFailure(result.error_category or "tool_error", result.error_message or "")
    return result.data or {}


def passage_from_dict(d: Dict[str, Any]) -> Passage:
    clean = {k: d[k] for k in _PASSAGE_FIELDS if k in d}
    try:
        if clean.get("product") is not None:
            clean["product"] = Product(clean["product"])
        if clean.get("source_type") is not None:
            clean["source_type"] = SourceType(clean["source_type"])
        return Passage(**clean)
    except (ValidationError, ValueError) as exc:
        raise ToolFailure("malformed_result", f"passage did not match the contract ({type(exc).__name__})")


def _parse(items: Any) -> List[Passage]:
    if not isinstance(items, list):
        raise ToolFailure("malformed_result", "passages was not a list")
    return [passage_from_dict(i) for i in items if isinstance(i, dict)]


SEARCH_TOP_K_LIMIT = 10     # the `search_docs` tool schema's maximum (mcp_server.MAX_TOP_K; a test keeps them equal)


def search_via_tools(gateway: ToolGateway, trace: TraceContext, question: str, versions: Sequence[str], top_k: int) -> List[Passage]:
    """One strictly-scoped `search_docs` call per version. A comparison is a UNION of strict calls, merged
    round-robin by rank and labelled cross_version relative to the caller's (first) version.

    `top_k` is clamped to the tool's own maximum. Found by review: the graph doubles `top_k` on a retry (up to `max_top_k` 20), and
    an argument above the schema's 10 was rejected as `invalid_arguments`, so every such request silently fell back to the in-process path."""
    top_k = max(1, min(int(top_k), SEARCH_TOP_K_LIMIT))
    per_version: List[List[Passage]] = []
    for v in versions:
        data = _invoke(gateway, trace, "search_docs", {"query": question[:1000], "top_k": top_k}, {"version": v},
                       rationale=f"retrieve official docs for {v}")
        passages = _parse(data.get("passages"))
        enforce_scope(passages, RetrievalScope(versions=(v,)))        # fail closed on a misbehaving server
        per_version.append(passages)
    merged: List[Passage] = []
    seen = set()
    for rank in range(max((len(p) for p in per_version), default=0)):
        for plist in per_version:
            if rank < len(plist) and plist[rank].chunk_id not in seen:
                seen.add(plist[rank].chunk_id)
                merged.append(plist[rank])
    if len(versions) > 1:
        merged = [p.model_copy(update={"cross_version": p.api_version != versions[0]}) for p in merged]
    return merged[:top_k]


def changes_via_tools(gateway: ToolGateway, trace: TraceContext, caller_version: str, scope: RetrievalScope,
                      endpoint: Optional[str]) -> Tuple[List[Passage], List[Dict[str, Any]]]:
    args: Dict[str, Any] = {"endpoint": endpoint} if endpoint else {}
    data = _invoke(gateway, trace, "lookup_changes", args, {"version": caller_version},
                   rationale="proactive changelog / deprecation lookup")
    passages = _parse(data.get("changes"))
    enforce_scope(passages, scope)
    facts = [f for f in (data.get("deprecations") or []) if isinstance(f, dict)]
    return passages, facts


def check_symbol_via_tools(gateway: ToolGateway, trace: TraceContext, version: str, symbol: str,
                           endpoint: Optional[str]) -> SymbolFinding:
    """One `check_symbol` call. The graph owns `version`; a finding that answers for a different version is refused (the server
    is another process: its evidence is checked, not trusted)."""
    args: Dict[str, Any] = {"symbol": symbol}
    if endpoint:
        args["endpoint"] = endpoint
    data = _invoke(gateway, trace, "check_symbol", args, {"version": version}, rationale="verify a code identifier for the caller's version")
    try:
        finding = SymbolFinding(**{k: v for k, v in data.items() if k in SymbolFinding.model_fields})
    except (ValidationError, TypeError, ValueError):
        raise ToolFailure("malformed_result", "check_symbol result did not match the contract")
    if finding.version != version or finding.symbol != symbol:
        raise ToolFailure("scope_mismatch", "check_symbol answered for a different version or symbol")
    return finding
