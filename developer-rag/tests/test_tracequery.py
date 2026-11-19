"""Phase 7: find a request by time / version / tool / status / code finding / cost, and inspect one trace.

Two kinds of input: spans written by the REAL engine (so the request-summary contract is tested against what the system actually
persists) and hand-built records (time ranges, outliers, legacy roots, malformed lines, redaction)."""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from sdk_engine import answering, cli, graph, tracequery as tq
from test_graph_code import GOOD, Q, V22, WRONG_VERSION, ans, make

NOW = datetime.now(timezone.utc)
SECRET = "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2"            # assembled at run time: not a literal secret in the source


def tid(n):
    return f"{n:032x}"


def rec(n, minutes_ago=5, *, name="request", dur=100.0, status="ok", service="sdk_engine", parent=None, span_id=None, **attrs):
    start = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {"trace_id": tid(n), "request_id": tid(n)[:12], "span_id": span_id or uuid.uuid4().hex[:16], "parent_span_id": parent,
            "service": service, "name": name, "start_ts": start, "end_ts": start, "duration_ms": dur, "status": status,
            "error_category": None, "attributes": attrs}


def summary(**kw):
    base = dict(summary_version=1, outcome="answered", corpus="official", sdk_version=V22, product="github_rest", tools_called=[],
                tool_failures=0, mcp_fallbacks=0, model="openai/gpt-oss-120b", provider="groq", prompt_version="sdk-answer-v1", llm_calls=1,
                prompt_tokens=1000, completion_tokens=100, cost_usd=0.0004, cost_status="calculated", retrieval_attempts=1,
                generation_attempts=1, security_events=0, code_blocks=0, code_tiers=[], code_hard=0, code_soft=0, reason_category="")
    base.update(kw)
    return base


