"""Find and inspect requests in the span logs (Phase 7). Standard library plus the redactor.

Reads ``spans.jsonl`` (engine + MCP client) and ``server_spans.jsonl`` (MCP server). Both carry the SAME trace id for a request.

* ``find``  scans ONLY the root ``request`` spans (one cheap substring test discards every other line before JSON parsing). The root
  span carries the request-level summary the engine writes (``graph.request_summary``, ``summary_version`` 1): version, tools,
  outcome, cost, retries, code findings, security events. Neither the question nor the answer is ever persisted, so there is
  nothing to full-text search: those summary fields ARE the index.
* ``show``  reads every span of ONE trace from both files and renders the tree across the process boundary, plus a per-stage table.
* A root written before the summary existed is still listed with the fields it has; a filter on a field it lacks does not match it.

Nothing here writes to the logs. Output passes through the redactor again (the logs are already redacted when written; this guards a
log produced by some other tool or version).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .redaction import redact_obj

CLIENT_FILE = "spans.jsonl"
SERVER_FILE = "server_spans.jsonl"
SUMMARY_VERSION = 1
_ROOT_NEEDLE = '"name": "request"'       # json.dumps' default separators: how JsonlSink writes it
MIN_FOR_OUTLIERS = 10


class TraceQueryError(Exception):
    pass


class TraceNotFound(TraceQueryError):
    pass


class AmbiguousTrace(TraceQueryError):
    pass


def _str(v: Any) -> Optional[str]:
    return v if isinstance(v, str) else None


def _int(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _strs(v: Any) -> List[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


@dataclass
class Request:
    trace_id: str
    request_id: str
    start_ts: str
    duration_ms: Optional[float]
    status: str                       # the span status: ok | error
    outcome: str                      # answered | abstain | degraded | clarify | error | unknown
    error_category: Optional[str]
    summarised: bool                  # False: a root written before the Phase 7 summary (fields below are then empty)
    corpus: Optional[str] = None
    sdk_version: Optional[str] = None
    product: Optional[str] = None
    retrieval_via: Optional[str] = None
    endpoints: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    tool_failures: int = 0
    mcp_fallbacks: int = 0
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_version: Optional[str] = None
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Optional[float] = None
    cost_status: str = "none"         # calculated | unavailable | none  (never a silent 0 for an unpriced model)
    retrieval_attempts: int = 0
    generation_attempts: int = 0
    security_events: int = 0
    code_blocks: int = 0
    code_tiers: List[str] = field(default_factory=list)
    code_hard: int = 0
    code_soft: int = 0
    code_deprecated: int = 0                 # deprecated identifiers (each is listed in the answer's footer: warned, not silent)
    code_unchecked: int = 0                  # identifiers that could not be checked, with the reasons below
    code_unchecked_reasons: List[str] = field(default_factory=list)   # no_symbol_index | lookup_unavailable | lookup_limit
    reason_category: str = ""

    @classmethod
    def from_root(cls, rec: Dict[str, Any]) -> "Request":
        a = rec.get("attributes") or {}
        cost = a.get("cost_usd")
        dur = rec.get("duration_ms")
        return cls(
            trace_id=str(rec.get("trace_id", "")), request_id=str(rec.get("request_id", "")), start_ts=str(rec.get("start_ts") or ""),
            duration_ms=float(dur) if isinstance(dur, (int, float)) and not isinstance(dur, bool) else None,
            status=str(rec.get("status") or "ok"), outcome=str(a.get("outcome") or "unknown"), error_category=_str(rec.get("error_category")),
            summarised=a.get("summary_version") == SUMMARY_VERSION,
            corpus=_str(a.get("corpus")), sdk_version=_str(a.get("sdk_version")), product=_str(a.get("product")),
            retrieval_via=_str(a.get("retrieval_via")), endpoints=_strs(a.get("endpoints")), tools=_strs(a.get("tools_called")),
            tool_failures=_int(a.get("tool_failures")),
            mcp_fallbacks=_int(a.get("mcp_fallbacks")), model=_str(a.get("model")), provider=_str(a.get("provider")),
            prompt_version=_str(a.get("prompt_version")), llm_calls=_int(a.get("llm_calls")), prompt_tokens=_int(a.get("prompt_tokens")),
            completion_tokens=_int(a.get("completion_tokens")),
            cost_usd=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
            cost_status=str(a.get("cost_status") or "none"), retrieval_attempts=_int(a.get("retrieval_attempts")),
            generation_attempts=_int(a.get("generation_attempts")), security_events=_int(a.get("security_events")),
            code_blocks=_int(a.get("code_blocks")), code_tiers=_strs(a.get("code_tiers")), code_hard=_int(a.get("code_hard")),
            code_soft=_int(a.get("code_soft")), code_deprecated=_int(a.get("code_deprecated")), code_unchecked=_int(a.get("code_unchecked")),
            code_unchecked_reasons=_strs(a.get("code_unchecked_reasons")), reason_category=str(a.get("reason_category") or ""))


@dataclass
class Scan:
    requests: List[Request]
    unreadable_lines: int
    legacy_roots: int
    path: Path


def scan_requests(directory: Path) -> Scan:
    """Every root `request` span in the client log. Lines that are not root spans are skipped without being parsed."""
    path = Path(directory) / CLIENT_FILE
    out: List[Request] = []
    bad = 0
    if path.exists():
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if _ROOT_NEEDLE not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(rec, dict) and rec.get("name") == "request":
                    out.append(Request.from_root(rec))
    return Scan(out, bad, sum(not r.summarised for r in out), path)


# ------------------------------------------------------------------------------------------------ selecting
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_REL_RE = re.compile(r"^(\d+)([smhdw])$")


def parse_when(text: str, *, now: Optional[datetime] = None) -> datetime:
    """`7d`, `12h`, `30m` (relative to now, UTC) or an ISO date / datetime (UTC unless an offset is given)."""
    t = text.strip()
    m = _REL_RE.match(t)
    if m:
        return (now or datetime.now(timezone.utc)) - timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        raise TraceQueryError(f"cannot read the time {text!r}: use 7d, 12h, 30m, 2026-11-03 or 2026-11-03T14:30") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Query:
    trace_id: Optional[str] = None            # a prefix
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    version: Optional[str] = None             # the caller's version, or any version of an explicit comparison
    tool: Optional[str] = None                # an MCP tool the request called
    status: Optional[str] = None              # an outcome (answered|abstain|degraded|clarify|error), or `error` for a failed span
    corpus: Optional[str] = None
    endpoint: Optional[str] = None            # a catalog endpoint id the question was about, e.g. create_issue
    code_tier: Optional[str] = None           # a code-validation tier seen in the request, e.g. deprecated | not_checked
    security: bool = False                    # at least one security event
    fallback: bool = False                    # at least one MCP tool call that failed and fell back to the in-process path
    has_code: bool = False                    # the answer contained at least one fenced code block
    unchecked: bool = False                   # code whose identifiers could NOT be checked (a failure to check, not a deprecation)
    min_cost_usd: Optional[float] = None
    min_ms: Optional[float] = None


def _when(r: Request) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(r.start_ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def matches(r: Request, q: Query) -> bool:
    if q.trace_id and not r.trace_id.startswith(q.trace_id):
        return False
    if q.since or q.until:
        t = _when(r)
        if t is None or (q.since and t < q.since) or (q.until and t > q.until):
            return False
    if q.version and q.version != r.sdk_version:
        return False
    if q.tool and q.tool not in r.tools:
        return False
    if q.status and not (r.outcome == q.status or (q.status == "error" and r.status == "error")):
        return False
    if q.corpus and q.corpus != r.corpus:
        return False
    if q.endpoint and q.endpoint not in r.endpoints:
        return False
    if q.code_tier and q.code_tier not in r.code_tiers:
        return False
    if q.security and r.security_events <= 0:
        return False
    if q.fallback and r.mcp_fallbacks <= 0:
        return False
    if q.has_code and r.code_blocks <= 0:
        return False
    if q.unchecked and not (r.code_unchecked > 0 or "not_checked" in r.code_tiers):
        return False
    if q.min_cost_usd is not None and not (r.cost_usd is not None and r.cost_usd >= q.min_cost_usd):
        return False
    if q.min_ms is not None and not (r.duration_ms is not None and r.duration_ms >= q.min_ms):
        return False
    return True


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    """Nearest-rank percentile (no interpolation: it is always a value that was observed)."""
    if not values:
        return None
    vals = sorted(values)
    return vals[max(1, math.ceil(p / 100 * len(vals))) - 1]


def cost_outliers(requests: Sequence[Request], factor: float = 3.0) -> Tuple[List[Request], Optional[float], str]:
    """Priced requests costing more than `factor` x the median priced request. Needs MIN_FOR_OUTLIERS priced requests: with
    fewer, 'outlier' is not meaningful and nothing is claimed."""
    priced = [r for r in requests if r.cost_status == "calculated" and r.cost_usd is not None]
    if len(priced) < MIN_FOR_OUTLIERS:
        return [], None, f"only {len(priced)} priced request(s) in this selection; outlier detection needs at least {MIN_FOR_OUTLIERS}"
    median = percentile([r.cost_usd for r in priced], 50) or 0.0
    threshold = factor * median
    return [r for r in priced if r.cost_usd > threshold], threshold, f"cost > {factor:g} x median (median ${median:.6f}, threshold ${threshold:.6f}) over {len(priced)} priced requests"


def select(requests: Sequence[Request], q: Query, *, costliest: Optional[int] = None, slowest: Optional[int] = None,
           outliers: bool = False, limit: Optional[int] = None) -> Tuple[List[Request], List[str]]:
    notes: List[str] = []
    chosen = [r for r in requests if matches(r, q)]
    if outliers:
        chosen, _, why = cost_outliers(chosen)
        notes.append(why)
    chosen.sort(key=lambda r: r.start_ts, reverse=True)           # newest first
    if costliest:
        chosen = sorted([r for r in chosen if r.cost_usd is not None], key=lambda r: r.cost_usd, reverse=True)[:costliest]
    if slowest:
        chosen = sorted([r for r in chosen if r.duration_ms is not None], key=lambda r: r.duration_ms, reverse=True)[:slowest]
    if limit:
        chosen = chosen[:limit]
    return chosen, notes


def stats(requests: Sequence[Request]) -> Dict[str, Any]:
    """Counts and nearest-rank percentiles over a selection. Cost percentiles use priced requests only; unpriced ones are counted."""
    def count(items) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for i in items:
            out[i] = out.get(i, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    lat = [r.duration_ms for r in requests if r.duration_ms is not None]
    priced = [r.cost_usd for r in requests if r.cost_status == "calculated" and r.cost_usd is not None]
    return {
        "requests": len(requests), "summarised": sum(r.summarised for r in requests),
        "outcomes": count(r.outcome for r in requests), "versions": count(r.sdk_version or "n/a" for r in requests),
        "tools": count(t for r in requests for t in r.tools), "code_tiers": count(t for r in requests for t in r.code_tiers),
        "with_security_events": sum(r.security_events > 0 for r in requests), "with_code": sum(r.code_blocks > 0 for r in requests),
        "mcp_fallbacks": sum(r.mcp_fallbacks for r in requests),
        "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95), "max": max(lat) if lat else None, "n": len(lat)},
        "cost_usd": {"p50": percentile(priced, 50), "p95": percentile(priced, 95), "total": round(sum(priced), 6) if priced else None,
                     "priced": len(priced), "unpriced": sum(r.cost_status != "calculated" for r in requests if r.llm_calls)},
        "llm_calls": sum(r.llm_calls for r in requests),
    }


# ------------------------------------------------------------------------------------------------ one trace
def load_trace(directory: Path, trace_id: str) -> List[Dict[str, Any]]:
    """All spans of one trace (an unambiguous id prefix is enough), from the client and the server log, oldest first."""
    spans: List[Dict[str, Any]] = []
    for name in (CLIENT_FILE, SERVER_FILE):
        path = Path(directory) / name
        if not path.exists():
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if trace_id not in line:           # cheap pre-filter: the id appears verbatim in every span of the trace
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and str(rec.get("trace_id", "")).startswith(trace_id):
                    spans.append(rec)
    ids = sorted({s["trace_id"] for s in spans})
    if not ids:
        raise TraceNotFound(f"no span with a trace id starting {trace_id!r} in {directory}")
    if len(ids) > 1:
        raise AmbiguousTrace(f"{trace_id!r} matches {len(ids)} traces; give more characters: " + ", ".join(i[:12] for i in ids[:5]))
    spans.sort(key=lambda s: (s.get("start_ts") or "", s.get("service") != "sdk_engine"))
    return redact_obj(spans)


_BRIEF: Dict[str, Tuple[str, ...]] = {
    "request": ("outcome", "sdk_version", "corpus", "retrieval_via", "tools_called", "llm_calls", "cost_usd", "cost_status", "reason_category"),
    "validate_request": ("question_chars", "redactions", "security_flags"),
    "select_corpus": ("corpus",),
    "resolve_version": ("status", "sdk_version", "source"),
    "route": ("intent", "specialists"),
    "retrieve": ("attempt", "via", "returned", "scope_versions", "mcp_fallback"),
    "mcp.call": ("tool", "ok", "latency_ms", "error_category"),
    "assess_sufficiency": ("sufficient", "attempts"),
    "changelog": ("via", "found", "kept"),
    "generate": ("attempt", "model", "provider", "prompt_version", "prompt_tokens", "completion_tokens", "cost_usd", "cost_status"),
    "verify_citations_and_version": ("ok", "failures", "security_failures", "code_failures"),
    "code_validation": ("blocks", "languages", "hard", "soft", "tiers", "symbol_source", "lookups"),
    "finalize": ("outcome", "citations"),
    "abstain": ("reason",),
}


def _brief_value(v: Any) -> str:
    if isinstance(v, list):
        shown = ", ".join(str(x) for x in v[:3]) + (f", +{len(v) - 3}" if len(v) > 3 else "")
        return f"[{shown}]"
    if isinstance(v, dict):
        return json.dumps(v, separators=(",", ":"))[:60]
    return str(v)[:60]


def _brief(span: Dict[str, Any]) -> str:
    a = span.get("attributes") or {}
    name = span.get("name", "")
    keys = _BRIEF.get(name) or (("tool", "requested_version", "returned", "status") if name.startswith("mcp.tool.") else tuple(a)[:4])
    return " ".join(f"{k}={_brief_value(a[k])}" for k in keys if k in a and a[k] not in (None, [], {}, ""))


def render_tree(spans: Sequence[Dict[str, Any]]) -> str:
    """The span tree across both processes. Durations are INCLUSIVE (a parent contains its children)."""
    ids = {s["span_id"] for s in spans}
    kids: Dict[Optional[str], List[Dict[str, Any]]] = {}
    for s in spans:
        parent = s.get("parent_span_id")
        kids.setdefault(parent if parent in ids else None, []).append(s)
    lines: List[str] = []

    def walk(node: Dict[str, Any], depth: int) -> None:
        dur = node.get("duration_ms")
        bad = "" if node.get("status") == "ok" else f" !{node.get('status')}:{node.get('error_category')}"
        lines.append(f"{'  ' * depth}{node.get('name')} [{node.get('service')}] "
                     f"{f'{dur:.1f} ms' if isinstance(dur, (int, float)) else '? ms'}{bad}  {_brief(node)}".rstrip())
        for child in sorted(kids.get(node["span_id"], []), key=lambda s: s.get("start_ts") or ""):
            walk(child, depth + 1)

    for top in sorted(kids.get(None, []), key=lambda s: s.get("start_ts") or ""):
        walk(top, 0)
    return "\n".join(lines)


def stage_costs(spans: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Per span name: calls, inclusive time, and API cost WITH ITS BASIS. A model call's cost is calculated from real token usage;
    an unpriced model is `unavailable` (never 0); everything else made no API call and is labelled local compute."""
    rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for s in spans:
        a = s.get("attributes") or {}
        row = rows.setdefault((s.get("name", "?"), s.get("service", "?")),
                              {"stage": s.get("name"), "service": s.get("service"), "calls": 0, "total_ms": 0.0, "cost_usd": 0.0, "statuses": set()})
        row["calls"] += 1
        row["total_ms"] += s["duration_ms"] if isinstance(s.get("duration_ms"), (int, float)) else 0.0
        if "cost_status" in a and s.get("name") == "generate":
            row["statuses"].add(a["cost_status"])
            if isinstance(a.get("cost_usd"), (int, float)):
                row["cost_usd"] += a["cost_usd"]
    out: List[Dict[str, Any]] = []
    total, unpriced = 0.0, False
    for (name, _), row in rows.items():
        if name == "generate":
            if row["statuses"] == {"calculated"}:
                cost, basis = round(row["cost_usd"], 8), "calculated: real token usage x published rate"
            else:
                cost, basis, unpriced = None, "unavailable: the model is not in the price table", True
        elif name == "request":
            cost, basis = None, "root span: contains every other stage (not additive)"
        else:
            cost, basis = 0.0, "no API charge (local compute)"
        if isinstance(cost, float) and name != "request":
            total += cost
        out.append({"stage": row["stage"], "service": row["service"], "calls": row["calls"],
                    "total_ms": round(row["total_ms"], 2), "api_cost_usd": cost, "basis": basis})
    return {"stages": out, "api_cost_usd": None if unpriced else round(total, 8),
            "note": "times are inclusive: nested stages overlap, so do not add them up"}


