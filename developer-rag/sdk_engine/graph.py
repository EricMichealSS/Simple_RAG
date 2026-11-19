"""LangGraph orchestration of one request.

    validate -> resolve_version -+-> clarify                      (version needs the user)
                                 +-> route -+-> abstain            (not a documented topic)
                                            +-> retrieve -> [changelog] -> assess
                                                  ^                         |  sufficient
                                                  +---- insufficient, <=N --+--> generate -> verify -> finalize
                                                                            |        ^           |
                                                                insufficient, none left   +-fail,<=M-+ (else abstain)
                                                                            +--> abstain

Design rules (see docs/week12/week12_plan.md §5):
* Nodes are plain functions; this module is thin wiring. The graph could be replaced by a loop.
* The ONLY model call is `generate`. Version resolution, routing, sufficiency and verification are
  deterministic, so a normal answer costs exactly one LLM call.
* Every loop is bounded (retrieval retries, generation retries, LangGraph recursion limit).
* Fails closed: a scope violation or a missing index ends the request with an error, never an answer.
* Degrades gracefully: if no LLM is available the engine returns extractive, cited excerpts.
* The per-request TraceContext travels in LangGraph's `configurable`, not in the state, so state
  stays small and serialisable. Spans record sizes and ids, never raw question or answer text.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Protocol, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from . import answering, codecheck, injection
from .config import EngineConfig, get_api_key
from . import toolbox
from .llm import LLMClient, LLMUnavailable
from .mcp_client import ToolGateway
from .pricing import cost_usd
from .redaction import redact_with_counts
from .extract import UploadError
from .retrieval import IndexMissing, RetrievalResult, RetrievalScope, ScopeViolation, boost_by_endpoint, owner_key
from .schemas import (
    AnswerResult, Citation, DeprecationNote, Intent, Passage, RouteDecision, Usage, VersionResolution,
    VersionStatus,
)
from .trace import JsonlSink, TraceContext
from .uploads import UploadStore, validate_owner
from .versioning import VersionCatalog, detect_endpoints, is_version_sensitive, resolve_version


class Retriever(Protocol):
    def search(self, query: str, scope: RetrievalScope, top_k: int = 5) -> RetrievalResult: ...


@dataclass
class Deps:
    config: EngineConfig
    catalog: VersionCatalog
    retriever: Optional[Retriever] = None        # in-process backend; also the fallback when MCP tools fail
    llm: Optional[LLMClient] = None
    sink: Optional[JsonlSink] = None
    tools: Optional[ToolGateway] = None          # MCP tools (discovered at runtime, allowlisted); preferred when present
    uploads: Optional[UploadStore] = None        # private per-owner uploads; in-process ONLY (the shared MCP server has no user identity)
    symbols: Optional[Any] = None                # SymbolIndex-like (.check(version, symbol, endpoint)); fallback for the check_symbol tool


class GraphState(TypedDict, total=False):
    question: str
    explicit_version: Optional[str]
    redaction_counts: Dict[str, int]
    version: VersionResolution
    route: RouteDecision
    scope_versions: List[str]
    passages: List[Passage]
    deprecation_passages: List[Passage]
    retrieval_attempts: int
    top_k: int
    sufficient: bool
    assessment: str
    generation_attempts: int
    correction: str
    draft: str
    degraded: bool
    verified: bool
    citations: List[Citation]
    outcome: Literal["answered", "degraded", "clarify", "abstain", "error"]
    reason: str
    answer: str
    clarification: Optional[str]
    usage: Usage
    deprecation_notes_payload: List[Dict[str, Any]]
    deprecation_facts: List[Dict[str, Any]]
    finish_reason: Optional[str]
    canary: str
    system_prompt: str
    security: List[Dict[str, str]]
    block_retry: bool
    endpoints: List[str]
    code_report: Any
    retrieval_via: str
    owner_id: Optional[str]
    scope_hint: str
    corpus: str


MAX_DEPRECATION_NOTES = 6
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CHANGE_RE = re.compile(r"\b(migrat\w*|upgrad\w*|deprecat\w*|replac\w*|chang\w*|differen\w*|remov\w*|renam\w*|breaking|new in)\b", re.I)
_UPLOAD_RE = re.compile(
    r"\b(?:my|our)\s+(?:uploaded\s+|attached\s+)?(?:document|documents|doc|docs|file|files|pdf|pdfs|upload|uploads|notes)\b"
    r"|\b(?:uploaded|attached)\s+(?:document|documents|doc|docs|file|files|pdf|pdfs)\b", re.I)
_CODE_RE = re.compile(r"```|\b(code|example|snippet|sample|curl|payload|implement)\b", re.I)


def _trace(config) -> TraceContext:
    return config["configurable"]["trace"]


def _fail(reason: str) -> Dict[str, Any]:
    return {"outcome": "error", "reason": reason}


SUMMARY_VERSION = 1


def request_summary(state: Dict[str, Any], spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The request-level fields written onto the root `request` span (contract: `summary_version` 1), so a request can be found
    by version, tool, outcome, cost or code finding WITHOUT reading its child spans, and without persisting the question or the
    answer. Everything here is an id, a count, a label or a number; `reason` is reduced to its category (the text before ':')."""
    calls = [s for s in spans if s["name"] == "mcp.call"]
    last_gen = next((s["attributes"] for s in reversed(spans) if s["name"] == "generate"), {})
    ver, usage, report = state.get("version"), state.get("usage") or Usage(), state.get("code_report")
    return {
        "summary_version": SUMMARY_VERSION,
        "corpus": state.get("corpus"),
        "sdk_version": getattr(ver, "version", None),
        "product": ver.product.value if ver is not None and ver.product is not None else None,
        "comparison_versions": list(ver.comparison_versions) if ver is not None else [],
        "scope_versions": list(state.get("scope_versions", [])),
        "endpoints": list(state.get("endpoints", [])),          # catalog ids detected in the question (not free text)
        "retrieval_via": state.get("retrieval_via") or None,
        "tools_called": [s["attributes"].get("tool") for s in calls],
        "tool_failures": sum(1 for s in calls if s["attributes"].get("ok") is False),
        "mcp_fallbacks": sum(1 for s in spans if s["attributes"].get("mcp_fallback"))
                         + sum(int(s["attributes"].get("mcp_fallbacks") or 0) for s in spans if s["name"] == "code_validation"),
        "model": usage.model, "provider": last_gen.get("provider"), "prompt_version": last_gen.get("prompt_version"),
        "llm_calls": usage.llm_calls, "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
        "cost_usd": usage.cost_usd, "cost_status": usage.cost_status,
        "retrieval_attempts": state.get("retrieval_attempts", 0), "generation_attempts": state.get("generation_attempts", 0),
        "code_blocks": len(report.blocks) if report is not None else 0,
        "code_tiers": sorted({f.tier for b in report.blocks for f in b.findings}) if report is not None else [],
        "code_hard": report.hard if report is not None else 0, "code_soft": report.soft if report is not None else 0,
        # deprecated identifiers are always listed in the answer's footer (render_footer; tested), so this counts WARNED deprecations;
        # unchecked identifiers are listed there too, with the reason code recorded here
        "code_deprecated": sum(f.tier == "deprecated" for b in report.blocks for f in b.findings) if report is not None else 0,
        "code_unchecked": sum(f.tier == "not_checked" for b in report.blocks for f in b.findings) if report is not None else 0,
        "code_unchecked_reasons": sorted({f.reason_code for b in report.blocks for f in b.findings if f.reason_code}) if report is not None else [],
        "reason_category": (state.get("reason") or "").split(":", 1)[0][:40],
    }