def write(directory, records, name="spans.jsonl"):
    with open(directory / name, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def requests_of(directory):
    return tq.scan_requests(directory).requests


# ------------------------------------------------------------------ the summary the engine really writes
@pytest.fixture(scope="module")
def real(tmp_path_factory, catalog):
    d = tmp_path_factory.mktemp("real_traces")
    out = {}
    for key, replies in {"code_ok": [ans(GOOD)], "code_wrong": [ans(WRONG_VERSION)]}.items():
        eng, _, _ = make(d, catalog, replies)
        out[key] = eng.ask(Q)
    eng, _, _ = make(d, catalog, [f"Pass assignee as a login string [gh:p4:spec:create-an-issue:0]."])
    out["plain"] = eng.ask(Q)
    out["clarify"] = eng.ask("how do I create an issue?")
    return d, out


def test_the_root_span_carries_the_request_summary_and_no_question_or_answer_text(real):
    d, res = real
    r = next(x for x in requests_of(d) if x.trace_id == res["code_ok"].trace_id)
    assert r.summarised and r.outcome == "answered" and r.status == "ok"
    assert (r.sdk_version, r.product, r.corpus) == (V22, "github_rest", "official")
    assert r.model == "openai/gpt-oss-120b" and re.fullmatch(r"sdk-answer-v\d+", str(r.prompt_version)) and r.llm_calls == 1
    assert r.cost_status == "calculated" and r.cost_usd and r.prompt_tokens > 0
    assert (r.code_blocks, r.code_tiers, r.code_hard, r.code_soft) == (1, ["cited"], 0, 0)
    assert r.endpoints == ["create_issue"]                                              # a catalog id, never question text
    assert tq.select(requests_of(d), tq.Query(endpoint="create_issue"))[0] and not tq.select(requests_of(d), tq.Query(endpoint="create_release"))[0]
    assert (r.retrieval_attempts, r.generation_attempts) == (1, 1) and r.duration_ms and r.start_ts
    raw = (d / "spans.jsonl").read_text(encoding="utf-8")
    assert "creating an issue" not in raw and "octocat" not in raw                       # neither the question nor the answer is persisted


def test_the_summary_version_in_the_engine_and_the_reader_cannot_drift():
    assert graph.SUMMARY_VERSION == tq.SUMMARY_VERSION


def test_the_generate_span_records_provider_and_prompt_version(real):
    d, res = real
    spans = tq.load_trace(d, res["plain"].trace_id)
    gen = next(s for s in spans if s["name"] == "generate")["attributes"]
    assert re.fullmatch(r"sdk-answer-v\d+", str(gen["prompt_version"])) and gen["prompt_version"] == answering.PROMPT_VERSION
    assert gen["model"] == "openai/gpt-oss-120b" and "provider" in gen
    assert gen["cost_status"] == "calculated" and gen["prompt_tokens"] > 0


def test_a_blocked_wrong_version_sample_is_findable_by_its_code_finding_and_reason_category(real):
    d, res = real
    r = next(x for x in requests_of(d) if x.trace_id == res["code_wrong"].trace_id)
    assert r.outcome == "abstain" and r.reason_category == "unverifiable_code" and r.code_hard == 1
    assert "other_version_only" in r.code_tiers and (r.retrieval_attempts, r.generation_attempts) == (1, 2)
    found, _ = tq.select(requests_of(d), tq.Query(code_tier="other_version_only"))
    assert [x.trace_id for x in found] == [r.trace_id]


def test_a_request_that_stopped_at_clarification_is_summarised_without_a_version(real):
    d, res = real
    r = next(x for x in requests_of(d) if x.trace_id == res["clarify"].trace_id)
    assert r.outcome == "clarify" and r.sdk_version is None and r.llm_calls == 0 and r.cost_status == "none"
    assert tq.select(requests_of(d), tq.Query(status="clarify"))[0][0].trace_id == r.trace_id


# ------------------------------------------------------------------ filters on hand-built records
def test_time_range_filters_accept_relative_and_iso_forms(tmp_path):
    write(tmp_path, [rec(1, 60 * 24 * 10, **summary()), rec(2, 60 * 24 * 3, **summary()), rec(3, 30, **summary())])
    reqs = requests_of(tmp_path)
    ids = lambda q: sorted(r.trace_id for r in tq.select(reqs, q)[0])                              # noqa: E731
    assert ids(tq.Query(since=tq.parse_when("7d"))) == sorted([tid(2), tid(3)])
    assert ids(tq.Query(since=tq.parse_when("1h"))) == [tid(3)]
    assert ids(tq.Query(until=tq.parse_when("5d"))) == [tid(1)]
    day = (NOW - timedelta(days=3)).date().isoformat()
    assert tid(2) in ids(tq.Query(since=tq.parse_when(day), until=tq.parse_when(day + "T23:59:59")))


def test_parse_when_rejects_nonsense_with_a_helpful_message():
    with pytest.raises(tq.TraceQueryError, match="use 7d, 12h, 30m"):
        tq.parse_when("last week")
    assert tq.parse_when("2026-11-03").tzinfo is not None


def test_filters_by_version_tool_status_corpus_security_cost_and_duration(tmp_path):
    write(tmp_path, [
        rec(1, 5, **summary(sdk_version="2025-06-01", tools_called=["search_docs", "check_symbol"], cost_usd=0.002)),
        rec(2, 6, **summary(outcome="abstain", security_events=2, dur=900)),
        rec(3, 7, **summary(corpus="upload", sdk_version=None)),
        rec(4, 8, status="error", **summary(outcome="error", cost_usd=None, cost_status="none", llm_calls=0)),
    ])
    reqs = requests_of(tmp_path)
    got = lambda **kw: sorted(r.trace_id for r in tq.select(reqs, tq.Query(**kw))[0])              # noqa: E731
    assert got(version="2025-06-01") == [tid(1)] and got(tool="check_symbol") == [tid(1)]
    assert got(status="abstain") == [tid(2)] and got(status="error") == [tid(4)]
    assert got(corpus="upload") == [tid(3)] and got(security=True) == [tid(2)]
    assert got(min_cost_usd=0.001) == [tid(1)]                       # an unpriced request never satisfies a cost filter
    assert got(trace_id=tid(3)) == [tid(3)]
    assert len(got(trace_id=tid(3)[:8])) == 4                          # a prefix matches every id that starts with it


def test_costliest_slowest_and_limit_order_the_selection(tmp_path):
    write(tmp_path, [rec(n, n, dur=100.0 * n, **summary(cost_usd=0.0001 * n)) for n in range(1, 7)])
    reqs = requests_of(tmp_path)
    assert [r.trace_id for r in tq.select(reqs, tq.Query(), costliest=2)[0]] == [tid(6), tid(5)]
    assert [r.trace_id for r in tq.select(reqs, tq.Query(), slowest=1)[0]] == [tid(6)]
    assert [r.trace_id for r in tq.select(reqs, tq.Query(), limit=3)[0]] == [tid(1), tid(2), tid(3)]       # newest first


def test_cost_outliers_need_enough_priced_requests_and_say_so(tmp_path):
    write(tmp_path, [rec(n, n, **summary(cost_usd=0.0001)) for n in range(1, 13)] + [rec(99, 1, **summary(cost_usd=0.001))])
    reqs = requests_of(tmp_path)
    out, why = tq.select(reqs, tq.Query(), outliers=True)
    assert [r.trace_id for r in out] == [tid(99)] and "median $0.000100" in why[0]
    few, why2 = tq.select(reqs[:5], tq.Query(), outliers=True)
    assert few == [] and "needs at least 10" in why2[0]


def test_unpriced_requests_are_counted_not_averaged_in_cost_stats(tmp_path):
    write(tmp_path, [rec(n, n, dur=10.0 * n, **summary(cost_usd=0.0002 * n)) for n in range(1, 11)]
          + [rec(50, 1, **summary(cost_usd=None, cost_status="unavailable"))])
    s = tq.stats(requests_of(tmp_path))
    assert s["requests"] == 11 and s["cost_usd"]["priced"] == 10 and s["cost_usd"]["unpriced"] == 1
    # latencies: 10..100 plus the unpriced request's default 100 -> 11 values; nearest-rank p50 is the 6th (60), p95 the 11th (100)
    assert s["cost_usd"]["p50"] == pytest.approx(0.001) and s["latency_ms"]["p50"] == 60.0 and s["latency_ms"]["p95"] == 100.0


def test_nearest_rank_percentile_is_always_an_observed_value():
    assert tq.percentile(list(range(1, 11)), 50) == 5 and tq.percentile(list(range(1, 11)), 95) == 10
    assert tq.percentile([7], 95) == 7 and tq.percentile([], 50) is None


def test_a_root_written_before_the_summary_existed_is_listed_but_matches_no_summary_filter(tmp_path):
    write(tmp_path, [rec(1, 5, outcome="answered", llm_calls=1, security_events=0, service_entry="Engine.ask")])
    scan = tq.scan_requests(tmp_path)
    assert scan.legacy_roots == 1 and not scan.requests[0].summarised and scan.requests[0].outcome == "answered"
    assert tq.select(scan.requests, tq.Query(version=V22))[0] == []
    assert "legacy" in tq.format_requests(scan.requests)


def test_unreadable_lines_are_counted_and_other_spans_are_never_parsed(tmp_path):
    write(tmp_path, ['{"name": "request", broken json', "not json at all", rec(1, 5, **summary()), rec(2, 5, name="generate", parent="x")])
    scan = tq.scan_requests(tmp_path)
    assert [r.trace_id for r in scan.requests] == [tid(1)] and scan.unreadable_lines == 1


def test_a_missing_log_is_an_empty_result_not_an_error(tmp_path):
    scan = tq.scan_requests(tmp_path / "nowhere")
    assert scan.requests == [] and scan.unreadable_lines == 0


# ------------------------------------------------------------------ one trace across the process boundary
def two_process_trace(directory, n=1):
    root = rec(n, 5, span_id="aaaa000000000001", dur=500.0, **summary())
    call = rec(n, 5, name="mcp.call", span_id="bbbb000000000002", parent=root["span_id"], dur=200.0, tool="search_docs", ok=True, latency_ms=190.0)
    srv = rec(n, 5, name="mcp.tool.search_docs", service="mcp_server", span_id="cccc000000000003", parent=call["span_id"], dur=150.0,
              tool="search_docs", requested_version=V22, returned=5)
    gen = rec(n, 5, name="generate", span_id="dddd000000000004", parent=root["span_id"], dur=250.0, model="m", cost_usd=0.0004, cost_status="calculated",
              prompt_tokens=900, completion_tokens=90, attempt=1)
    write(directory, [root, call, gen])
    write(directory, [srv], name="server_spans.jsonl")


def test_show_joins_the_client_and_server_logs_into_one_tree_nested_under_the_calling_span(tmp_path):
    two_process_trace(tmp_path)
    spans = tq.load_trace(tmp_path, tid(1)[:10])
    assert {s["service"] for s in spans} == {"sdk_engine", "mcp_server"}
    lines = tq.render_tree(spans).splitlines()
    depth = lambda name: next(len(ln) - len(ln.lstrip()) for ln in lines if ln.lstrip().startswith(name))      # noqa: E731
    assert depth("request") == 0 and depth("mcp.call") == 2 and depth("mcp.tool.search_docs") == 4 and depth("generate") == 2
    assert "tool=search_docs" in tq.render_tree(spans) and "model=m" in tq.render_tree(spans)


def test_show_reports_unknown_and_ambiguous_prefixes_clearly(tmp_path):
    two_process_trace(tmp_path, 1)
    two_process_trace(tmp_path, 2)
    with pytest.raises(tq.TraceNotFound):
        tq.load_trace(tmp_path, "ffff")
    with pytest.raises(tq.AmbiguousTrace, match="matches 2 traces"):
        tq.load_trace(tmp_path, "0000000000")


def test_stage_costs_label_the_basis_and_never_present_unpriced_or_local_work_as_a_measured_cost(tmp_path):
    two_process_trace(tmp_path)
    table = tq.stage_costs(tq.load_trace(tmp_path, tid(1)))
    by = {s["stage"]: s for s in table["stages"]}
    assert by["generate"]["api_cost_usd"] == pytest.approx(0.0004) and by["generate"]["basis"].startswith("calculated")
    assert by["mcp.call"]["api_cost_usd"] == 0.0 and "no API charge" in by["mcp.call"]["basis"]
    assert by["request"]["api_cost_usd"] is None and "not additive" in by["request"]["basis"]
    assert table["api_cost_usd"] == pytest.approx(0.0004) and "inclusive" in table["note"]


def test_an_unpriced_model_makes_the_request_cost_unavailable_not_zero(tmp_path):
    root = rec(1, 5, span_id="aaaa000000000001", **summary(cost_usd=None, cost_status="unavailable"))
    gen = rec(1, 5, name="generate", parent=root["span_id"], model="unknown/model", cost_usd=None, cost_status="unavailable")
    write(tmp_path, [root, gen])
    table = tq.stage_costs(tq.load_trace(tmp_path, tid(1)))
    assert table["api_cost_usd"] is None and next(s for s in table["stages"] if s["stage"] == "generate")["api_cost_usd"] is None


def test_an_error_span_is_marked_in_the_tree(tmp_path):
    root = rec(1, 5, span_id="aaaa000000000001", **summary())
    bad = rec(1, 5, name="retrieve", parent=root["span_id"], status="error")
    bad["error_category"] = "ScopeViolation"
    write(tmp_path, [root, bad])
    assert "!error:ScopeViolation" in tq.render_tree(tq.load_trace(tmp_path, tid(1)))


def test_what_is_shown_is_redacted_again_even_if_the_log_was_not(tmp_path):
    root = rec(1, 5, span_id="aaaa000000000001", **summary())
    leaky = rec(1, 5, name="retrieve", parent=root["span_id"], note=f"the key is {SECRET} and mail alice@example.com")
    write(tmp_path, [root, leaky])
    shown = json.dumps(tq.load_trace(tmp_path, tid(1)))
    assert SECRET not in shown and "alice@example.com" not in shown
    assert SECRET in (tmp_path / "spans.jsonl").read_text(encoding="utf-8")          # the file itself is never rewritten


# ------------------------------------------------------------------ the CLI
def run(capsys, *argv):
    code = cli.main(["trace", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_find_prints_a_table_and_counts_what_it_scanned(real, capsys):
    d, res = real
    code, out, _ = run(capsys, "--dir", str(d), "find", "--status", "abstain")
    assert code == 0 and "1 request(s) shown of 4 scanned" in out
    assert res["code_wrong"].trace_id[:12] in out and "code:other_version_only" in out and "hard:1" in out


def test_cli_find_json_is_machine_readable_and_the_dir_may_follow_the_action(real, capsys):
    d, res = real
    code, out, _ = run(capsys, "find", "--dir", str(d), "--code-tier", "cited", "--json")
    data = json.loads(out)
    assert code == 0 and data["scanned"] == 4 and data["matched"] == 1 and data["requests"][0]["trace_id"] == res["code_ok"].trace_id


def test_cli_find_with_no_match_says_so_and_still_exits_zero(real, capsys):
    d, _ = real
    code, out, _ = run(capsys, "--dir", str(d), "find", "--version", "9.9.9")
    assert code == 0 and "no matching requests" in out


def test_cli_show_renders_the_tree_the_stage_table_and_the_api_cost(real, capsys):
    d, res = real
    code, out, _ = run(capsys, "--dir", str(d), "show", res["code_ok"].trace_id[:10])
    assert code == 0 and "request [sdk_engine]" in out and "code_validation" in out and "Stages (times are inclusive" in out
    assert "calculated: real token usage x published rate" in out and "API cost of this request: $" in out


def test_cli_show_json_and_errors(real, capsys):
    d, res = real
    code, out, _ = run(capsys, "--dir", str(d), "show", res["plain"].trace_id, "--json")
    data = json.loads(out)
    assert code == 0 and data["header"]["outcome"] == "answered" and data["spans"] and data["stages"]["stages"]
    code, _, err = run(capsys, "--dir", str(d), "show", "zzzz")
    assert code == 2 and "no span with a trace id starting" in err
    code, _, err = run(capsys, "--dir", str(d), "find", "--since", "yesterday-ish")
    assert code == 2 and "cannot read the time" in err


def test_cli_stats_summarises_the_selection(real, capsys):
    d, _ = real
    code, out, _ = run(capsys, "--dir", str(d), "stats")
    s = json.loads(out)
    assert code == 0 and s["requests"] == 4 and s["outcomes"]["answered"] == 2 and s["outcomes"]["abstain"] == 1
    assert s["with_code"] == 2 and s["cost_usd"]["priced"] == 3 and s["latency_ms"]["p50"] is not None


# ------------------------------------------------------------------ added after the incident drill (the investigator's feedback)
def fixed(n, when, **attrs):
    r = rec(n, **summary(**attrs))
    r["start_ts"] = when
    return r


def test_the_summary_counts_warned_deprecations_and_records_why_identifiers_were_unchecked():
    from sdk_engine.schemas import CodeBlockReport, CodeFinding, CodeValidationReport

    findings = [CodeFinding(symbol="a", kind="body_key", tier="deprecated", severity="warn", replacement="b"),
                CodeFinding(symbol="c", kind="body_key", tier="not_checked", severity="warn", reason_code="lookup_unavailable"),
                CodeFinding(symbol="d", kind="body_key", tier="not_checked", severity="warn", reason_code="no_symbol_index")]
    report = CodeValidationReport(version=V22, blocks=[CodeBlockReport(index=1, language="json", lines=1, syntax="parsed", findings=findings)])
    s = graph.request_summary({"code_report": report}, [])
    assert (s["code_deprecated"], s["code_unchecked"]) == (1, 2) and s["code_unchecked_reasons"] == ["lookup_unavailable", "no_symbol_index"]
    assert graph.request_summary({}, [])["code_unchecked"] == 0                                         # no code, nothing claimed


def test_a_real_request_with_no_symbol_index_is_recorded_as_unchecked_for_that_reason_and_the_user_is_told(tmp_path, catalog):
    eng, _, _ = make(tmp_path, catalog, [ans(WRONG_VERSION)], symbols=False)
    res = eng.ask(Q)
    r = requests_of(tmp_path)[0]
    assert r.outcome == "answered" and r.code_unchecked == 1 and r.code_unchecked_reasons == ["no_symbol_index"] and "not_checked" in r.code_tiers
    assert "1 identifier(s) could NOT be checked (no symbol index available): `assignees`" in res.answer     # the user is told, not left to assume


def test_find_can_filter_on_mcp_fallback_and_the_table_names_what_the_flags_mean(tmp_path, capsys):
    write(tmp_path, [rec(1, 5, **summary(mcp_fallbacks=2, tool_failures=2, code_blocks=1, code_tiers=["not_checked"], code_unchecked=1,
                                         code_unchecked_reasons=["lookup_unavailable"], endpoints=["create_issue"])),
                     rec(2, 6, **summary()), rec(3, 7, **summary(code_blocks=1, code_tiers=["cited", "deprecated"], code_deprecated=1))])
    got = [r.trace_id for r in tq.select(requests_of(tmp_path), tq.Query(fallback=True))[0]]
    assert got == [tid(1)]
    code, out, _ = run(capsys, "--dir", str(tmp_path), "find")
    assert code == 0 and "mcp_fallback:2" in out and "unchecked:lookup_unavailable" in out and "deprecated_warned:1" in out
    assert "mcp_fallback:N = N tool calls failed" in out                                                  # the legend is printed
    code, out, _ = run(capsys, "--dir", str(tmp_path), "find", "--fallback", "--json")
    assert [r["trace_id"] for r in json.loads(out)["requests"]] == [tid(1)]


def test_unchecked_and_has_code_separate_a_failure_to_check_from_a_found_and_warned_deprecation(tmp_path):
    """The drill's second investigator: searching `--code-tier deprecated` finds the harmless, WARNED cases, while the worst
    failure (the check never ran) has no deprecated flag. `--unchecked` finds the failure to check directly."""
    write(tmp_path, [
        rec(1, 5, **summary(code_blocks=1, code_tiers=["cited", "deprecated"], code_deprecated=1)),
        rec(2, 6, **summary(code_blocks=1, code_tiers=["not_checked"], code_unchecked=1, code_unchecked_reasons=["lookup_unavailable"])),
        rec(3, 7, **summary(code_blocks=1, code_tiers=["cited"])),
        rec(4, 8, **summary()),
    ])
    reqs = requests_of(tmp_path)
    got = lambda **kw: sorted(r.trace_id for r in tq.select(reqs, tq.Query(**kw))[0])                  # noqa: E731
    assert got(unchecked=True) == [tid(2)] and got(code_tier="deprecated") == [tid(1)]
    assert got(has_code=True) == sorted([tid(1), tid(2), tid(3)]) and got(has_code=True, unchecked=True) == [tid(2)]
    assert tq.Request.from_root(rec(9, 1, **summary(code_tiers=["not_checked"]))).code_unchecked == 0      # a request written without the count ...
    assert tid(9) in sorted(r.trace_id for r in tq.select([tq.Request.from_root(rec(9, 1, **summary(code_tiers=["not_checked"])))],
                                                          tq.Query(unchecked=True))[0])                  # ... is still found through its tier


def test_the_timeline_shows_an_outage_as_a_cluster_without_reading_a_trace():
    reqs = [tq.Request.from_root(fixed(n, f"2026-10-30T14:{m:02d}:00+00:00", mcp_fallbacks=2, code_unchecked=1, code_tiers=["not_checked"]))
            for n, m in enumerate((5, 20, 40), 1)]
    reqs += [tq.Request.from_root(fixed(10 + n, f"2026-10-30T{h:02d}:10:00+00:00")) for n, h in enumerate((9, 9, 10, 15, 16), 1)]
    hours = {row["bucket"]: row for row in tq.timeline(reqs, "hour")}
    assert hours["2026-10-30T14"]["mcp_fallback_requests"] == 3 and hours["2026-10-30T14"]["code_unchecked_requests"] == 3
    assert hours["2026-10-30T09"]["requests"] == 2 and hours["2026-10-30T09"]["mcp_fallback_requests"] == 0
    assert [r["bucket"] for r in tq.timeline(reqs, "day")] == ["2026-10-30"] and tq.timeline(reqs, "day")[0]["requests"] == 8
    with pytest.raises(tq.TraceQueryError):
        tq.timeline(reqs, "week")


def test_stats_bucket_is_available_from_the_cli(tmp_path, capsys):
    write(tmp_path, [fixed(1, "2026-10-30T14:05:00+00:00", mcp_fallbacks=1), fixed(2, "2026-10-31T09:00:00+00:00")])
    code, out, _ = run(capsys, "--dir", str(tmp_path), "stats", "--bucket", "day")
    data = json.loads(out)
    assert code == 0 and [(b["bucket"], b["requests"]) for b in data["timeline"]] == [("2026-10-30", 1), ("2026-10-31", 1)]


def test_as_of_makes_a_relative_query_reproducible_whenever_it_is_run(tmp_path, capsys):
    write(tmp_path, [fixed(1, "2026-10-30T14:09:00+00:00")])
    _, out, _ = run(capsys, "--dir", str(tmp_path), "find", "--since", "2d", "--as-of", "2026-11-01", "--json")
    assert json.loads(out)["matched"] == 1                                                              # 10-30 is within 2d of 11-01
    _, out, _ = run(capsys, "--dir", str(tmp_path), "find", "--since", "2d", "--as-of", "2026-11-10", "--json")
    assert json.loads(out)["matched"] == 0                                                              # ... and not within 2d of 11-10