def trace_header(spans: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    root = next((s for s in spans if s.get("name") == "request"), None)
    a = (root or {}).get("attributes") or {}
    return {"trace_id": spans[0]["trace_id"] if spans else None, "request_id": (root or {}).get("request_id"),
            "started": (root or {}).get("start_ts"), "duration_ms": (root or {}).get("duration_ms"),
            "outcome": a.get("outcome"), "spans": len(spans), "services": sorted({s.get("service") for s in spans if s.get("service")}),
            "summarised": a.get("summary_version") == SUMMARY_VERSION}


# ------------------------------------------------------------------------------------------------ text output
def format_requests(requests: Sequence[Request]) -> str:
    rows = [("trace_id", "started (UTC)", "version", "endpoint", "outcome", "tools", "cost", "ms", "flags")]
    for r in requests:
        flags = []
        if r.security_events:
            flags.append(f"security:{r.security_events}")
        if r.code_tiers:
            flags.append("code:" + "+".join(r.code_tiers))
        if r.mcp_fallbacks:
            flags.append(f"mcp_fallback:{r.mcp_fallbacks}")
        if r.code_unchecked:
            flags.append("unchecked:" + "+".join(r.code_unchecked_reasons or ["?"]))
        if r.code_deprecated:
            flags.append(f"deprecated_warned:{r.code_deprecated}")
        if r.code_hard:
            flags.append(f"hard:{r.code_hard}")
        if not r.summarised:
            flags.append("legacy")
        cost = f"${r.cost_usd:.6f}" if r.cost_status == "calculated" and r.cost_usd is not None else (r.cost_status if r.llm_calls else "-")
        rows.append((r.trace_id[:12], r.start_ts[:19].replace("T", " "), r.sdk_version or "-", ",".join(r.endpoints) or "-", r.outcome,
                     ",".join(sorted(set(r.tools))) or "-", cost, f"{r.duration_ms:.0f}" if r.duration_ms is not None else "?", " ".join(flags) or "-"))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in rows)


