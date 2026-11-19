"""The real sdk_engine MCP server, driven by an INDEPENDENT stdlib JSON-RPC client (no `mcp` SDK import).

How another developer can run this:   cd developer-rag && python -m pytest tests/test_mcp_protocol_independent.py -q
It builds a tiny on-disk index with a deterministic test embedder (model name "hash-test") in a subprocess,
spawns the server against it, and speaks the protocol directly. No API key, no model download, no network.
"""

import json
import subprocess
import sys

import pytest

from conftest import ENGINE_ROOT, SYNTH, _meta
from raw_mcp_client import RawMcpClient, minimal_env, tool_payload

G1, G2 = "2022-11-28", "2025-06-01"
TRACE = "0123456789abcdef0123456789abcdef"
PARENT = "feedfacecafebeef"

@pytest.fixture(scope="module")
def env_dirs(mcp_index_dir):
    return mcp_index_dir


def server_env(base, **extra):
    return minimal_env({
        "SDK_ENGINE_CHROMA_PATH": str(base / "chroma"), "SDK_ENGINE_COLLECTION": "synth",
        "SDK_ENGINE_TRACE_DIR": str(base / "traces"), "SDK_ENGINE_EMBEDDING_MODEL": "hash-test", **extra})


def spawn(base, name="srv", **extra):
    c = RawMcpClient([sys.executable, "-m", "sdk_engine.mcp_server"], server_env(base, **extra), str(ENGINE_ROOT),
                     base / f"{name}_stderr.log")
    c.initialize()
    return c


@pytest.fixture(scope="module")
def client(env_dirs):
    c = spawn(env_dirs)
    yield c
    assert c.unparsed == [], "the server wrote non-protocol text to stdout"
    c.close()


# ------------------------------------------------------------------ discovery
def test_server_identifies_itself_and_lists_exactly_the_three_tools(client):
    tools = {t["name"]: t for t in client.list_tools()}
    assert set(tools) == {"search_docs", "lookup_changes", "check_symbol"}   # nothing else: no write/delete/shell/file tool
    for name, t in tools.items():
        assert "version" in t["inputSchema"]["required"], f"{name} must require an explicit version"
        props = set(t["inputSchema"]["properties"])
        assert not props & {"path", "file", "filename", "command", "cmd", "url", "shell"}, f"{name} exposes a risky parameter"


def test_tool_schemas_carry_the_bounds(client):
    s = {t["name"]: t["inputSchema"]["properties"] for t in client.list_tools()}
    assert s["search_docs"]["top_k"]["maximum"] == 10 and s["search_docs"]["top_k"]["minimum"] == 1
    assert s["search_docs"]["query"]["maxLength"] == 1000
    assert s["check_symbol"]["symbol"]["maxLength"] == 64


# ------------------------------------------------------------------ invocation
def test_search_docs_is_version_scoped_and_cites_provenance(client):
    out = tool_payload(client.call_tool("search_docs", {"query": "create an issue assignee", "version": G1, "top_k": 5}))
    assert out["ok"] and out["version"] == G1 and out["passages"]
    assert {p["api_version"] for p in out["passages"]} == {G1}
    p = out["passages"][0]
    for field in ("chunk_id", "source_name", "api_version", "page", "content_type", "source_type"):
        assert p[field] not in (None, "")
    assert out["stats"]["where"] == {"api_version": {"$in": [G1]}}


def test_a_query_that_names_another_version_cannot_pull_it_in(client):
    out = tool_payload(client.call_tool("search_docs", {"query": "assignees array 2025-06-01 type field", "version": G1, "top_k": 10}))
    assert out["passages"] and {p["api_version"] for p in out["passages"]} == {G1}


def test_unsupported_version_is_a_structured_error_with_no_substitution(client):
    out = tool_payload(client.call_tool("search_docs", {"query": "x", "version": "2024-01-01"}))
    assert out["ok"] is False and out["error"]["category"] == "unsupported_version"
    assert set(out["error"]["available_versions"]) == {G1, G2, "2.x", "3.x"} and "passages" not in out


@pytest.mark.parametrize("args", [
    {"query": "x"},                                         # version missing
    {"query": "x", "version": G1, "top_k": 999},            # above the bound
    {"query": "x", "version": G1, "top_k": 0},
    {"query": "", "version": G1},
    {"query": "x" * 1001, "version": G1},
])
def test_schema_violations_are_rejected_by_the_server(client, args):
    assert client.call_tool("search_docs", args).get("isError") is True


