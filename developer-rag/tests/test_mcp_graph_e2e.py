"""End to end across the REAL process boundary: Engine (LangGraph) -> McpToolClient -> sdk_engine.mcp_server
subprocess -> tool. One trace id must survive every hop. Uses the tiny deterministic on-disk index; no key/network."""

import json

import pytest

from sdk_engine.config import EngineConfig
from sdk_engine.graph import Deps, Engine
from sdk_engine.llm import LLMResult
from sdk_engine.mcp_client import McpToolClient, default_server_config
from sdk_engine.trace import JsonlSink
from sdk_engine.versioning import VersionCatalog

V22, V25 = "2022-11-28", "2025-06-01"
CID22 = "gh:p4:spec:create-an-issue:0"


class FakeLLM:
    model = "openai/gpt-oss-120b"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.0):
        self.calls += 1
        return LLMResult(text=f"Pass assignee as a login string [{CID22}].", prompt_tokens=80, completion_tokens=12, model=self.model)


@pytest.fixture(scope="module")
def stack(mcp_index_dir):
    traces = mcp_index_dir / "e2e_traces"
    cfg = EngineConfig(chroma_path=mcp_index_dir / "chroma", official_collection="synth", trace_dir=traces, embedding_model="hash-test")
    sink = JsonlSink(traces)
    client = McpToolClient([default_server_config(cfg)], call_timeout_s=20, max_output_chars=cfg.max_tool_output_chars,
                           log_dir=traces, sink=sink, startup_timeout_s=60).start()
    llm = FakeLLM()
    engine = Engine(Deps(config=cfg, catalog=VersionCatalog.from_page_metadata(cfg.page_metadata_path),
                         retriever=None, llm=llm, sink=sink, tools=client))   # NO in-process retriever: tools are the only path
    yield engine, llm, traces
    engine.close()


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_one_trace_id_survives_the_agent_graph_the_mcp_hop_and_the_server_tool(stack):
    engine, llm, traces = stack
    res = engine.ask(f"ZQXPRIVATE On API version {V22}, how do I set the assignee when creating an issue?")

    assert res.outcome == "answered" and res.retrieval_via == "mcp" and llm.calls == 1
    assert [c.chunk_id for c in res.citations] == [CID22] and res.citations[0].api_version == V22

    client_spans = [s for s in read(traces / "spans.jsonl") if s["trace_id"] == res.trace_id]
    server_spans = [s for s in read(traces / "server_spans.jsonl") if s["trace_id"] == res.trace_id]
    assert client_spans and server_spans, "the server must have recorded spans under the CLIENT's trace id"

    names = {s["name"] for s in client_spans}
    assert {"request", "validate_request", "resolve_version", "route", "retrieve", "mcp.call", "changelog", "generate", "finalize"} <= names
    assert {s["name"] for s in server_spans} == {"mcp.tool.search_docs", "mcp.tool.lookup_changes"}

    # parent links across the process boundary: each server span hangs under the client mcp.call that caused it
    call_ids = {s["span_id"] for s in client_spans if s["name"] == "mcp.call"}
    assert len(call_ids) == 2 and all(s["parent_span_id"] in call_ids for s in server_spans)
    assert {s["service"] for s in server_spans} == {"mcp_server"} and {s["request_id"] for s in server_spans} == {res.request_id}
    # and the whole client side nests under one request span
    root = next(s for s in client_spans if s["name"] == "request")
    assert all(s["parent_span_id"] == root["span_id"] for s in client_spans if s["name"] not in {"request", "mcp.call"})

    # privacy across both processes
    for f in ("spans.jsonl", "server_spans.jsonl"):
        assert "ZQXPRIVATE" not in (traces / f).read_text(encoding="utf-8")


def test_the_server_enforced_the_version_scope_for_the_callers_version(stack):
    engine, _, traces = stack
    res = engine.ask(f"On {V22} how do I create an issue?")
    assert set(res.retrieved_chunk_ids) <= {CID22, "gh:p10:spec:create-a-release:3"}      # no 2025-06-01 chunk reached the answer
    srv = [s for s in read(traces / "server_spans.jsonl") if s["trace_id"] == res.trace_id and s["name"] == "mcp.tool.search_docs"][0]
    assert srv["attributes"]["requested_version"] == V22 and srv["status"] == "ok"
    # the upgrade heads-up arrived through lookup_changes, labelled as another version
    assert res.deprecation_notes and all(n.cross_version and n.applies_to_version == V25 for n in res.deprecation_notes)


