"""End-to-end graph behaviour with a real in-memory Chroma, a fake embedder, and a scripted fake LLM.
No API key, no ML model, no network."""

import json

import pytest

from conftest import LeakyCollection, hash_embed
from sdk_engine.config import EngineConfig
from sdk_engine.graph import Deps, Engine
from sdk_engine.llm import LLMResult, LLMUnavailable
from sdk_engine.retrieval import ChromaRetriever
from sdk_engine.trace import JsonlSink, TraceContext

V22, V25 = "2022-11-28", "2025-06-01"
CID22 = "gh:p4:spec:create-an-issue:0"
CID25 = "gh:p5:spec:create-an-issue:1"
GROQ = "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2"


class FakeLLM:
    def __init__(self, replies, model="openai/gpt-oss-120b", raises=None, finish=None):
        self.model, self.replies, self.raises, self.calls = model, list(replies), raises, []
        self.finish = list(finish or [])

    def complete(self, messages, *, max_tokens, temperature=0.0):
        self.calls.append(messages)
        if self.raises:
            raise self.raises
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        fr = self.finish.pop(0) if self.finish else None
        return LLMResult(text=text, prompt_tokens=100, completion_tokens=20, model=self.model, finish_reason=fr)


class CountingRetriever:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def search(self, query, scope, top_k=5):
        self.calls.append(scope)
        return self.inner.search(query, scope, top_k)


def engine(col, catalog, tmp_path, llm=None, retriever=None, **cfg):
    config = EngineConfig(trace_dir=tmp_path, **cfg)
    r = retriever or CountingRetriever(ChromaRetriever(col, hash_embed))
    return Engine(Deps(config=config, catalog=catalog, retriever=r, llm=llm, sink=JsonlSink(tmp_path))), r


def spans(tmp_path):
    return [json.loads(line) for line in (tmp_path / "spans.jsonl").read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------------ happy path
def test_version_specific_answer_is_scoped_cited_one_llm_call_and_one_trace(synth_collection, catalog, tmp_path):
    llm = FakeLLM([f"Pass assignee as a single login string [{CID22}]."])
    eng, retr = engine(synth_collection, catalog, tmp_path, llm)
    q = f"ZQXMARKER My app uses API version {V22}. How do I create an issue with an assignee?"
    res = eng.ask(q)

    assert res.outcome == "answered" and res.answer.startswith(f"[GitHub REST API {V22}]")
    assert [c.chunk_id for c in res.citations] == [CID22]
    assert res.citations[0].api_version == V22 and res.citations[0].source_name
    assert res.version.version == V22 and res.version.source.value == "question"
    assert set(res.retrieved_chunk_ids) <= {CID22, "gh:p10:spec:create-a-release:3"}  # nothing from 2025-06-01
    assert res.usage.llm_calls == 1 and len(llm.calls) == 1
    assert res.usage.cost_status == "calculated" and res.usage.cost_usd > 0

    # one trace id everywhere, every node span nested under the request span (parent links survive LangGraph)
    recorded = spans(tmp_path)
    assert {s["trace_id"] for s in recorded} == {res.trace_id}
    root = next(s for s in recorded if s["name"] == "request")
    assert root["parent_span_id"] is None
    assert all(s["parent_span_id"] == root["span_id"] for s in recorded if s["name"] != "request")
    names = [s["name"] for s in recorded]
    for expected in ("validate_request", "resolve_version", "route", "retrieve", "changelog", "assess_sufficiency",
                     "generate", "verify_citations_and_version", "finalize"):
        assert expected in names
    # privacy: the question text is never persisted
    assert "ZQXMARKER" not in (tmp_path / "spans.jsonl").read_text(encoding="utf-8")


def test_older_caller_gets_a_labelled_upgrade_note_but_never_v3_text_in_the_answer(synth_collection, catalog, tmp_path):
    llm = FakeLLM([f"Use assignee [{CID22}]."])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22}, how do I set the assignee when creating an issue?")
    assert res.outcome == "answered"
    notes = [n for n in res.deprecation_notes if n.cross_version]
    assert notes and all(n.applies_to_version == V25 and n.caller_version == V22 for n in notes)
    assert V25 not in res.answer  # the answer itself stays in the caller's version
    assert all(c.api_version == V22 for c in res.citations)


def test_migration_question_uses_a_labelled_comparison_scope(synth_collection, catalog, tmp_path):
    llm = FakeLLM([f"Before: single assignee [{CID22}] ({V22}); now: assignees array [{CID25}] ({V25})."])
    eng, retr = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"How do I migrate create_issue from {V22} to {V25}?")
    assert res.outcome == "answered" and res.version.is_comparison
    assert retr.calls[0].mode == "comparison" and set(retr.calls[0].versions) == {V22, V25}
    assert {c.api_version for c in res.citations} == {V22, V25}


def test_alias_words_that_belong_to_another_product_are_ignored(synth_collection, catalog, tmp_path):
    """Regression (found on the real index): 'On Swagger Codegen 2.x, which flag generates markdown documentation?'
    matched the GitHub endpoint alias 'markdown', so sufficiency demanded render_markdown in a Swagger scope and the
    request abstained for the wrong reason. The resolved product must decide."""
    eng, _ = engine(synth_collection, catalog, tmp_path, FakeLLM(["I don't know based on the provided documents."]))
    eng.ask("On Swagger Codegen 2.x which java generator option controls markdown output?")
    retrieve = next(s for s in spans(tmp_path) if s["name"] == "retrieve")
    assert retrieve["attributes"]["boosted_endpoints"] == ["java_generator"]      # 'render_markdown' (GitHub) was discarded
    assess = next(s for s in spans(tmp_path) if s["name"] == "assess_sufficiency")
    assert assess["attributes"]["sufficient"] is True and "render_markdown" not in assess["attributes"]["why"]


class FixedOrderRetriever:
    """Returns a fixed, deliberately wrong-first ranking so the graph's endpoint boost is observable."""

    def __init__(self, passages):
        self.passages = passages

    def search(self, query, scope, top_k=5):
        from sdk_engine.retrieval import RetrievalResult
        return RetrievalResult(passages=[p for p in self.passages if p.api_version in scope.versions], stats={"where": {}})


def test_the_graph_boosts_the_endpoint_the_question_is_about_to_the_front(synth_collection, catalog, tmp_path):
    from sdk_engine.schemas import Passage

    wrong_first = Passage(chunk_id="gh:p10:spec:create-a-release:3", text="release body", source_name="s", api_version=V22,
                          endpoint="create_release", page=10)
    right_second = Passage(chunk_id=CID22, text="issue body", source_name="s", api_version=V22, endpoint="create_issue", page=4)
    eng, _ = engine(synth_collection, catalog, tmp_path, FakeLLM([f"x [{CID22}]"]),
                    retriever=FixedOrderRetriever([wrong_first, right_second]))
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.retrieved_chunk_ids[0] == CID22 and set(res.retrieved_chunk_ids) == {CID22, wrong_first.chunk_id}
    retrieve = next(s for s in spans(tmp_path) if s["name"] == "retrieve")
    assert retrieve["attributes"]["boosted_endpoints"] == ["create_issue"]


# ------------------------------------------------------------------ never guess, never answer outside scope
def test_unknown_version_asks_the_user_without_retrieving_or_calling_the_llm(synth_collection, catalog, tmp_path):
    llm = FakeLLM(["should never be used"])
    eng, retr = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask("How do I create an issue?")
    assert res.outcome == "clarify" and "Which version" in res.answer and res.clarification
    assert retr.calls == [] and llm.calls == []


def test_unsupported_version_is_refused_not_substituted(synth_collection, catalog, tmp_path):
    llm = FakeLLM(["nope"])
    eng, retr = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask("create an issue", explicit_version="2024-01-01")
    assert res.outcome == "clarify" and res.version.status.value == "unsupported"
    assert retr.calls == [] and llm.calls == []


def test_out_of_scope_question_abstains_without_retrieval(synth_collection, catalog, tmp_path):
    llm = FakeLLM(["nope"])
    eng, retr = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask("What is the capital of France?")
    assert res.outcome == "abstain" and res.reason == "out_of_scope" and retr.calls == [] and llm.calls == []


# ------------------------------------------------------------------ bounded loops
def test_insufficient_evidence_retries_once_then_abstains_without_calling_the_llm(synth_collection, catalog, tmp_path):
    llm = FakeLLM(["nope"])
    eng, retr = engine(synth_collection, catalog, tmp_path, llm)
    # 2.x is Swagger; the question is about a GitHub endpoint -> nothing in scope covers it.
    res = eng.ask("How do I create a release?", explicit_version="2.x")
    assert res.outcome == "abstain" and res.reason.startswith("insufficient_evidence")
    assert llm.calls == []
    assert [s["name"] for s in spans(tmp_path)].count("retrieve") == 1 + eng.deps.config.max_retrieval_retries


def test_fabricated_citation_is_retried_once_then_withheld(synth_collection, catalog, tmp_path):
    llm = FakeLLM(["Fact [fake:p9:spec:x:1]."])  # same bad reply forever
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "abstain" and res.reason.startswith("unverifiable_answer")
    assert len(llm.calls) == 1 + eng.deps.config.max_generation_retries == res.usage.llm_calls
    assert res.citations == []  # an unverified answer is never returned


def test_correction_is_fed_back_and_a_good_retry_is_accepted(synth_collection, catalog, tmp_path):
    llm = FakeLLM(["no citation here", f"Use assignee [{CID22}]."])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.usage.llm_calls == 2
    assert "rejected" in llm.calls[1][1]["content"] and "no citations" in llm.calls[1][1]["content"]


def test_a_claim_about_another_version_is_rejected_even_with_a_valid_citation(synth_collection, catalog, tmp_path):
    llm = FakeLLM([f"In {V25} you should use assignees [{CID22}].", f"Use assignee [{CID22}]."])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.usage.llm_calls == 2 and V25 not in res.answer


def test_model_abstention_is_honoured(synth_collection, catalog, tmp_path):
    llm = FakeLLM(["I don't know based on the provided documents."])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "abstain" and res.reason.startswith("model_abstained") and res.citations == []


def test_graph_step_limit_ends_cleanly(synth_collection, catalog, tmp_path):
    eng, _ = engine(synth_collection, catalog, tmp_path, FakeLLM([f"x [{CID22}]"]), max_graph_steps=3)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "error" and res.reason.startswith("step_limit")


# ------------------------------------------------------------------ fail closed / degrade
def test_scope_violation_withholds_the_answer_and_records_a_security_event(synth_collection, catalog, tmp_path):
    llm = FakeLLM([f"x [{CID22}]"])
    leaky = ChromaRetriever(LeakyCollection(synth_collection), hash_embed)
    eng, _ = engine(synth_collection, catalog, tmp_path, llm, retriever=leaky)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "error" and res.reason.startswith("scope_violation")
    assert llm.calls == [] and res.citations == []
    ev = [s for s in spans(tmp_path) if s["attributes"].get("security_event") == "scope_violation"]
    assert ev and ev[0]["status"] == "error"


@pytest.mark.parametrize("llm", [None, FakeLLM([], raises=LLMUnavailable("rate limited"))])
def test_without_a_model_the_engine_returns_cited_official_excerpts(synth_collection, catalog, tmp_path, llm):
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "degraded" and res.citations and "unavailable" in res.answer
    assert all(c.api_version == V22 for c in res.citations) and res.usage.llm_calls == 0


def test_degraded_comparison_answer_names_both_versions(synth_collection, catalog, tmp_path):
    eng, _ = engine(synth_collection, catalog, tmp_path, None)
    res = eng.ask(f"How do I migrate create_issue from {V22} to {V25}?")
    assert res.outcome == "degraded" and f"{V22} and {V25}" in res.answer


def test_unpriced_model_reports_cost_unavailable_not_zero(synth_collection, catalog, tmp_path):
    llm = FakeLLM([f"Use assignee [{CID22}]."], model="groq/compound")
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.usage.cost_status == "unavailable" and res.usage.cost_usd is None and res.usage.llm_calls == 1


# ------------------------------------------------------------------ validation and privacy
@pytest.mark.parametrize("q", ["", "   \n\t ", None, 123, "x" * 5000])
def test_invalid_questions_never_reach_retrieval_or_the_llm(synth_collection, catalog, tmp_path, q):
    llm = FakeLLM(["nope"])
    eng, retr = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(q)
    assert res.outcome == "error" and res.reason.startswith("invalid_request")
    assert retr.calls == [] and llm.calls == []


def test_a_secret_in_the_question_never_reaches_the_logs(synth_collection, catalog, tmp_path):
    llm = FakeLLM([f"Use assignee [{CID22}]."])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    eng.ask(f"My key is {GROQ} and I use {V22}; how do I create an issue?")
    blob = (tmp_path / "spans.jsonl").read_text(encoding="utf-8")
    assert GROQ not in blob
    validate = next(s for s in spans(tmp_path) if s["name"] == "validate_request")
    assert validate["attributes"]["redactions"].get("provider-key") == 1  # counts only, never values


def test_callers_trace_context_is_honoured(synth_collection, catalog, tmp_path):
    ctx = TraceContext(sink=JsonlSink(tmp_path))
    eng, _ = engine(synth_collection, catalog, tmp_path, FakeLLM([f"Use assignee [{CID22}]."]))
    res = eng.ask(f"On {V22} how do I create an issue?", trace=ctx)
    assert res.trace_id == ctx.trace_id and res.request_id == ctx.request_id


def test_prompt_example_is_a_real_retrieved_id_and_the_retry_lists_the_valid_ids(synth_collection, catalog, tmp_path):
    """Regression (first live run): the model prefixed ids with 'doc:' (copying our example) and every answer was rejected."""
    llm = FakeLLM([f"Use assignee [doc:{CID22}].", f"Use assignee [{CID22}]."])      # first reply makes the mistake seen live
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.usage.llm_calls == 2
    ids = res.retrieved_chunk_ids
    system = llm.calls[0][0]["content"]
    assert f"e.g. [{ids[0]}]" in system and "doc:p5" not in system
    retry = llm.calls[1][1]["content"]
    assert "not retrieved" in retry and "The only valid document ids are:" in retry and all(i in retry for i in ids)


def test_full_width_citation_brackets_from_the_model_are_accepted_and_shown_as_ascii(synth_collection, catalog, tmp_path):
    """Regression (first live run): a correct, grounded answer cited with 【id】 was discarded as 'no citations'."""
    llm = FakeLLM(["Pass assignee as a login string \u3010" + CID22 + "\u3011."])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.usage.llm_calls == 1
    assert f"[{CID22}]" in res.answer and "\u3010" not in res.answer
    assert [c.chunk_id for c in res.citations] == [CID22]


def test_an_answer_cut_off_at_the_token_limit_is_rejected_and_retried(synth_collection, catalog, tmp_path):
    """Regression (first live run): 700 max_tokens truncated an answer mid-citation and it was still returned."""
    llm = FakeLLM([f"Pass assignee [{CID22}] and then [", f"Pass assignee as a login string [{CID22}]."], finish=["length", "stop"])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.usage.llm_calls == 2 and res.answer.rstrip().endswith("login string [" + CID22 + "].")
    assert "cut off" in llm.calls[1][1]["content"]


def test_an_answer_that_keeps_getting_cut_off_is_withheld(synth_collection, catalog, tmp_path):
    llm = FakeLLM([f"Pass assignee [{CID22}] and then ["], finish=["length", "length"])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "abstain" and res.reason.startswith("unverifiable_answer") and "cut off" in res.reason


def test_the_default_token_budget_leaves_room_for_reasoning_models():
    assert EngineConfig().llm_max_tokens >= 1500


def test_hyphen_lookalikes_in_the_model_answer_are_repaired_before_verification_and_delivery(synth_collection, catalog, tmp_path):
    """Phase 9 finding: 17 of 35 live answers carried U+2011 inside identifiers/dates (`okhttp-gson` would not paste). The text that is verified is the text delivered."""
    nb = "\u2011"
    llm = FakeLLM([f"In {V22} the assignee{nb}field takes one login string; API version 2022{nb}11{nb}28 is what you use [{CID22}]."])
    eng, _ = engine(synth_collection, catalog, tmp_path, llm)
    res = eng.ask(f"My app uses API version {V22}. How do I create an issue with an assignee?")
    assert res.outcome == "answered" and nb not in res.answer
    assert "assignee-field" in res.answer and "2022-11-28" in res.answer
