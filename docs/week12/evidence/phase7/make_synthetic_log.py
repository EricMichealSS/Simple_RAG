"""SYNTHETIC span logs shaped like the real ones (same span names, attributes and parent links as the live trace in
sample_trace.json), for (a) measuring how the trace query scales and (b) rehearsing the incident drill.

Everything written here is synthetic and says so: each root span carries `synthetic_log` in its attributes. Token counts are random
draws; costs are computed with the engine's real price function (sdk_engine.pricing.cost_usd), so cost = f(tokens) as in production.
A planted incident is a rehearsal device, not a historical incident.

    build(directory, n, days=14, seed=7)              n background requests spread over `days`
    plant_incident(directory, seed=7)                 an MCP-outage window plus the one planted request; returns the ground truth
"""

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine.pricing import cost_usd

MODEL = "openai/gpt-oss-120b"
PROMPT_VERSION = "sdk-answer-v1"
CATALOG = [("2022-11-28", "github_rest", ["create_issue", "create_release", "create_repository"]),
           ("2025-06-01", "github_rest", ["create_issue", "create_release", "create_repository"]),
           ("2.x", "swagger_codegen", ["cli_general", "python_generator", "java_generator"]),
           ("3.x", "swagger_codegen", ["cli_general", "python_generator", "java_generator"])]
TAG = "phase7-drill"


class Gen:
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def hex(self, bits):
        return f"{self.rng.getrandbits(bits):0{bits // 4}x}"

    def span(self, trace, req, name, parent, start, dur_ms, attrs, service="sdk_engine", status="ok", err=None):
        return {"trace_id": trace, "request_id": req, "span_id": self.hex(64), "parent_span_id": parent, "service": service, "name": name,
                "start_ts": start.isoformat(), "end_ts": (start + timedelta(milliseconds=dur_ms)).isoformat(), "duration_ms": round(dur_ms, 3),
                "status": status, "error_category": err, "attributes": attrs}

    def request(self, start, *, version=None, endpoint=None, outcome="answered", tiers=None, outage=False, security=0,
                reason="", trace=None, tokens=None):
        r = self.rng
        trace, req = trace or self.hex(128), self.hex(48)
        ver, product, eps = version and next(c for c in CATALOG if c[0] == version) or r.choice(CATALOG)
        endpoint = endpoint or r.choice(eps)
        client, server = [], []
        t = start

        def add(name, parent, dur, attrs, **kw):
            nonlocal t
            s = self.span(trace, req, name, parent, t, dur, attrs, **kw)
            t = t + timedelta(milliseconds=dur * 0.0 + 0.01)
            client.append(s)
            return s

        root = self.span(trace, req, "request", None, start, 0, {})
        rid = root["span_id"]
        add("validate_request", rid, 0.3, {"question_chars": r.randint(40, 300), "redactions": {}, "has_owner": False, "scope_hint": "auto", "security_flags": []})
        add("select_corpus", rid, 0.02, {"corpus": "official", "scope_hint": "auto", "owner_key": None})
        tools, fallbacks, failures = [], 0, 0
        generated = outcome in ("answered", "abstain")
        if outcome == "clarify":
            add("clarify", rid, 0.1, {"status": "ambiguous"})
        else:
            add("resolve_version", rid, 0.4, {"status": "resolved", "sdk_version": ver, "product": product, "source": "question"})
            add("route", rid, 0.3, {"intent": "code_help", "specialists": ["documentation_researcher"], "endpoints": [endpoint]})
            for node, tool in (("retrieve", "search_docs"), ("changelog", "lookup_changes")):
                d = r.uniform(8, 450)
                if outage:
                    d += 30000.0      # the request really waited for the tool timeout (the first drill's log did not model this: its
                                      # request durations were shorter than its own mcp.call spans, which the investigator noticed)
                n = add(node, rid, d, {"attempt": 1, "via": "local" if outage else "mcp", "returned": 5})
                if outage:
                    n["attributes"]["mcp_fallback"] = "timeout"
                    fallbacks += 1
                    failures += 1
                    add("mcp.call", n["span_id"], 30000.0, {"tool": tool, "ok": False, "error_category": "timeout"}, status="error", err="timeout")
                else:
                    tools.append(tool)
                    c = add("mcp.call", n["span_id"], n["duration_ms"] * 0.95, {"tool": tool, "ok": True, "latency_ms": n["duration_ms"] * 0.94})
                    server.append(self.span(trace, req, f"mcp.tool.{tool}", c["span_id"], t, n["duration_ms"] * 0.85,
                                            {"tool": tool, "requested_version": ver, "returned": 5}, service="mcp_server"))
            add("assess_sufficiency", rid, 0.3, {"sufficient": True, "attempts": 1})
        p_tok, c_tok = tokens or (max(300, int(r.gauss(1500, 300))), max(20, int(r.gauss(250, 120))))
        cost = cost_usd(MODEL, p_tok, c_tok) if generated else None
        if generated:
            add("generate", rid, r.uniform(900, 5000), {"attempt": 1, "model": MODEL, "provider": "groq", "prompt_version": PROMPT_VERSION,
                                                        "prompt_tokens": p_tok, "completion_tokens": c_tok, "cost_usd": cost, "cost_status": "calculated"})
            add("verify_citations_and_version", rid, 1.0, {"ok": outcome == "answered", "failures": [], "security_failures": 0, "code_failures": 0})
            if tiers:
                add("code_validation", rid, 3.0, {"blocks": 1, "languages": ["shell"], "hard": 0, "soft": 0, "tiers": sorted(tiers),
                                                  "symbol_source": "none" if "not_checked" in tiers else "mcp", "lookups": 0, "mcp_fallbacks": 2 if outage else 0})
        add("finalize" if outcome != "abstain" else "abstain", rid, 0.2, {"outcome": outcome})
        total = (t - start).total_seconds() * 1000 + (sum(s["duration_ms"] for s in client if s["name"] in ("retrieve", "changelog", "generate")))
        root["duration_ms"] = round(total, 3)
        root["end_ts"] = (start + timedelta(milliseconds=total)).isoformat()
        if outcome == "error":
            root["status"], root["error_category"] = "error", "IndexMissing"
        root["attributes"] = {
            "service_entry": "Engine.ask", "outcome": outcome, "llm_calls": 1 if generated else 0, "security_events": security,
            "synthetic_log": TAG, "summary_version": 1, "corpus": "official", "sdk_version": None if outcome == "clarify" else ver,
            "product": None if outcome == "clarify" else product, "comparison_versions": [], "scope_versions": [] if outcome == "clarify" else [ver],
            "endpoints": [] if outcome == "clarify" else [endpoint], "retrieval_via": None if outcome == "clarify" else ("local" if outage else "mcp"),
            "tools_called": tools, "tool_failures": failures, "mcp_fallbacks": fallbacks + (2 if outage and tiers else 0),
            "model": MODEL if generated else None, "provider": "groq" if generated else None, "prompt_version": PROMPT_VERSION if generated else None,
            "llm_calls": 1 if generated else 0, "prompt_tokens": p_tok if generated else 0, "completion_tokens": c_tok if generated else 0,
            "cost_usd": cost, "cost_status": "calculated" if generated else "none", "retrieval_attempts": 0 if outcome == "clarify" else 1,
            "generation_attempts": 1 if generated else 0, "code_blocks": 1 if tiers else 0, "code_tiers": sorted(tiers or []), "code_hard": 0,
            "code_soft": 0, "code_deprecated": 1 if tiers and "deprecated" in tiers else 0,
            "code_unchecked": 1 if tiers and "not_checked" in tiers else 0,
            "code_unchecked_reasons": ["lookup_unavailable"] if tiers and "not_checked" in tiers else [], "reason_category": reason}
        client.append(root)
        return client, server