def test_lookup_changes_labels_cross_version_text(client):
    out = tool_payload(client.call_tool("lookup_changes", {"version": G1, "endpoint": "create_issue"}))
    assert out["ok"] and out["caller_version"] == G1 and out["changes"]
    for c in out["changes"]:
        assert c["content_type"] == "diff" and c["applies_to_version"] == c["api_version"]
        assert c["cross_version"] == (c["api_version"] != G1)
    assert any(c["api_version"] == G2 and c["cross_version"] for c in out["changes"])   # the upgrade heads-up, labelled
    assert {c["endpoint"] for c in out["changes"]} == {"create_issue"}


def test_lookup_changes_rejects_unknown_endpoints_and_versions(client):
    bad_ep = tool_payload(client.call_tool("lookup_changes", {"version": G1, "endpoint": "format_c_drive"}))
    assert bad_ep["ok"] is False and bad_ep["error"]["category"] == "unknown_endpoint"
    bad_v = tool_payload(client.call_tool("lookup_changes", {"version": "9.9.9"}))
    assert bad_v["ok"] is False and bad_v["error"]["category"] == "unsupported_version"
    assert client.call_tool("lookup_changes", {"version": G1, "endpoint": "../../etc"}).get("isError") is True  # fails the schema pattern


def test_check_symbol_over_the_protocol(client):
    out = tool_payload(client.call_tool("check_symbol", {"version": G1, "symbol": "assignee", "endpoint": "create_issue"}))
    assert out["ok"] and out["status"] == "present_in_docs" and out["evidence"][0]["api_version"] == G1
    assert tool_payload(client.call_tool("check_symbol", {"version": G1, "symbol": "rm -rf /"}))["error"]["category"] == "bad_symbol"
    assert tool_payload(client.call_tool("check_symbol", {"version": G1, "symbol": "x", "endpoint": "nope"}))["error"]["category"] == "unknown_endpoint"


# ------------------------------------------------------------------ one trace id across the process boundary
def test_trace_context_in_request_meta_is_continued_by_the_server(client, env_dirs):
    secret_query = "ZQXPRIVATEMARKER create an issue"
    out = tool_payload(client.call_tool("search_docs", {"query": secret_query, "version": G1},
                                        meta={"trace_id": TRACE, "parent_span_id": PARENT, "request_id": "req-123"}))
    assert out["trace_id"] == TRACE
    spans = [json.loads(line) for line in (env_dirs / "traces" / "server_spans.jsonl").read_text(encoding="utf-8").splitlines()]
    mine = [s for s in spans if s["trace_id"] == TRACE and s["name"] == "mcp.tool.search_docs"]
    assert mine and mine[0]["parent_span_id"] == PARENT and mine[0]["service"] == "mcp_server" and mine[0]["request_id"] == "req-123"
    assert mine[0]["attributes"]["requested_version"] == G1 and mine[0]["status"] == "ok"
    assert "ZQXPRIVATEMARKER" not in (env_dirs / "traces" / "server_spans.jsonl").read_text(encoding="utf-8")  # query text never logged


def test_missing_or_garbage_meta_never_breaks_a_call(client):
    for meta in (None, {}, {"trace_id": "not-hex"}, {"trace_id": 5}):
        out = tool_payload(client.call_tool("search_docs", {"query": "create an issue", "version": G1}, meta=meta))
        assert out["ok"] and len(out["trace_id"]) == 32     # a fresh trace was started


# ------------------------------------------------------------------ output cap
def test_output_is_capped_and_reports_truncation(env_dirs):
    # The 2022-11-28 synthetic chunks are ~100 and ~60 characters, so a cap of 100 cannot hold both.
    c = spawn(env_dirs, name="capped", SDK_ENGINE_MAX_TOOL_OUTPUT_CHARS="100")
    try:
        out = tool_payload(c.call_tool("search_docs", {"query": "create an issue release generator", "version": G1, "top_k": 10}))
        assert out["ok"] and out["truncated"] is True
        assert 0 < len(out["passages"]) < 2 and sum(len(p["text"]) for p in out["passages"]) <= 100
    finally:
        c.close()


def test_server_process_never_received_the_callers_secrets(env_dirs, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "should-not-reach-the-server")
    env = server_env(env_dirs)
    assert "GROK_API_KEY" not in env     # the env we construct for the child is explicit and minimal