def test_a_version_the_corpus_does_not_have_never_reaches_a_tool(stack):
    engine, llm, traces = stack
    before = len(read(traces / "server_spans.jsonl"))
    res = engine.ask("create an issue", explicit_version="2024-01-01")
    assert res.outcome == "clarify" and len(read(traces / "server_spans.jsonl")) == before   # stopped at the resolver; no tool call at all


class CodeLLM:
    model = "openai/gpt-oss-120b"

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def complete(self, messages, *, max_tokens, temperature=0.0):
        self.calls += 1
        return LLMResult(text=self.reply, prompt_tokens=80, completion_tokens=30, model=self.model)


def test_a_code_identifier_is_checked_by_the_real_check_symbol_tool_and_a_wrong_version_field_is_blocked(stack):
    """Phase 6 across the real process boundary: `type` exists only from 2025-06-01 in the fixture docs. The sample is checked by
    the discovered, allow-listed `check_symbol` tool (version enforced by the graph), under the same trace id."""
    import dataclasses

    engine, _, traces = stack
    llm = CodeLLM(f'Set it as shown [{CID22}].\n\n```json\n{{"assignee": "octocat", "type": "bug"}}\n```')
    eng = Engine(dataclasses.replace(engine.deps, llm=llm))      # same client and server process, a different answer model
    res = eng.ask(f"On API version {V22}, how do I set the assignee when creating an issue? Show the JSON body.")

    assert res.outcome == "abstain" and res.reason.startswith("unverifiable_code")
    assert "`type` does not exist in 2022-11-28" in res.answer and "documented from 2025-06-01" in res.answer
    assert res.code_validation.symbol_source == "mcp" and res.code_validation.lookups >= 1 and llm.calls == 2

    server = [s for s in read(traces / "server_spans.jsonl") if s["trace_id"] == res.trace_id and s["name"] == "mcp.tool.check_symbol"]
    assert server and all(s["status"] == "ok" for s in server)
    hit = next(s for s in server if s["attributes"]["status"] == "absent_per_docs")
    assert hit["attributes"]["requested_version"] == V22 and hit["attributes"]["endpoint"] == "create_issue"
    assert "symbol" not in hit["attributes"] and hit["attributes"]["symbol_chars"] == len("type")     # the identifier text is not logged
    client_calls = {s["span_id"] for s in read(traces / "spans.jsonl") if s["trace_id"] == res.trace_id and s["name"] == "mcp.call"}
    assert all(s["parent_span_id"] in client_calls for s in server)                                 # nested under the client's mcp.call


def test_the_trace_query_finds_the_request_by_tool_and_renders_one_tree_across_the_real_mcp_hop(stack):
    """Phase 7 across the real process boundary: the request is found by the tool it called, and `show` merges the engine's and
    the MCP server's logs into one tree in which each server span hangs under the client `mcp.call` that caused it."""
    from sdk_engine import tracequery as tq

    engine, _, traces = stack
    res = engine.ask(f"On API version {V22}, how do I set the assignee when creating an issue?")
    found, _ = tq.select(tq.scan_requests(traces).requests, tq.Query(tool="lookup_changes", version=V22))
    req = next(r for r in found if r.trace_id == res.trace_id)
    assert req.tools == ["search_docs", "lookup_changes"] and req.retrieval_via == "mcp" and req.outcome == "answered"
    assert req.tool_failures == 0 and req.mcp_fallbacks == 0 and req.llm_calls == 1

    spans = tq.load_trace(traces, res.trace_id[:12])
    assert {s["service"] for s in spans} == {"sdk_engine", "mcp_server"}
    lines = tq.render_tree(spans).splitlines()
    depth = lambda prefix: [len(ln) - len(ln.lstrip()) for ln in lines if ln.lstrip().startswith(prefix)]      # noqa: E731
    # request > node span (retrieve / changelog) > client mcp.call > server tool span: indent is two spaces per level
    assert depth("request") == [0] and depth("mcp.call") == [4, 4] and depth("mcp.tool.") == [6, 6]
    stages = {s["stage"]: s for s in tq.stage_costs(spans)["stages"]}
    assert stages["mcp.tool.search_docs"]["api_cost_usd"] == 0.0 and "no API charge" in stages["mcp.tool.search_docs"]["basis"]