def _write(directory, client, server):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, spans in (("spans.jsonl", client), ("server_spans.jsonl", server)):
        spans.sort(key=lambda s: s["start_ts"])
        with open(directory / name, "a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(s, ensure_ascii=False) + "\n" for s in spans)


def build(directory, n, days=14, seed=7, end=None):
    g = Gen(seed)
    end = end or datetime.now(timezone.utc)
    client, server = [], []
    for _ in range(n):
        start = end - timedelta(seconds=g.rng.uniform(0, days * 86400))
        roll = g.rng.random()
        outcome = "answered" if roll < 0.80 else "abstain" if roll < 0.92 else "clarify" if roll < 0.96 else "error" if roll < 0.98 else "answered"
        tiers = None
        if outcome == "answered" and g.rng.random() < 0.30:
            tiers = {"cited"} | ({"elsewhere_in_version"} if g.rng.random() < 0.08 else set()) \
                | ({"deprecated"} if g.rng.random() < 0.06 else set()) | ({"unverified"} if g.rng.random() < 0.02 else set())
        tokens = (int(g.rng.gauss(1500, 300)) * 5, 1400) if g.rng.random() < 0.01 else None            # ~1% cost outliers
        c, s = g.request(start, outcome=outcome, tiers=tiers, tokens=tokens, security=1 if g.rng.random() < 0.01 else 0,
                         reason="model_abstained" if outcome == "abstain" else "")
        client += c
        server += s
    _write(directory, client, server)


def plant_incident(directory, seed=7, end=None):
    """An MCP outage 4 days ago (about 90 minutes): requests fell back to the in-process retriever and the code check could not
    reach a symbol index, so code samples went out as `not_checked`. One request in the window is the planted 'complaint'."""
    g = Gen(seed + 1000)
    end = end or datetime.now(timezone.utc)
    day = (end - timedelta(days=4)).replace(hour=13, minute=30, second=0, microsecond=0)
    client, server, incident = [], [], None
    for k in range(12):                                                       # the outage window
        start = day + timedelta(minutes=k * 7 + g.rng.randint(0, 5))
        if k == 5:
            c, s = g.request(start, version="2025-06-01", endpoint="create_issue", outage=True, tiers={"not_checked"})
            incident = next(x for x in c if x["name"] == "request")
        else:
            ver, ep = g.rng.choice([("2022-11-28", "create_release"), ("2.x", "java_generator"), ("3.x", "cli_general"), ("2025-06-01", "create_repository")])
            c, s = g.request(start, version=ver, endpoint=ep, outage=True, tiers={"not_checked"} if k in (1, 3, 8) else None)
        client += c
        server += s
    for k in range(10):                                                       # decoys: create_issue on 2025-06-01 with code, the system WARNED
        start = end - timedelta(days=g.rng.uniform(0.2, 12))
        c, s = g.request(start, version="2025-06-01", endpoint="create_issue", tiers={"cited", "deprecated"})
        client += c
        server += s
    _write(directory, client, server)
    return {"trace_id": incident["trace_id"], "started": incident["start_ts"], "version": "2025-06-01", "endpoint": "create_issue",
            "window_start": day.isoformat(), "window_requests": 12, "decoys": 10}
