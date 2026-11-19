"""The integrated evaluation suite, tested as software: the registry meets the brief, the judge arithmetic and the label sheet are honest,
a crashing case can never count as a pass, and (integration) the offline tier and the RED/GREEN regression run against the real index."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import pytest

from sdk_eval import cases as C
from sdk_eval import cli, judge, regress, runner
from sdk_eval.harness import Rec, Skip


# ------------------------------------------------------------------ the registry meets the brief
def test_the_suite_has_25_to_40_cases_with_unique_ids():
    ids = [s.id for s in C.CASES]
    assert 25 <= len(ids) <= 40 and len(ids) == len(set(ids))


def test_every_one_of_the_briefs_sixteen_areas_is_covered():
    covered = {k for s in C.CASES for k in (s.category, *s.covers)}
    assert covered == set(C.CATEGORIES) and len(C.CATEGORIES) == 16, set(C.CATEGORIES) - covered


def test_every_case_declares_valid_metadata():
    for s in C.CASES:
        assert s.category in C.CATEGORIES and s.tier in ("offline", "live") and s.origin and s.title
        assert s.modes and all(m in C.MODES for m in s.modes), (s.id, s.modes)
        assert all(c in C.CATEGORIES for c in s.covers)


def test_the_suite_reuses_week6_modes_the_golden_set_and_real_observed_failures():
    origins = " ".join(s.origin for s in C.CASES)
    assert "week6:" in origins and "golden:gs_" in origins and "observed:" in origins
    assert sum(s.origin.startswith("golden:") for s in C.CASES) >= 8


def test_every_regression_case_has_a_re_creatable_defect_and_a_real_origin():
    reg = [s for s in C.CASES if s.regression]
    assert len(reg) >= 1 and {s.id for s in reg} == set(regress.DEFECTS)
    assert all(s.origin.startswith("observed:") and s.tier == "offline" for s in reg)


def test_live_cases_are_only_those_that_need_the_real_model():
    assert {s.id for s in C.CASES if s.tier == "live"} >= {"g01", "g09", "u01", "k04"}
    assert all(s.tier == "offline" for s in C.CASES if s.regression)


# ------------------------------------------------------------------ a case can fail, skip or crash, never silently pass
def _spec(fn, **kw):
    return C.Spec(id=kw.get("id", "t"), title="t", category="abstention", modes=("false_refusal",), origin="new", tier="offline", fn=fn)


def test_a_crashing_case_is_an_error_and_keeps_the_checks_made_before_the_crash():
    def fn(rec):
        rec.check("first", True)
        raise RuntimeError("boom")
    r = runner.run_case(SimpleNamespace(), _spec(fn))
    assert r["status"] == "error" and "RuntimeError: boom" in r["error"] and r["checks"] == [{"name": "first", "passed": True, "detail": ""}]


def test_a_case_with_no_checks_does_not_pass():
    assert runner.run_case(SimpleNamespace(), _spec(lambda rec: None))["status"] == "fail"


def test_a_case_that_needs_a_missing_model_is_skipped_not_passed_or_failed():
    def fn(rec):
        raise Skip("needs the live model")
    r = runner.run_case(SimpleNamespace(), _spec(fn))
    assert r["status"] == "skipped" and r["checks"] == []


def test_one_failing_check_fails_the_case():
    def fn(rec):
        rec.check("a", True)
        rec.check("b", False, "why")
    r = runner.run_case(SimpleNamespace(), _spec(fn))
    assert r["status"] == "fail" and [c["passed"] for c in r["checks"]] == [True, False]


# ------------------------------------------------------------------ aggregation
def _res(id, status, modes=("version_confusion",), **kw):
    base = {"id": id, "status": status, "modes": list(modes), "category": "version_resolution", "covers": [], "tier": "offline", "retrieved": 0,
            "wrong_version_chunks": 0, "citations_total": 0, "citations_bad": 0, "live_code_samples": 0, "live_wrong_version_samples": 0, "code": [],
            "cost_usd": None, "live_llm_calls": 0, "scripted_llm_calls": 0, "notes": {}, "checks": []}
    base.update(kw)
    return base


def test_pass_rate_by_mode_uses_executed_cases_as_the_denominator_and_reports_it():
    s = runner.summarize([_res("a", "pass"), _res("b", "fail"), _res("c", "skipped"), _res("d", "error", modes=("unsafe_content",))])
    m = s["by_taxonomy_mode"]
    assert m["version_confusion"]["denominator"] == 2 and m["version_confusion"]["pass_rate"] == 0.5 and m["version_confusion"]["skipped"] == 1
    assert m["unsafe_content"]["pass_rate"] == 0.0 and s["executed"] == 3 and s["pass_rate_executed"] == round(1 / 3, 3)


def test_metrics_with_no_data_say_so_instead_of_reporting_zero():
    s = runner.summarize([_res("a", "pass")])
    assert s["retrieval_version_filter"]["wrong_version_rate"] is None and s["wrong_version_code"]["live_rate"] is None
    assert s["citations_live"]["correct_rate"] is None and s["cost"]["priced_api_cost_usd"] is None


def test_wrong_version_and_citation_rates_are_computed_from_the_counts():
    s = runner.summarize([_res("a", "pass", retrieved=50, wrong_version_chunks=1, citations_total=10, citations_bad=1,
                               live_code_samples=4, live_wrong_version_samples=1, cost_usd=0.001, live_llm_calls=2)])
    assert s["retrieval_version_filter"]["wrong_version_rate"] == 0.02 and s["citations_live"]["correct_rate"] == 0.9
    assert s["wrong_version_code"]["live_rate"] == 0.25 and s["cost"]["priced_api_cost_usd"] == 0.001 and s["non_coverage"]


# ------------------------------------------------------------------ the judge: arithmetic and honesty
def test_a_verdict_is_parsed_and_an_unparseable_reply_is_unparsed():
    assert judge.parse_verdict("VERDICT: PASS because ...") == "PASS" and judge.parse_verdict("verdict: fail") == "FAIL"
    assert judge.parse_verdict("I think it is fine") == "UNPARSED" and judge.parse_verdict(None) == "UNPARSED"


def _v(**ids):
    return [{"id": i, "verdict": v} for i, v in ids.items()]


def _l(**ids):
    return [{"id": i, "label": v, "reason": ""} for i, v in ids.items()]


def test_agreement_is_agree_over_the_human_labelled_denominator_and_flags_the_threshold():
    r = judge.agreement(_v(a="PASS", b="FAIL", c="PASS", d="FAIL"), _l(a="PASS", b="FAIL", c="FAIL", d="FAIL"))
    assert (r["n"], r["agree"], r["agreement"]) == (4, 3, 0.75) and r["meets_threshold"] is False and r["disagreements"] == [{"id": "c", "human": "FAIL", "judge": "PASS"}]
    ok = judge.agreement(_v(**{f"x{i}": "PASS" for i in range(20)}), _l(**{f"x{i}": "PASS" for i in range(20)}))
    assert ok["meets_threshold"] is True and ok["sample_warning"] is None


def test_without_human_labels_agreement_is_not_measured_not_zero_and_not_perfect():
    for labels in ([], _l(a=None, b=None), [{"id": "a", "label": "MAYBE"}]):
        r = judge.agreement(_v(a="PASS", b="FAIL"), labels)
        assert r["measured"] is False and r["n"] == 0 and r["agreement"] is None and r["meets_threshold"] is None


def test_a_small_sample_is_flagged_as_too_small_to_claim_the_threshold():
    r = judge.agreement(_v(a="PASS", b="PASS"), _l(a="PASS", b="PASS"))
    assert r["agreement"] == 1.0 and "too small" in r["sample_warning"]


def test_a_judge_reply_that_is_not_pass_or_fail_counts_as_a_disagreement():
    r = judge.agreement(_v(a="UNPARSED", b="ERROR", c="PASS"), _l(a="PASS", b="FAIL", c="PASS"))
    assert (r["n"], r["agree"], r["judge_unparsed_or_error"]) == (3, 1, 2)


def test_labels_for_outputs_the_judge_never_graded_are_reported_not_counted():
    r = judge.agreement(_v(a="PASS"), _l(a="PASS", z="PASS"))
    assert r["n"] == 1 and r["labelled_but_not_judged"] == ["z"]


def test_the_human_label_sheet_is_blind_to_the_judge_and_to_the_suites_own_verdict():
    out = [{"id": "g01#1", "case": "g01", "question": "q", "caller_version": "2022-11-28", "answer": "a", "outcome": "answered",
            "context": [{"chunk_id": "c", "text": "t"}], "judge_verdict": "PASS", "status": "pass"}]
    sheet = judge.label_sheet(out)
    dumped = json.dumps(sheet).lower()
    assert "verdict" not in dumped.replace("judge_verdicts.json", "") and "status" not in dumped and "\"case\"" not in dumped and "outcome" not in dumped
    assert sheet["items"][0]["id"] == "g01#1" and sheet["items"][0]["answer_shown_to_the_user"] == "a"
    assert judge.label_template(out) == [{"id": "g01#1", "label": None, "reason": ""}]


def test_judge_vs_deterministic_is_labelled_informational_and_is_not_the_threshold_check():
    results = [{"id": "g01", "status": "pass"}, {"id": "g02", "status": "fail"}, {"id": "g03", "status": "skipped"}]
    r = judge.judge_vs_deterministic(results, _v(**{"g01#1": "PASS", "g02#1": "PASS", "g03#1": "PASS"}))
    assert r["n"] == 2 and r["agree"] == 1 and "NOT human labels" in r["note"] and "meets_threshold" not in r


# ------------------------------------------------------------------ RED/GREEN logic
def test_red_green_is_verified_only_when_the_defect_breaks_the_case_and_the_current_code_passes():
    state = {"broken": False}

    @contextmanager
    def break_it():
        state["broken"] = True
        try:
            yield
        finally:
            state["broken"] = False

    def fn(rec):
        rec.check("works", not state["broken"])

    def inert(rec):
        rec.check("works", True)               # stays green even with the defect: protects nothing

    specs = [C.Spec("rA", "sensitive", "abstention", ("over_blocking",), "observed:x", "offline", fn, regression=True),
             C.Spec("rB", "inert", "abstention", ("over_blocking",), "observed:x", "offline", inert, regression=True)]
    with mock.patch.object(runner, "select", lambda ids=None, tier=None, regression=None: specs), \
            mock.patch.dict(regress.DEFECTS, {"rA": ("d", break_it), "rB": ("d", break_it)}, clear=True):
        rows = {r["id"]: r for r in regress.red_green(SimpleNamespace())}
    assert rows["rA"]["red"] == "fail" and rows["rA"]["green"] == "pass" and rows["rA"]["verified"] is True
    assert rows["rB"]["red"] == "pass" and rows["rB"]["verified"] is False


# ------------------------------------------------------------------ CLI
def test_cli_list_reports_counts_and_category_coverage(capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert f"{len(C.CASES)} cases" in out and "16/16 brief categories covered" in out


def _run_dir(tmp_path, with_verdicts=True):
    (tmp_path / "results.json").write_text(json.dumps({"meta": {}, "results": [{"id": "g01", "status": "pass"}]}), encoding="utf-8")
    (tmp_path / "outputs.json").write_text(json.dumps([{"id": "g01#1", "case": "g01", "question": "q", "caller_version": "2022-11-28", "outcome": "answered",
                                                         "answer": "a", "context": []}]), encoding="utf-8")
    if with_verdicts:
        (tmp_path / "judge_verdicts.json").write_text(json.dumps(_v(**{"g01#1": "PASS"})), encoding="utf-8")
    return tmp_path


def test_cli_agreement_with_no_labels_says_not_measured(tmp_path, capsys):
    d = _run_dir(tmp_path)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([{"id": "g01#1", "label": None, "reason": ""}]), encoding="utf-8")
    assert cli.main(["agreement", "--run", str(d), "--labels", str(labels)]) == 0
    out = capsys.readouterr().out
    assert "NOT MEASURED" in out and "denominator" in out
    assert json.loads((d / "agreement.json").read_text(encoding="utf-8"))["measured"] is False


def test_cli_agreement_with_a_human_label_reports_the_denominator(tmp_path, capsys):
    d = _run_dir(tmp_path)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(_l(**{"g01#1": "PASS"})), encoding="utf-8")
    cli.main(["agreement", "--run", str(d), "--labels", str(labels)])
    out = capsys.readouterr().out
    assert "1/1 = 100.0%" in out and "too small" in out


def test_cli_agreement_accepts_verdicts_and_key_from_elsewhere_and_reports_human_vs_construction(tmp_path, capsys):
    d = _run_dir(tmp_path, with_verdicts=False)
    elsewhere = tmp_path / "private"
    elsewhere.mkdir()
    (elsewhere / "v.json").write_text(json.dumps(_v(item01="PASS", item02="FAIL")), encoding="utf-8")
    (elsewhere / "key.json").write_text(json.dumps({"item01": {"kind": "real"}, "item02": {"kind": "wrong_fact"}}), encoding="utf-8")
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(_l(item01="PASS", item02="PASS")), encoding="utf-8")              # the human passes a bad answer I constructed
    cli.main(["agreement", "--run", str(d), "--labels", str(labels), "--verdicts", str(elsewhere / "v.json"), "--key", str(elsewhere / "key.json")])
    out = capsys.readouterr().out
    assert "1/2 = 50.0%" in out and "human_passed_a_constructed_bad_answer\": 1" in out
    assert judge.human_vs_construction(_l(item01="FAIL"), {"item01": {"kind": "real"}})["human_failed_a_real_answer"] == 1


def test_cli_label_sheet_writes_a_blind_sheet_and_an_empty_template(tmp_path, capsys):
    d = _run_dir(tmp_path)
    assert cli.main(["label-sheet", "--run", str(d)]) == 0
    sheet = (d / "hand_label_sheet.json").read_text(encoding="utf-8")
    assert "PASS" not in json.loads(sheet)["items"][0] and json.loads((d / "hand_labels_template.json").read_text(encoding="utf-8"))[0]["label"] is None


# ------------------------------------------------------------------ controlled negatives and the opaque label set
def _o(i, answer, **kw):
    return {"id": i, "case": i.split("#")[0], "question": f"question {i}", "caller_version": "2022-11-28", "comparison_versions": [], "outcome": "answered",
            "answer": answer, "context": [{"chunk_id": "c:1", "text": "the real context"}], **kw}


def _full_outputs():
    return [_o("g01#1", "[GitHub REST API 2022-11-28] It returns 201 Created [c:1]."), _o("g02#1", "[GitHub REST API 2022-11-28] Scopes: public_repo or repo [c:1]."),
            _o("g03#1", "[GitHub REST API 2025-06-01] Use assignees [c:1]."), _o("g04#1", "[Swagger Codegen 3.x] Use --generate-markdown [c:1]."),
            _o("g05#1", "[GitHub REST API 2025-06-01] internal is accepted [c:1]."), _o("g06#1", "[GitHub REST API 2025-06-01] discussion_category_name [c:1]."),
            _o("g07#1", "[GitHub REST API 2022-11-28] In 2022 there was no field.\n\nIn 2025 make_latest exists."),
            _o("g08#1", "**2022**: no type.\n\n**2025**: type exists."), _o("g09#1", "[GitHub REST API 2025-06-01] I don't know based on the provided documents.", outcome="abstain")]


def test_controlled_negatives_are_built_by_stated_rules_from_the_real_outputs_and_context():
    negs = judge.controlled_negatives(_full_outputs())
    kinds = [n["_kind"] for n in negs]
    assert len(negs) == 13 and {k: kinds.count(k) for k in set(kinds)} == {"wrong_fact": 5, "false_refusal": 3, "answers_a_different_question": 2,
                                                                          "one_sided_comparison": 2, "fabricated_answer": 1}
    by = {n["id"]: n for n in negs}
    assert "200" in by["neg:g01#1:wrong_fact"]["answer"] and "201" not in by["neg:g01#1:wrong_fact"]["answer"]
    assert by["neg:g03#1:false_refusal"]["answer"] == "[GitHub REST API 2025-06-01] I don't know based on the provided documents."
    assert by["neg:g07#1:one_sided_comparison"]["answer"] == "[GitHub REST API 2022-11-28] In 2022 there was no field."
    assert "public_repo" not in by["neg:g02#1:answers_a_different_question"]["answer"] and "201" in by["neg:g02#1:answers_a_different_question"]["answer"]
    assert all(n["context"] == [{"chunk_id": "c:1", "text": "the real context"}] and n["answer"] != n["answer"][:0] for n in negs)


def test_a_negative_is_only_made_when_its_source_output_exists():
    assert [n["_kind"] for n in judge.controlled_negatives([_o("g01#1", "It returns 201.")])] == ["wrong_fact"]
    assert judge.controlled_negatives([]) == []


def test_the_label_set_mixes_real_and_constructed_items_under_opaque_deterministic_ids():
    items, key = judge.build_label_set(_full_outputs())
    assert len(items) == 9 + 13 and [i["id"] for i in items] == [f"item{n:02d}" for n in range(1, 23)]
    assert sum(k["kind"] == "real" for k in key.values()) == 9 and sum(k["expected_by_construction"] == "FAIL" for k in key.values()) == 13
    again, _ = judge.build_label_set(_full_outputs())
    assert [i["answer"] for i in again] == [i["answer"] for i in items]                      # same seed, same order
    other, _ = judge.build_label_set(_full_outputs(), seed=99)
    assert [i["answer"] for i in other] != [i["answer"] for i in items]
    kinds_in_order = [key[i["id"]]["kind"] for i in items]
    assert kinds_in_order != sorted(kinds_in_order) and kinds_in_order[:9].count("real") < 9      # shuffled: the real ones are not all first


def test_nothing_in_the_sheet_or_template_reveals_which_items_are_constructed():
    items, key = judge.build_label_set(_full_outputs())
    import re

    sheet = judge.label_sheet(items)
    per_item = json.dumps(sheet["items"]).replace("the real context", "<context>") + json.dumps(judge.label_template(items))    # the data, not the instructions
    for leak in ("neg:", "wrong_fact", "false_refusal", "one_sided", "fabricated", "controlled", "expected_by_construction", "source"):
        assert leak not in per_item, leak
    assert not re.search(r"\b(real|constructed|negative|kind)\b", per_item)
    assert "judge" not in json.dumps(sheet["items"]).lower() and all(i["id"].startswith("item") for i in sheet["items"])
    assert all(set(i) == {"id", "question", "caller_version", "comparison_versions", "answer_shown_to_the_user", "context_the_assistant_was_shown"} for i in sheet["items"])


def test_the_judge_check_on_constructed_items_is_labelled_as_measured_against_my_construction_not_humans():
    items, key = judge.build_label_set(_full_outputs())
    verdicts = [{"id": i["id"], "verdict": "FAIL" if key[i["id"]]["kind"] != "real" and key[i["id"]]["kind"] != "fabricated_answer" else "PASS"} for i in items]
    info = judge.judge_on_controlled_items(verdicts, key)
    assert info["controlled_negatives"] == 13 and info["judge_said_fail"] == 12 and info["judge_caught_rate_vs_construction"] == round(12 / 13, 3)
    assert info["by_kind"]["fabricated_answer"] == {"items": 1, "judge_said_fail": 0} and info["real_judged_pass"] == 9
    assert "NOT against human labels" in info["note"] and "meets_threshold" not in info


def test_agreement_works_on_the_opaque_items_and_a_human_who_disagrees_with_my_construction_is_not_overruled():
    items, key = judge.build_label_set(_full_outputs())
    verdicts = [{"id": i["id"], "verdict": "PASS" if key[i["id"]]["kind"] == "real" else "FAIL"} for i in items]
    labels = [{"id": i["id"], "label": "PASS" if key[i["id"]]["kind"] == "real" else "FAIL"} for i in items]
    assert judge.agreement(verdicts, labels)["agreement"] == 1.0
    flipped = next(i["id"] for i in items if key[i["id"]]["kind"] == "one_sided_comparison")
    labels = [dict(l, label="PASS") if l["id"] == flipped else l for l in labels]           # the human thinks this 'bad' answer is acceptable
    r = judge.agreement(verdicts, labels)
    assert r["n"] == 22 and r["agree"] == 21 and r["disagreements"] == [{"id": flipped, "human": "PASS", "judge": "FAIL"}]       # the human label stands


# ------------------------------------------------------------------ against the real index (integration)
pytestmark_integration = pytest.mark.integration


@pytest.fixture(scope="module")
def real_env(tmp_path_factory):
    from sdk_engine.retrieval import IndexMissing
    from sdk_eval.harness import Env

    try:
        env = Env(tmp_path_factory.mktemp("eval_traces"), live=False)
    except IndexMissing:
        pytest.skip("race_docs index not built")
    yield env
    env.close()


@pytest.mark.integration
def test_the_whole_offline_tier_passes_on_the_real_index(real_env):
    results = runner.run_suite(real_env, runner.select(tier="offline"), progress=lambda *_: None)
    failed = {r["id"]: [c["name"] for c in r["checks"] if not c["passed"]] or r["error"] for r in results if r["status"] != "pass"}
    assert not failed, failed
    assert len(results) >= 20


@pytest.mark.integration
def test_every_regression_case_is_red_with_its_defect_and_green_on_the_current_code(real_env):
    rows = regress.red_green(real_env)
    assert rows and all(r["verified"] for r in rows), [r for r in rows if not r["verified"]]
