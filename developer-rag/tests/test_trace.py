import json

import pytest

from sdk_engine.trace import JsonlSink, TraceContext

GROQ = "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2"


def _read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_nested_spans_share_trace_id_and_link_parents(tmp_path):
    ctx = TraceContext(sink=JsonlSink(tmp_path))
    with ctx.span("request") as root:
        with ctx.span("retrieve") as child:
            with ctx.span("rerank"):
                pass
    spans = {s["name"]: s for s in ctx.spans}
    assert len({s["trace_id"] for s in ctx.spans}) == 1
    assert spans["request"]["parent_span_id"] is None
    assert spans["retrieve"]["parent_span_id"] == root.span_id
    assert spans["rerank"]["parent_span_id"] == child.span_id
    assert all(s["duration_ms"] is not None and s["status"] == "ok" for s in ctx.spans)
    assert len(_read(tmp_path / "spans.jsonl")) == 3


def test_attributes_are_redacted_before_persisting_and_in_memory(tmp_path):
    ctx = TraceContext(sink=JsonlSink(tmp_path))
    with ctx.span("generate", sdk_version="2025-06-01", note=f"oops {GROQ}", Authorization="Bearer zzz") as sp:
        sp.set(extra=f"again {GROQ}")
    persisted = (tmp_path / "spans.jsonl").read_text(encoding="utf-8")
    assert GROQ not in persisted
    assert GROQ not in json.dumps(ctx.spans)
    rec = _read(tmp_path / "spans.jsonl")[0]
    assert rec["attributes"]["sdk_version"] == "2025-06-01"  # filterable fields survive redaction
    assert rec["attributes"]["Authorization"].startswith("[REDACTED")


def test_long_attribute_is_truncated_and_marked(tmp_path):
    ctx = TraceContext(sink=JsonlSink(tmp_path))
    with ctx.span("x", prompt="p" * 10_000):
        pass
    val = _read(tmp_path / "spans.jsonl")[0]["attributes"]["prompt"]
    assert len(val) < 2200 and "truncated" in val


def test_error_is_recorded_then_reraised(tmp_path):
    ctx = TraceContext(sink=JsonlSink(tmp_path))
    with pytest.raises(ValueError):
        with ctx.span("boom"):
            raise ValueError("bad thing")
    rec = _read(tmp_path / "spans.jsonl")[0]
    assert rec["status"] == "error" and rec["error_category"] == "ValueError"
    assert rec["attributes"]["error"] == "bad thing"


def test_trace_survives_a_process_boundary_via_carrier(tmp_path):
    client = TraceContext(sink=JsonlSink(tmp_path, "client.jsonl"), service="agent")
    with client.span("mcp.call", tool="search_docs") as call:
        carrier = client.carrier()  # what the MCP client would send
        # --- "server process": reconstructs context only from the carrier ---
        server = TraceContext.from_carrier(carrier, sink=JsonlSink(tmp_path, "server.jsonl"), service="mcp_server")
        with server.span("tool.search_docs"):
            pass
    assert server.trace_id == client.trace_id
    s = server.spans[0]
    assert s["parent_span_id"] == call.span_id          # server span hangs under the client span
    assert s["service"] == "mcp_server" and server.request_id == client.request_id


@pytest.mark.parametrize("bad", [None, {}, {"trace_id": "short"}, {"trace_id": "Z" * 32}, {"trace_id": 123}, "nonsense"])
def test_invalid_carrier_starts_a_fresh_trace_without_raising(bad, tmp_path):
    carrier = bad if isinstance(bad, dict) or bad is None else None
    ctx = TraceContext.from_carrier(carrier, sink=JsonlSink(tmp_path))
    assert len(ctx.trace_id) == 32
    with ctx.span("ok"):
        pass


def test_unwritable_sink_never_breaks_the_request(tmp_path):
    blocker = tmp_path / "file_not_dir"
    blocker.write_text("x")
    ctx = TraceContext(sink=JsonlSink(blocker))  # directory path is actually a file -> OSError on write
    with ctx.span("still-works"):
        pass
    assert ctx.spans and ctx.spans[0]["name"] == "still-works"
