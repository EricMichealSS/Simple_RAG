"""The SDK-based McpToolClient against (a) a hostile fake server and (b) our real server."""

import json
import os
import sys
import threading

import pytest

from conftest import ENGINE_ROOT
from sdk_engine.config import EngineConfig
from sdk_engine.mcp_client import McpToolClient, McpUnavailable, default_server_config
from sdk_engine.tool_policy import ServerConfig
from sdk_engine.trace import JsonlSink, TraceContext

FAKE = str(ENGINE_ROOT / "tests" / "fake_mcp_server.py")
ALLOWED = ("search_docs", "slow_tool", "big_output", "not_json", "env_probe", "wrong_version")   # NOT delete_everything


def fake_server(**kw):
    return ServerConfig(name="fake", command=sys.executable, args=[FAKE], cwd=str(ENGINE_ROOT),
                        env={"PYTHONUTF8": "1"}, allowed_tools=kw.pop("allowed_tools", ALLOWED), **kw)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    c = McpToolClient([fake_server()], call_timeout_s=10, max_output_chars=50_000,
                      sink=JsonlSink(tmp_path_factory.mktemp("cl")), startup_timeout_s=60).start()
    yield c
    c.close()


def by_name(client):
    return {t.name: t for t in client.tools()}


# ------------------------------------------------------------------ discovery is not authorization
def test_tools_are_discovered_at_runtime_and_only_allowlisted_ones_are_authorized(client):
    tools = by_name(client)
    assert set(tools) == set(ALLOWED) | {"delete_everything"}          # the hostile server advertised it ...
    assert not tools["delete_everything"].authorized                    # ... but it is not callable
    assert "allowlist" in tools["delete_everything"].rejected_reason
    assert tools["delete_everything"].suspicious_description            # injection-style description flagged for telemetry
    assert all(tools[n].authorized for n in ALLOWED)


def test_an_unauthorized_tool_is_refused_without_being_sent(client):
    res = client.call("delete_everything", {"path": "/"})
    assert not res.ok and res.error_category == "unauthorized_tool" and res.server is None


def test_unknown_tool_is_refused(client):
    assert client.call("no_such_tool", {}).error_category == "unauthorized_tool"


# ------------------------------------------------------------------ bounded, never hangs the caller
def test_a_slow_tool_times_out_and_the_client_stays_usable(client):
    res = client.call("slow_tool", {"seconds": 5.0}, timeout_s=0.5)
    assert not res.ok and res.error_category == "timeout"
    ok = client.call("search_docs", {"query": "hello", "version": "2022-11-28"}, timeout_s=15)
    assert ok.ok


def test_oversized_output_is_refused_not_passed_on(tmp_path):
    with McpToolClient([fake_server()], max_output_chars=1000, sink=JsonlSink(tmp_path)) as c:
        res = c.call("big_output", {})
    assert not res.ok and res.error_category == "output_too_large" and res.truncated and res.data is None


def test_non_json_output_is_a_malformed_result(client):
    res = client.call("not_json", {})
    assert not res.ok and res.error_category == "malformed_result"


# ------------------------------------------------------------------ one trace id across the boundary
def test_trace_context_reaches_the_server_in_request_meta(client):
    trace = TraceContext(sink=client._sink)
    res = client.call("search_docs", {"query": "ZQXPRIVATE create an issue", "version": "2022-11-28"}, trace=trace)
    assert res.ok
    got = res.data["received_meta"]
    call_span = next(s for s in trace.spans if s["name"] == "mcp.call")
    assert got["trace_id"] == trace.trace_id and got["request_id"] == trace.request_id
    assert got["parent_span_id"] == call_span["span_id"]               # server work hangs under the client's call span
    # the client span records sizes and keys, never the query text
    assert call_span["attributes"]["arg_keys"] == ["query", "version"] and "ZQXPRIVATE" not in json.dumps(trace.spans)


def test_failed_calls_are_recorded_as_error_spans(client):
    trace = TraceContext(sink=client._sink)
    client.call("slow_tool", {"seconds": 5.0}, trace=trace, timeout_s=0.3)
    span = next(s for s in trace.spans if s["name"] == "mcp.call")
    assert span["status"] == "error" and span["error_category"] == "timeout"


# ------------------------------------------------------------------ least privilege
def test_the_server_subprocess_does_not_inherit_the_callers_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "must-not-reach-the-child")
    monkeypatch.setenv("SOME_OTHER_SECRET", "nor-this")
    with McpToolClient([fake_server()], sink=JsonlSink(tmp_path)) as c:
        res = c.call("env_probe", {})
    names = set(res.data["env_names"])
    assert res.ok and "GROK_API_KEY" not in names and "SOME_OTHER_SECRET" not in names
    assert "PYTHONUTF8" in names     # only what the operator configured (plus a minimal OS default) was passed


# ------------------------------------------------------------------ operator-owned configuration
def test_an_unstartable_server_raises_mcp_unavailable():
    bad = ServerConfig(name="broken", command="definitely-not-a-real-binary-xyz", allowed_tools=("search_docs",))
    with pytest.raises(McpUnavailable):
        McpToolClient([bad], startup_timeout_s=10).start()


def test_default_config_uses_the_current_interpreter_and_an_explicit_allowlist(tmp_path):
    sc = default_server_config(EngineConfig(trace_dir=tmp_path))
    assert sc.command == sys.executable and sc.args == ["-m", "sdk_engine.mcp_server"]      # not a hard-coded "python3"
    assert set(sc.allowed_tools) == {"search_docs", "lookup_changes", "check_symbol"}
    assert "GROK_API_KEY" not in sc.env


def test_two_servers_offering_the_same_tool_name_do_not_both_become_callable(tmp_path):
    a, b = fake_server(), fake_server()
    b.name = "fake2"
    with McpToolClient([a, b], sink=JsonlSink(tmp_path)) as c:
        sd = [t for t in c.tools() if t.name == "search_docs"]
    assert len(sd) == 2 and [t.authorized for t in sd] == [True, False]


def test_concurrent_calls_from_several_threads_all_complete(client):
    results, errors = [], []

    def work(i):
        try:
            results.append(client.call("search_docs", {"query": f"q{i}", "version": "2022-11-28"}, timeout_s=20).ok)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors and results == [True] * 6


def test_calling_after_close_is_a_clean_error(tmp_path):
    c = McpToolClient([fake_server()], sink=JsonlSink(tmp_path)).start()
    c.close()
    assert c.call("search_docs", {"query": "x", "version": "2022-11-28"}).error_category == "client_closed"