FLAG_LEGEND = ("flags: code:<tiers> = code-validation tiers seen | mcp_fallback:N = N tool calls failed and the in-process path served the request | "
               "unchecked:<why> = identifiers that could NOT be checked | deprecated_warned:N = deprecated identifiers (listed in the answer's footer) | "
               "hard:N = findings that block an answer | security:N = security events | legacy = no request summary")


def timeline(requests: Sequence[Request], bucket: str = "day") -> List[Dict[str, Any]]:
    """Per-hour or per-day counts (UTC), oldest first: how an outage or a spike shows up without reading a single trace."""
    if bucket not in ("hour", "day"):
        raise TraceQueryError("bucket must be hour or day")
    width = 13 if bucket == "hour" else 10                      # '2026-10-30T14' / '2026-10-30'
    rows: Dict[str, Dict[str, Any]] = {}
    for r in requests:
        key = r.start_ts[:width]
        row = rows.setdefault(key, {"bucket": key, "requests": 0, "errors": 0, "abstain": 0, "mcp_fallback_requests": 0,
                                    "code_unchecked_requests": 0, "security_requests": 0})
        row["requests"] += 1
        row["errors"] += r.outcome == "error" or r.status == "error"
        row["abstain"] += r.outcome == "abstain"
        row["mcp_fallback_requests"] += r.mcp_fallbacks > 0
        row["code_unchecked_requests"] += r.code_unchecked > 0 or "not_checked" in r.code_tiers
        row["security_requests"] += r.security_events > 0
    return [rows[k] for k in sorted(rows)]


def as_dicts(requests: Sequence[Request]) -> List[Dict[str, Any]]:
    return [asdict(r) for r in requests]