def build_graph(deps: Deps):
    cfg, catalog = deps.config, deps.catalog

    def _endpoints(question: str, ver: Optional[VersionResolution]) -> List[str]:
        """Endpoints named in the question, restricted to the RESOLVED product. Alias words are ambiguous across
        products ('markdown' is both a GitHub endpoint and a Swagger Codegen flag); the version decides."""
        found = detect_endpoints(question, catalog)
        if ver is None or ver.product is None:
            return found
        return [e for e in found if catalog.endpoint_product[e] is ver.product]

    def _scan(passages: List[Passage], source: str, existing: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Scan passages for instruction-like text, mark them, and return the merged, de-duplicated event list.
        Flagged passages are KEPT (as data): legitimate docs can discuss injection, and dropping evidence is its own failure."""
        events = list(existing)
        seen = {(e['source'], e['pattern']) for e in events}
        for p in passages:
            found = injection.scan_text(p.text)
            p.injection_flags = sorted(set(p.injection_flags) | set(injection.flag_names(found)))
            for e in injection.summarize({f"{source}:{p.chunk_id}": found}):
                if (e['source'], e['pattern']) not in seen:
                    seen.add((e['source'], e['pattern']))
                    events.append(e)
            for name in p.injection_flags:   # flags that came from ingest-time scanning of uploads
                if (f"{source}:{p.chunk_id}", name) not in seen:
                    seen.add((f"{source}:{p.chunk_id}", name))
                    events.append({'source': f"{source}:{p.chunk_id}", 'pattern': name, 'severity': 'n/a', 'excerpt': '(flagged at upload)'})
        return events

    # ---------------------------------------------------------------- nodes
    def validate_request(state: GraphState, config):
        with _trace(config).span("validate_request") as sp:
            q = state.get("question")
            if not isinstance(q, str):
                return _fail("invalid_request: question must be text")
            q = _CONTROL_RE.sub(" ", q).strip()
            if not q:
                return _fail("invalid_request: empty question")
            if len(q) > cfg.max_question_chars:
                return _fail(f"invalid_request: question longer than {cfg.max_question_chars} characters")
            owner = state.get("owner_id")
            if owner is not None:
                try:
                    validate_owner(owner)
                except UploadError:
                    return _fail("invalid_request: owner id must be 1-64 characters from [A-Za-z0-9_.@-]")
            hint = state.get("scope_hint") or "auto"
            if hint not in ("auto", "official", "upload"):
                return _fail("invalid_request: scope must be auto, official or upload")
            counts = redact_with_counts(q).counts  # what WOULD be redacted if this text were logged; text itself is not logged
            user_flags = injection.scan_text(q)
            sp.set(question_chars=len(q), redactions=counts, has_owner=owner is not None, scope_hint=hint,
                   security_flags=injection.flag_names(user_flags))
            return {"question": q, "redaction_counts": counts, "retrieval_attempts": 0, "generation_attempts": 0,
                    "top_k": cfg.top_k, "usage": Usage(), "owner_id": owner, "scope_hint": hint,
                    "canary": "ref-" + secrets.token_hex(8), "security": injection.summarize({"user_input": user_flags})}

    def select_corpus(state: GraphState, config):
        """Official SDK docs or the caller's PRIVATE uploads? Decided by rules from an explicit scope or the wording.
        Uploads are never searched without an owner, and are never mixed with the official corpus."""
        with _trace(config).span("select_corpus") as sp:
            hint, owner = state["scope_hint"], state.get("owner_id")
            wants_upload = hint == "upload" or (hint == "auto" and bool(_UPLOAD_RE.search(state["question"])))
            if wants_upload and not owner:
                text = ("Your question is about your uploaded documents, but no upload scope was provided. Tell me which "
                        "owner/collection to search: uploads are private and are never searched without one.")
                sp.set(corpus="upload", needs_owner=True)
                return {"corpus": "upload", "outcome": "clarify", "answer": text, "clarification": text, "reason": "upload_scope_required"}
            corpus = "upload" if wants_upload else "official"
            sp.set(corpus=corpus, scope_hint=hint, owner_key=owner_key(owner) if owner else None)   # hashed, never the raw id
            return {"corpus": corpus}

    def resolve_version_node(state: GraphState, config):
        with _trace(config).span("resolve_version") as sp:
            res = resolve_version(state["question"], catalog=catalog, explicit=state.get("explicit_version"),
                                  config_defaults=cfg.default_versions)
            sp.set(status=res.status.value, sdk_version=res.version, product=res.product.value if res.product else None,
                   source=res.source.value, needs_clarification=res.needs_clarification,
                   comparison_versions=res.comparison_versions, confidence=res.confidence)
            return {"version": res}

    def route(state: GraphState, config):
        with _trace(config).span("route") as sp:
            q, ver = state["question"], state["version"]
            endpoints: List[str] = []
            if ver.status is not VersionStatus.RESOLVED:
                d = RouteDecision(intent=Intent.OUT_OF_SCOPE, needs_retrieval=False, confidence=0.8, made_by="rule",
                                  rationale="no documented product or version in the question")
            else:
                endpoints = _endpoints(q, ver)
                change = ver.is_comparison or bool(_CHANGE_RE.search(q))
                code = bool(_CODE_RE.search(q))
                intent = Intent.MIGRATION if change else Intent.CODE_HELP if code else Intent.LOOKUP
                # Deprecations are surfaced proactively whenever we know which endpoint is in play.
                needs_changelog = bool(endpoints) or change
                specialists = ["documentation_researcher"]
                if needs_changelog:
                    specialists.append("deprecation_migration")
                if code:
                    specialists.append("code_validation")
                d = RouteDecision(
                    intent=intent, needs_retrieval=True, needs_changelog=needs_changelog, needs_code_validation=code,
                    specialists=specialists, confidence=0.85 if endpoints else 0.55, made_by="rule",
                    rationale=f"endpoints={endpoints or 'none detected'}; change_intent={change}; code={code}",
                )
            sp.set(intent=d.intent.value, needs_changelog=d.needs_changelog, needs_code_validation=d.needs_code_validation,
                   specialists=d.specialists, confidence=d.confidence, made_by=d.made_by, endpoints=endpoints)
            return {"route": d, "endpoints": endpoints}

    def _scope_for(ver: VersionResolution) -> RetrievalScope:
        if ver.is_comparison:
            return RetrievalScope(mode="comparison", versions=tuple(ver.comparison_versions))
        return RetrievalScope(versions=(ver.version,))

    def retrieve(state: GraphState, config):
        trace = _trace(config)
        attempt = state.get("retrieval_attempts", 0) + 1
        top_k = state.get("top_k", cfg.top_k)
        if attempt > 1:
            top_k = min(cfg.max_top_k, top_k * 2)  # the only change on retry: widen, deterministically
        with trace.span("retrieve", attempt=attempt) as sp:
            scope = _scope_for(state["version"])
            raw: Optional[List[Passage]] = None
            stats: Dict[str, Any] = {}
            via = "local"
            try:
                if deps.tools is not None:
                    try:
                        raw = toolbox.search_via_tools(deps.tools, trace, state["question"], list(scope.versions), top_k)
                        via = "mcp"
                    except toolbox.ToolFailure as failure:   # not a security event: fall back to the in-process retriever
                        sp.set(mcp_fallback=failure.category, mcp_fallback_detail=str(failure)[:200])
                if raw is None:
                    if deps.retriever is None:
                        sp.set_status("error", "NoRetrievalBackend")
                        return {**_fail("no_retrieval_backend: MCP tools failed and no in-process retriever is configured"),
                                "retrieval_attempts": attempt}
                    res = deps.retriever.search(state["question"], scope, top_k)
                    raw, stats = res.passages, res.stats
            except ScopeViolation as exc:
                sp.set_status("error", "ScopeViolation")
                sp.set(security_event="scope_violation", detail=str(exc))
                return {**_fail("scope_violation: a retrieved chunk was outside the allowed version scope; answer withheld"),
                        "retrieval_attempts": attempt}
            except IndexMissing as exc:
                sp.set_status("error", "IndexMissing")
                return {**_fail(f"index_missing: {exc}"), "retrieval_attempts": attempt}
            endpoints = _endpoints(state["question"], state["version"])
            passages = boost_by_endpoint(raw, endpoints)  # boost, never filter
            events = _scan(passages, "retrieved", state.get("security", []))
            sp.set(via=via, where=stats.get("where"), filtered_pool=stats.get("filtered_pool"),
                   returned=len(passages), top_k=top_k, scope_versions=list(scope.versions),
                   chunk_ids=[p.chunk_id for p in passages], reranked=stats.get("reranked"),
                   boosted_endpoints=endpoints, security_flags=[e["pattern"] for e in events if e["source"].startswith("retrieved:")])
            return {"passages": passages, "retrieval_attempts": attempt, "top_k": top_k,
                    "scope_versions": list(scope.versions), "retrieval_via": via, "security": events}

    def route_upload(state: GraphState, config):
        with _trace(config).span("route") as sp:
            d = RouteDecision(intent=Intent.UPLOADED_DOC, needs_retrieval=True, corpus="upload", confidence=0.9, made_by="rule",
                              specialists=["user_document_researcher"],
                              rationale="the question refers to the user's uploaded documents (or scope=upload)")
            sp.set(intent=d.intent.value, specialists=d.specialists, confidence=d.confidence, made_by=d.made_by, corpus="upload")
            return {"route": d}

    def retrieve_upload(state: GraphState, config):
        attempt = state.get("retrieval_attempts", 0) + 1
        top_k = state.get("top_k", cfg.top_k)
        if attempt > 1:
            top_k = min(cfg.max_top_k, top_k * 2)
        owner = state["owner_id"]
        with _trace(config).span("retrieve_upload", attempt=attempt, owner_key=owner_key(owner)) as sp:
            if deps.uploads is None:
                sp.set_status("error", "NoUploadStore")
                return {**_fail("uploads_unavailable: no upload store is configured"), "retrieval_attempts": attempt}
            try:
                res = deps.uploads.search(owner, state["question"], top_k)
            except ScopeViolation as exc:
                sp.set_status("error", "ScopeViolation")
                sp.set(security_event="scope_violation", detail=str(exc))
                return {**_fail("scope_violation: a retrieved chunk did not belong to the requesting owner; answer withheld"),
                        "retrieval_attempts": attempt}
            except UploadError as exc:
                sp.set_status("error", "UploadError")
                return {**_fail(f"invalid_request: {exc.category}"), "retrieval_attempts": attempt}
            events = _scan(res.passages, "upload", state.get("security", []))
            sp.set(returned=len(res.passages), top_k=top_k, filtered_pool=res.stats.get("filtered_pool"),
                   no_uploads=bool(res.stats.get("no_uploads")), chunk_ids=[p.chunk_id for p in res.passages],
                   security_flags=[e["pattern"] for e in events if e["source"].startswith("upload:")])
            return {"passages": res.passages, "retrieval_attempts": attempt, "top_k": top_k, "retrieval_via": "uploads",
                    "security": events,
                    "assessment": "no uploaded documents for this owner" if res.stats.get("no_uploads") else ""}

    def changelog(state: GraphState, config):
        """Deprecation / migration findings. Cross-version text is reachable ONLY through an explicit,
        labelled comparison scope; each finding keeps the version it applies to."""
        with _trace(config).span("changelog") as sp:
            ver = state["version"]
            mine = ver.version
            ordered = list(catalog.versions[ver.product])
            newer = [v for v in ordered if ordered.index(v) > ordered.index(mine)]
            versions = tuple([mine] + newer)
            scope = (RetrievalScope(mode="comparison", versions=versions, content_types=("diff",))
                     if newer else RetrievalScope(versions=(mine,), content_types=("diff",)))
            endpoints = set(_endpoints(state["question"], ver))
            found: Optional[List[Passage]] = None
            facts: List[Dict[str, Any]] = []
            via = "local"
            try:
                if deps.tools is not None:
                    try:
                        single = next(iter(endpoints)) if len(endpoints) == 1 else None
                        found, facts = toolbox.changes_via_tools(deps.tools, _trace(config), mine, scope, single)
                        via = "mcp"
                    except toolbox.ToolFailure as failure:
                        sp.set(mcp_fallback=failure.category, mcp_fallback_detail=str(failure)[:200])
                if found is None:
                    if deps.retriever is None:
                        sp.set_status("error", "NoRetrievalBackend")
                        return _fail("no_retrieval_backend: changelog lookup has neither MCP tools nor an in-process retriever")
                    found = deps.retriever.fetch(scope, limit=50).passages if hasattr(deps.retriever, "fetch") \
                        else deps.retriever.search(state["question"], scope, cfg.top_k).passages
            except (ScopeViolation, IndexMissing) as exc:
                sp.set_status("error", type(exc).__name__)
                if isinstance(exc, ScopeViolation):
                    sp.set(security_event="scope_violation", detail=str(exc))
                return _fail(f"{type(exc).__name__.lower()}: changelog retrieval failed closed")
            keep = [p for p in found if not endpoints or p.endpoint in endpoints][:MAX_DEPRECATION_NOTES]
            facts = [f for f in facts if not endpoints or f.get("endpoint") in endpoints][:MAX_DEPRECATION_NOTES]
            sp.set(via=via, scope_versions=list(versions), found=len(found), kept=len(keep),
                   chunk_ids=[p.chunk_id for p in keep], deprecation_facts=len(facts))
            return {"deprecation_passages": keep, "deprecation_facts": facts}

    def assess(state: GraphState, config):
        with _trace(config).span("assess_sufficiency") as sp:
            passages = state.get("passages", [])
            is_upload = state.get("corpus") == "upload"
            endpoints = set() if is_upload else set(_endpoints(state["question"], state.get("version")))
            if not passages:
                ok, why = False, state.get("assessment") or "no passages in scope"
            elif endpoints:
                hit = any(p.endpoint in endpoints for p in passages)
                ok, why = hit, "an in-scope passage covers the asked endpoint" if hit else f"no in-scope passage covers {sorted(endpoints)}"
            else:
                dists = [p.dense_distance for p in passages if p.dense_distance is not None]
                best = min(dists) if dists else None
                limit = cfg.upload_max_dense_distance if is_upload else cfg.max_dense_distance
                ok = best is not None and best <= limit
                why = f"best dense distance {best} vs threshold {limit} ({'calibrated on 20 authored questions' if is_upload else 'placeholder, untuned'})"
            sp.set(sufficient=ok, why=why, attempts=state.get("retrieval_attempts", 0))
            return {"sufficient": ok, "assessment": why}

    def generate(state: GraphState, config):
        trace = _trace(config)
        attempt = state.get("generation_attempts", 0) + 1
        with trace.span("generate", attempt=attempt) as sp:
            passages = state.get("passages", [])
            ver = state.get("version")
            if deps.llm is None:
                sp.set(degraded="no llm configured")
                return {"degraded": True, "generation_attempts": attempt, "reason": "llm_unavailable: no model configured"}
            example = passages[0].chunk_id if passages else "doc-id-from-the-id-attribute"
            canary = state.get("canary", "")
            system = (answering.build_system_prompt_upload(example, canary) if state.get("corpus") == "upload"
                      else answering.build_system_prompt(ver.product, ver.version, ver.comparison_versions, example, canary))
            user = answering.build_user_prompt(state["question"], passages, cfg.max_prompt_chars)
            if state.get("correction"):
                valid = ", ".join(p.chunk_id for p in passages)   # our own ids, safe to show; lets the retry fix a mis-copied id
                user += (f"\n\nYour previous answer was rejected: {state['correction']} "
                         f"The only valid document ids are: {valid}. Fix this and answer again.")
            try:
                out = deps.llm.complete([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                        max_tokens=cfg.llm_max_tokens, temperature=0.0)
            except LLMUnavailable as exc:
                sp.set_status("error", "LLMUnavailable")
                sp.set(error=str(exc))
                return {"degraded": True, "generation_attempts": attempt, "reason": f"llm_unavailable: {exc}"}
            prev = state.get("usage") or Usage()
            c = out.cost_usd
            total_cost = None if (c is None or prev.cost_status == "unavailable") else (prev.cost_usd or 0.0) + c
            usage = Usage(
                llm_calls=prev.llm_calls + 1, prompt_tokens=prev.prompt_tokens + out.prompt_tokens,
                completion_tokens=prev.completion_tokens + out.completion_tokens, model=out.model,
                cost_usd=total_cost, cost_status="calculated" if total_cost is not None else "unavailable",
            )
            sp.set(model=out.model, provider=getattr(deps.llm, "provider", "unknown"), prompt_version=answering.PROMPT_VERSION,
                   prompt_tokens=out.prompt_tokens, completion_tokens=out.completion_tokens,
                   cost_usd=c, cost_status="calculated" if c is not None else "unavailable",
                   prompt_chars=len(system) + len(user), answer_chars=len(out.text))
            # Repaired HERE, once: code validation, citation/version/security verification and delivery all read this one text.
            return {"draft": answering.ascii_hyphens(out.text), "degraded": False, "generation_attempts": attempt, "usage": usage,
                    "finish_reason": out.finish_reason, "system_prompt": system}

    def _symbol_lookup(trace, used: Dict[str, int]):
        """Evidence for code identifiers: the MCP `check_symbol` tool when it is authorised (same gates as every tool call,
        version enforced by the graph), else the in-process index. None when neither exists."""
        mcp_ok = deps.tools is not None and any(t.name == "check_symbol" and t.authorized for t in deps.tools.tools())
        if not mcp_ok and deps.symbols is None:
            return None

        def lookup(version: str, symbol: str, endpoint: Optional[str]):
            if mcp_ok:
                try:
                    found = toolbox.check_symbol_via_tools(deps.tools, trace, version, symbol, endpoint)
                    used["mcp"] += 1
                    return found
                except toolbox.ToolFailure:
                    used["mcp_fallbacks"] += 1          # not a security event: fall back to the in-process index
            if deps.symbols is not None:
                used["local"] += 1
                return deps.symbols.check(version, symbol, endpoint)
            return None
        return lookup

    def code_validation(state: GraphState, config):
        """Static check of the draft's code blocks against the caller's version. Runs for every official-corpus answer that
        contains a fenced block, whatever the question looked like; costs nothing (and calls nothing) when there is none."""
        if not codecheck.has_code(state["draft"]):
            return None                      # no code: no span, no lookup, nothing added to the answer
        trace = _trace(config)
        ver = state["version"]
        used = {"mcp": 0, "local": 0, "mcp_fallbacks": 0}
        with trace.span("code_validation") as sp:
            report = codecheck.validate_code(
                state["draft"], version=ver.version, versions=list(ver.comparison_versions) if ver.is_comparison else [ver.version],
                passages=state.get("passages", []), lookup=_symbol_lookup(trace, used),
                question_endpoints=_endpoints(state["question"], ver))
            if report is None:
                sp.set(blocks=0)
                return None
            report.symbol_source = "mcp" if used["mcp"] else "local" if used["local"] else "none"
            sp.set(blocks=len(report.blocks), languages=[b.language for b in report.blocks], syntax=[b.syntax for b in report.blocks],
                   lookups=report.lookups, symbol_source=report.symbol_source, mcp_fallbacks=used["mcp_fallbacks"],
                   hard=report.hard, soft=report.soft, tiers=sorted({f.tier for b in report.blocks for f in b.findings}))
            return report

    def verify(state: GraphState, config):
        with _trace(config).span("verify_citations_and_version") as sp:
            ver = state.get("version")
            allowed = None if state.get("corpus") == "upload" else (list(ver.comparison_versions) if ver.is_comparison else [ver.version])
            ok, failures, cits = answering.verify_answer(state["draft"], state.get("passages", []), allowed, catalog,
                                                         truncated=state.get("finish_reason") == "length")
            sec = answering.output_safety_failures(
                state["draft"], passages=state.get("passages", []), question=state["question"],
                system_prompt=state.get("system_prompt", ""), canary=state.get("canary", ""),
                secret_values=[k for k in [get_api_key()] if k], allowed_hosts=cfg.answer_allowed_hosts,
                # strict (allowlist only) for the official corpus and whenever ANY retrieved passage looks hostile;
                # verbatim quoting of a link is allowed only for the user's own, unflagged upload
                strict_urls=state.get("corpus") != "upload" or any(p.injection_flags for p in state.get("passages", [])))
            events = list(state.get("security", []))
            for f in sec:
                events.append({"source": "answer", "pattern": f.split(":", 1)[1].strip()[:60], "severity": "high", "excerpt": "(answer withheld)"})
            code_report, code_failures = None, []
            if state.get("corpus") != "upload" and not sec and not answering.is_abstention(state["draft"]):
                code_report = code_validation(state, config)
                if code_report is not None:
                    # a SOFT finding gets one correction retry; with none left it is shown as a warning, never an abstention
                    code_failures = codecheck.failures_for(
                        code_report, retries_left=state.get("generation_attempts", 0) <= cfg.max_generation_retries)
            ok, failures = ok and not code_failures, failures + code_failures
            sp.set(ok=ok and not sec, failures=failures + sec, cited=[c.chunk_id for c in cits], allowed_versions=allowed,
                   security_failures=len(sec), code_failures=len(code_failures))
            if sec:   # withhold immediately: a leak/exfiltration attempt is not given a second try
                sp.set_status("error", "SecurityCheck")
                return {"verified": False, "correction": "; ".join(sec), "block_retry": True, "security": events, "code_report": None}
            if ok:
                return {"verified": True, "citations": cits, "correction": "", "code_report": code_report}
            return {"verified": False, "correction": "; ".join(failures), "code_report": code_report}

    def degraded_excerpts(state, *, strict_urls):
        """Excerpts that may be printed with no model in between: held to the same output checks as a generated answer."""
        shown, held = answering.safe_excerpts(
            state.get("passages", []), question=state["question"], allowed_hosts=cfg.answer_allowed_hosts,
            strict_urls=strict_urls, secret_values=[k for k in [get_api_key()] if k])
        events = list(state.get("security", [])) + [
            {"source": "degraded_excerpt", "pattern": reason.split(":", 1)[1].strip()[:60], "severity": "high",
             "excerpt": "(excerpt withheld)"} for _cid, reason in held]
        notice = [f"- ({len(held)} excerpt{'s' if len(held) != 1 else ''} withheld by a safety check)"] if held else []
        return shown, notice, events

    def finalize(state: GraphState, config):
        with _trace(config).span("finalize") as sp:
            ver = state.get("version")
            passages = state.get("passages", [])
            extra = {}
            if state.get("corpus") == "upload":
                if state.get("degraded"):
                    top, notice, events = degraded_excerpts(state, strict_urls=False)
                    lines = [f"- [{p.chunk_id}] ({p.location}) {' '.join(p.text.split())[:300]}" for p in top] + notice
                    text = ("The answer model is unavailable, so here are the closest excerpts from your uploaded documents "
                            "(they may not answer your question):\n" + "\n".join(lines))
                    cits, outcome, reason = [answering.make_citation(p) for p in top], "degraded", state.get("reason", "llm_unavailable")
                    extra = {"security": events}
                elif answering.is_abstention(state["draft"]):
                    text, cits, outcome, reason = answering.UPLOAD_ABSTAIN, [], "abstain", "model_abstained: the uploaded documents do not contain the answer"
                else:
                    text, cits, outcome, reason = ("[Your uploaded documents] " + answering.normalize_citations(state["draft"]),
                                                   state.get("citations", []), "answered", "")
                sp.set(outcome=outcome, citations=len(cits), corpus="upload")
                return {"answer": text, "citations": cits, "outcome": outcome, "reason": reason, "deprecation_notes_payload": [], **extra}
            if state.get("degraded"):
                top, notice, events = degraded_excerpts(state, strict_urls=True)
                lines = [f"- [{p.chunk_id}] ({p.api_version}) {' '.join(p.text.split())[:300]}" for p in top] + notice
                shown = " and ".join(ver.comparison_versions) if ver.is_comparison else ver.version
                text = ("The answer model is unavailable, so here are the closest official excerpts (they may not answer "
                        f"your question) for version {shown}:\n" + "\n".join(lines))
                cits = [answering.make_citation(p) for p in top]
                outcome, reason = "degraded", state.get("reason", "llm_unavailable")
                extra = {"security": events}
            elif answering.is_abstention(state["draft"]):
                text, cits, outcome, reason = answering.ABSTAIN_PHRASE, [], "abstain", "model_abstained: documents do not contain the answer"
            else:
                label = answering.PRODUCT_LABEL.get(ver.product, "API")
                banner = f"[{label} {ver.version}] "
                text, cits, outcome, reason = banner + answering.normalize_citations(state["draft"]), state.get("citations", []), "answered", ""
                report = state.get("code_report")
                if report is not None and report.blocks:    # deterministic, model-free summary of what was and was not checked
                    text += "\n\n" + codecheck.render_footer(report)
            notes = []
            for p in state.get("deprecation_passages", []):
                notes.append(DeprecationNote(
                    caller_version=ver.version, applies_to_version=p.api_version, cross_version=p.cross_version,
                    excerpt=" ".join(p.text.split())[:690], citation=answering.make_citation(p)))
            sp.set(outcome=outcome, citations=len(cits), deprecation_notes=len(notes))
            return {"answer": text, "citations": cits, "outcome": outcome, "reason": reason,
                    "deprecation_notes_payload": [n.model_dump() for n in notes], **extra}

    def clarify(state: GraphState, config):
        with _trace(config).span("clarify") as sp:
            ver = state["version"]
            cands = ", ".join(ver.candidates) or "none"
            text = (f"Which version are you using? {ver.reason}. Documented versions: {cands}. "
                    "Tell me your version (or say you want a comparison between two) and I will answer for that version only.")
            sp.set(status=ver.status.value)
            return {"outcome": "clarify", "answer": text, "clarification": text, "reason": ver.reason}

    def abstain(state: GraphState, config):
        with _trace(config).span("abstain") as sp:
            route_d = state.get("route")
            if state.get("block_retry"):
                reason = f"blocked_by_safety_check: {state.get('correction', '')}"
                sp.set(reason=reason, security_block=True)
                return {"outcome": "abstain", "answer": "The generated answer was withheld by a safety check.", "reason": reason}
            if state.get("corpus") == "upload":
                reason = (f"unverifiable_answer: {state['correction']}" if state.get("correction")
                          else f"insufficient_evidence: {state.get('assessment', '')}")
                sp.set(reason=reason, corpus="upload")
                return {"outcome": "abstain", "answer": answering.UPLOAD_ABSTAIN, "reason": reason}
            report = state.get("code_report")
            if route_d is not None and route_d.intent is Intent.OUT_OF_SCOPE:
                reason, text = "out_of_scope", ("I can only answer questions about the documented GitHub REST API and "
                                                "Swagger Codegen versions in the indexed corpus.")
            elif codecheck.has_hard(report):
                ver = state["version"]
                reason = f"unverifiable_code: {state.get('correction', '')}"
                text = codecheck.explain_blocked(report, answering.PRODUCT_LABEL.get(ver.product, "API"), ver.version)
            elif state.get("correction"):
                reason, text = f"unverifiable_answer: {state['correction']}", answering.ABSTAIN_PHRASE
            else:
                reason, text = f"insufficient_evidence: {state.get('assessment', '')}", answering.ABSTAIN_PHRASE
            sp.set(reason=reason)
            return {"outcome": "abstain", "answer": text, "reason": reason}

    # ---------------------------------------------------------------- edges
    def after_validate(state): return "end" if state.get("outcome") == "error" else "select_corpus"
    def after_select(state):
        if state.get("outcome") == "clarify":
            return "end"
        return "route_upload" if state["corpus"] == "upload" else "resolve_version"
    def after_route_upload(state): return "retrieve_upload"
    def after_retrieve_upload(state): return "end" if state.get("outcome") == "error" else "assess"
    def after_version(state): return "clarify" if state["version"].needs_clarification else "route"
    def after_route(state): return "retrieve" if state["route"].needs_retrieval else "abstain"
    def after_retrieve(state):
        if state.get("outcome") == "error":
            return "end"
        return "changelog" if state["route"].needs_changelog else "assess"
    def after_changelog(state): return "end" if state.get("outcome") == "error" else "assess"
    def after_assess(state):
        if state["sufficient"]:
            return "generate"
        no_uploads = state.get("corpus") == "upload" and state.get("assessment") == "no uploaded documents for this owner"
        if state.get("retrieval_attempts", 0) <= cfg.max_retrieval_retries and not no_uploads:   # retrying cannot create documents
            return "retrieve_upload" if state.get("corpus") == "upload" else "retrieve"
        return "abstain"
    def after_generate(state): return "finalize" if state.get("degraded") else "verify"
    def after_verify(state):
        if state["verified"]:
            return "finalize"
        if state.get("block_retry"):
            return "abstain"
        return "generate" if state.get("generation_attempts", 0) <= cfg.max_generation_retries else "abstain"

    g = StateGraph(GraphState)
    for name, fn in [("validate_request", validate_request), ("resolve_version", resolve_version_node), ("route", route),
                     ("retrieve", retrieve), ("changelog", changelog), ("assess", assess), ("generate", generate),
                     ("verify", verify), ("finalize", finalize), ("clarify", clarify), ("abstain", abstain),
                     ("select_corpus", select_corpus), ("route_upload", route_upload), ("retrieve_upload", retrieve_upload)]:
        g.add_node(name, fn)
    g.add_edge(START, "validate_request")
    g.add_conditional_edges("validate_request", after_validate, {"end": END, "select_corpus": "select_corpus"})
    g.add_conditional_edges("select_corpus", after_select, {"end": END, "route_upload": "route_upload", "resolve_version": "resolve_version"})
    g.add_conditional_edges("route_upload", after_route_upload, {"retrieve_upload": "retrieve_upload"})
    g.add_conditional_edges("retrieve_upload", after_retrieve_upload, {"end": END, "assess": "assess"})
    g.add_conditional_edges("resolve_version", after_version, {"clarify": "clarify", "route": "route"})
    g.add_conditional_edges("route", after_route, {"retrieve": "retrieve", "abstain": "abstain"})
    g.add_conditional_edges("retrieve", after_retrieve, {"end": END, "changelog": "changelog", "assess": "assess"})
    g.add_conditional_edges("changelog", after_changelog, {"end": END, "assess": "assess"})
    g.add_conditional_edges("assess", after_assess, {"generate": "generate", "retrieve": "retrieve", "retrieve_upload": "retrieve_upload", "abstain": "abstain"})
    g.add_conditional_edges("generate", after_generate, {"finalize": "finalize", "verify": "verify"})
    g.add_conditional_edges("verify", after_verify, {"finalize": "finalize", "generate": "generate", "abstain": "abstain"})
    for terminal in ("finalize", "clarify", "abstain"):
        g.add_edge(terminal, END)
    return g.compile()


class Engine:
    """Compiled graph plus the request wrapper. One instance can serve many requests."""

    def __init__(self, deps: Deps):
        self.deps = deps
        self._graph = build_graph(deps)

    def close(self) -> None:
        """Release the MCP connection (and its server subprocess), if any."""
        closer = getattr(self.deps.tools, "close", None)
        if callable(closer):
            closer()

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def ask(self, question: str, *, explicit_version: Optional[str] = None, owner_id: Optional[str] = None,
            scope: str = "auto", trace: Optional[TraceContext] = None) -> AnswerResult:
        cfg = self.deps.config
        trace = trace or TraceContext(sink=self.deps.sink)
        t0 = time.perf_counter()
        state: Dict[str, Any] = {}
        error: Optional[str] = None
        with trace.span("request", service_entry="Engine.ask") as root:
            try:
                state = self._graph.invoke(
                    {"question": question, "explicit_version": explicit_version, "owner_id": owner_id, "scope_hint": scope},
                    config={"configurable": {"trace": trace}, "recursion_limit": cfg.max_graph_steps},
                )
            except GraphRecursionError:
                error = "step_limit: the request exceeded the maximum number of graph steps"
                root.set_status("error", "GraphRecursionError")
            except Exception as exc:  # fail closed with a sanitised reason; the span already records the class
                error = f"internal_error: {type(exc).__name__}"
                root.set_status("error", type(exc).__name__)
            outcome = "error" if error else state.get("outcome", "error")
            root.set(outcome=outcome, llm_calls=(state.get("usage") or Usage()).llm_calls,
                     security_events=len(state.get("security", [])))
            root.set(**request_summary(state, trace.spans))
            tools_fn = getattr(self.deps.tools, "tools", None)
            if callable(tools_fn):   # which discovered tools were callable, and which had instruction-like descriptions
                registry = tools_fn()
                root.set(tools_authorized=sorted(t.name for t in registry if t.authorized),
                         tools_rejected=sorted(t.name for t in registry if not t.authorized),
                         tools_suspicious_description=sorted(t.name for t in registry if t.suspicious_description))
        notes = [DeprecationNote(**n) for n in state.get("deprecation_notes_payload", [])]
        if error or outcome == "error":
            reason = error or state.get("reason", "unknown_error")
            return AnswerResult(trace_id=trace.trace_id, request_id=trace.request_id, outcome="error",
                                answer="The request could not be completed safely.", reason=reason,
                                version=state.get("version"), route=state.get("route"),
                                usage=state.get("usage") or Usage(),
                                latency_ms=round((time.perf_counter() - t0) * 1000, 2))
        return AnswerResult(
            trace_id=trace.trace_id, request_id=trace.request_id, outcome=outcome, answer=state.get("answer", ""),
            reason=state.get("reason", ""), clarification=state.get("clarification"), version=state.get("version"),
            route=state.get("route"), citations=state.get("citations", []), deprecation_notes=notes,
            deprecation_facts=state.get("deprecation_facts", []), retrieval_via=state.get("retrieval_via", ""),
            corpus=state.get("corpus", "official"), security=state.get("security", []), code_validation=state.get("code_report"),
            retrieved_chunk_ids=[p.chunk_id for p in state.get("passages", [])],
            usage=state.get("usage") or Usage(), latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )
