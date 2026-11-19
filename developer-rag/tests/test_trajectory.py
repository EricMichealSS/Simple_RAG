"""Trajectory scoring (Week 8 method, integrated tools): the specs, the path extraction from spans, and the score/summary arithmetic."""

from sdk_eval import golden, trajectory as T
from sdk_eval.trajectory import Call, SPECS, score, summarize

V22, V25 = T.V22, T.V25


def calls(*triples):
    return [Call(tool=t, ok=ok, version=v) for t, ok, v in triples]


def good_single():
    return calls(("search_docs", True, V25), ("lookup_changes", True, V25))


# ------------------------------------------------------------------ the specs were fixed before the run, and are consistent
def test_there_are_ten_specs_for_the_ten_week7_race_questions():
    assert set(SPECS) == {"easy_01", "easy_02", "easy_13", "medium_01", "medium_07", "medium_13", "hard_01", "hard_04", "hard_06", "hard_11"}
    assert set(SPECS) <= {r["id"] for r in golden.load_golden()}


def test_every_spec_is_internally_consistent():
    for sid, s in SPECS.items():
        assert s.versions and all(t in s.allowed_max for t in s.required), sid
        assert all(s.required[t] <= s.allowed_max[t] for t in s.required), sid
        assert set(s.allowed_max) <= {"search_docs", "lookup_changes", "check_symbol"}, sid          # only the integrated tool vocabulary
        assert s.why


def test_comparison_specs_require_one_search_per_version_and_the_changelog_and_negatives_expect_a_refusal():
    for sid in ("hard_01", "hard_04", "hard_06", "hard_11"):
        s = SPECS[sid]
        assert len(s.versions) == 2 and s.required == {"search_docs": 2, "lookup_changes": 1}
    assert SPECS["easy_13"].negative and SPECS["medium_13"].negative and not SPECS["easy_01"].negative


# ------------------------------------------------------------------ scoring
def test_a_correct_path_scores_clean():
    r = score(SPECS["easy_01"], good_single(), outcome="answered", outcome_pass=True)
    assert r["path_ok"] and r["unnecessary_calls"] == 0 and r["failed_calls"] == 0 and r["missing_required"] == {} and r["version_args_ok"]
    assert r["valid_tool_calls"] == 2 and r["gap"] == "agree" and r["outcome_as_expected"]


def test_an_unnecessary_call_is_counted_and_the_path_is_not_ok():
    r = score(SPECS["easy_02"], calls(("search_docs", True, "3.x"), ("lookup_changes", True, "3.x")), outcome="answered", outcome_pass=True)
    assert r["unnecessary_calls"] == 1 and not r["path_ok"] and r["gap"] == "correct_answer_unreliable_path"


def test_a_missing_required_call_is_reported_even_when_the_answer_is_right():
    r = score(SPECS["medium_01"], calls(("search_docs", True, V25)), outcome="answered", outcome_pass=True)
    assert r["missing_required"] == {"lookup_changes": 1} and not r["path_ok"] and r["gap"] == "correct_answer_unreliable_path"


def test_a_comparison_needs_a_search_in_each_version_and_a_repeat_is_unnecessary():
    both = calls(("search_docs", True, V22), ("search_docs", True, V25), ("lookup_changes", True, V22))
    assert score(SPECS["hard_01"], both, outcome="answered", outcome_pass=True)["path_ok"]
    twice_same = calls(("search_docs", True, V25), ("search_docs", True, V25), ("lookup_changes", True, V22))
    r = score(SPECS["hard_01"], twice_same, outcome="answered", outcome_pass=True)
    assert not r["version_args_ok"] and not r["path_ok"]                       # the 2022-11-28 side was never searched
    three = both + calls(("search_docs", True, V22))
    assert score(SPECS["hard_01"], three, outcome="answered", outcome_pass=True)["unnecessary_calls"] == 1


def test_a_call_the_server_saw_with_the_wrong_version_fails_the_version_check():
    r = score(SPECS["easy_01"], calls(("search_docs", True, V22), ("lookup_changes", True, V25)), outcome="answered", outcome_pass=True)
    assert not r["version_args_ok"] and not r["path_ok"]


def test_a_failed_call_and_a_wrong_tool_are_counted():
    r = score(SPECS["easy_01"], calls(("search_docs", False, None), ("check_symbol", True, V25), ("search_docs", True, V25)), outcome="answered", outcome_pass=False)
    assert r["failed_calls"] == 1 and r["wrong_tool_calls"] == ["check_symbol"] and not r["path_ok"]


def test_the_outcome_and_the_path_are_scored_independently_all_four_cells():
    ok, bad = good_single(), calls(("search_docs", True, V25))
    for cs, passed, expect in [(ok, True, "agree"), (bad, True, "correct_answer_unreliable_path"), (ok, False, "right_path_wrong_answer"), (bad, False, "agree")]:
        assert score(SPECS["medium_01"], cs, outcome="answered", outcome_pass=passed)["gap"] == expect
    assert score(SPECS["easy_01"], ok, outcome="answered", outcome_pass=None)["gap"] is None             # ungraded: no claim


def test_a_negative_question_expects_a_refusal_not_an_answer():
    r = score(SPECS["easy_13"], calls(("search_docs", True, V25)), outcome="answered", outcome_pass=False)
    assert r["expected_outcome"] == "abstain" and not r["outcome_as_expected"]
    assert score(SPECS["easy_13"], calls(("search_docs", True, V25)), outcome="abstain", outcome_pass=True)["outcome_as_expected"]


def test_summary_figures_carry_their_denominators():
    rows = [{"score": score(SPECS["easy_01"], good_single(), outcome="answered", outcome_pass=True)},
            {"score": score(SPECS["easy_02"], calls(("search_docs", True, "3.x"), ("lookup_changes", True, "3.x")), outcome="answered", outcome_pass=True)},
            {"score": score(SPECS["medium_01"], calls(("search_docs", True, V25), ("lookup_changes", True, V25)), outcome="answered", outcome_pass=False)}]
    s = summarize(rows)
    assert s["questions"] == 3 and s["tool_calls"] == 6 and s["tool_choice_accuracy"] == {"valid": 5, "of": 6, "rate": 0.833}
    assert s["path_ok"] == {"count": 2, "of": 3} and s["unnecessary_calls"] == 1 and s["outcome_pass_judge"] == {"count": 2, "of": 3}
    assert s["outcome_vs_path"] == {"outcome_pass&path_ok": 1, "outcome_pass&path_not_ok": 1, "outcome_fail&path_ok": 1, "outcome_fail&path_not_ok": 0}


# ------------------------------------------------------------------ path extraction from real span shapes
def _span(sid, name, parent, service="sdk_engine", start="2026-11-04T10:00:00+00:00", **attrs):
    return {"span_id": sid, "parent_span_id": parent, "service": service, "name": name, "start_ts": start, "attributes": attrs}


def test_calls_are_extracted_in_order_and_joined_to_the_version_the_server_saw():
    spans = [_span("c2", "mcp.call", "r", start="2026-11-04T10:00:02+00:00", tool="lookup_changes", ok=True),
             _span("c1", "mcp.call", "r", start="2026-11-04T10:00:01+00:00", tool="search_docs", ok=True),
             _span("s1", "mcp.tool.search_docs", "c1", service="mcp_server", requested_version=V25),
             _span("s2", "mcp.tool.lookup_changes", "c2", service="mcp_server", requested_version=V25),
             _span("x", "retrieve", "r")]
    assert [(c.tool, c.version) for c in T.extract_calls(spans)] == [("search_docs", V25), ("lookup_changes", V25)]


def test_a_call_that_never_reached_the_server_has_no_version_and_counts_as_failed():
    assert T.extract_calls([_span("c1", "mcp.call", "r", tool="search_docs", ok=False, error_category="timeout")]) == [Call("search_docs", False, None, "timeout")]


# ------------------------------------------------------------------ golden helpers
def test_gold_chunk_labels_map_to_index_page_prefixes():
    assert golden.expected_pages("github_combined_p5#create_issue_v2025") == ["github_combined_reference:p5:"]
    assert golden.expected_pages("swagger_v3_p1#flag_reference; swagger_v2_p1#flag_reference") == ["swagger_v3_reference:p1:", "swagger_v2_reference:p1:"]
    assert golden.expected_pages(None) == [] and golden.expected_pages("garbage") == []


def test_the_golden_loader_returns_45_questions_with_unanswerable_ones_marked():
    rows = golden.load_golden()
    assert len(rows) == 45 and sum(not r["answerable"] for r in rows) == 9
    assert {r["level"] for r in rows} == {"easy", "medium", "hard"} and len({r["id"] for r in rows}) == 45


def test_a_non_ascii_date_in_an_answer_is_detected_for_the_exposure_count():
    rx = golden._NON_ASCII_DATE
    assert rx.search("In 2022‑11‑28 there was no field") and rx.search("2025–06–01")
    assert not rx.search("In 2022-11-28 there was no field") and not rx.search("version 3.x")


# ------------------------------------------------------------------ load test aggregation (stub engine: no model, no index)
def test_the_load_level_reports_percentiles_outcomes_and_counts_an_exception_as_a_failure():
    import time
    from types import SimpleNamespace

    from sdk_eval import loadtest

    def ask(q, v):
        time.sleep(0.002)
        if q == "boom":
            raise RuntimeError("engine crashed")
        return SimpleNamespace(outcome="error" if q == "err" else "answered", reason="scope" if q == "err" else "")

    row = loadtest.run_level(ask, [("a", None)] * 7 + [("boom", None), ("err", None)], concurrency=3)
    assert row["requests"] == 9 and row["outcomes"] == {"answered": 7, "exception:RuntimeError": 1, "error": 1} and row["failures"] == 2
    assert row["latency_ms"]["p50"] <= row["latency_ms"]["p95"] <= row["latency_ms"]["max"] and row["throughput_rps"] > 0
    assert any("exception:RuntimeError" in s for s in row["failure_samples"])
