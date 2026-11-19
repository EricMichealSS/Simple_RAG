"""Phase 6 in the graph: the draft's code blocks are validated against the caller's version before the answer is delivered.

The collection below is shaped like the REAL create-an-issue pages (parameter table, 'Required.' markers, a 2025 diff that says
`assignee` is deprecated in favor of `assignees`). The classic failure: a caller on 2022-11-28 gets a sample using `assignees`,
a field that only the 2025-06-01 docs document. `verify_answer` alone never catches it (no version string appears in the code)."""

import json
import uuid

import chromadb
import pytest

from conftest import hash_embed
from sdk_engine.config import EngineConfig
from sdk_engine.graph import Deps, Engine
from sdk_engine.llm import LLMResult
from sdk_engine.mcp_client import ToolResult
from sdk_engine.retrieval import ChromaRetriever
from sdk_engine.symbols import SymbolIndex
from sdk_engine.trace import JsonlSink
from test_graph_tools import Gateway, real_registry
from test_security_adversarial import Obedient, cid, upload_rig

V22, V25 = "2022-11-28", "2025-06-01"
CID = "gh:p4:spec:create-an-issue:0"
Q = f"On API version {V22}, how do I set the assignee when creating an issue? Show a curl example."
REAL_LIKE = [
    (CID, V22, "create_issue", "spec", 4,
     "Create an issue POST /repos/{owner}/{repo}/issues Parameters owner path string Required. repo path string Required. "
     "title body string or integer Required. The title of the issue. assignee body string Login for the user that this issue "
     "should be assigned to. labels body array of strings Labels to associate with this issue."),
    ("gh:p5:spec:create-an-issue:1", V25, "create_issue", "spec", 5,
     "Create an issue POST /repos/{owner}/{repo}/issues Parameters owner path string Required. repo path string Required. "
     "title body string or integer Required. assignees body array of strings Logins for Users to assign to this issue. "
     "type body string The name of the issue type."),
    ("gh:p5:diff:previous-version:2", V25, "create_issue", "diff", 5,
     "Previous version. assignee is deprecated in favor of assignees. The type field did not exist in 2022-11-28."),
]


def collection():
    col = chromadb.EphemeralClient().create_collection(name=f"c_{uuid.uuid4().hex[:10]}", metadata={"hnsw:space": "cosine"})
    col.add(ids=[r[0] for r in REAL_LIKE], documents=[r[5] for r in REAL_LIKE], embeddings=[hash_embed(r[5]) for r in REAL_LIKE],
            metadatas=[{"source_file": "github_combined_reference.pdf", "page": r[4], "anchor": "top", "content_type": r[3],
                        "api_version": r[1], "endpoint": r[2], "has_diff": r[3] == "diff"} for r in REAL_LIKE])
    return col


class Scripted:
    """An answer model that replies from a list (the last reply repeats) and remembers every prompt it was given."""
    model = "openai/gpt-oss-120b"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, messages, *, max_tokens, temperature=0.0):
        self.prompts.append(messages[1]["content"])
        text = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        return LLMResult(text=text, prompt_tokens=60, completion_tokens=20, model=self.model)


class CountingIndex:
    """Wraps a SymbolIndex and records the lookups the graph made in-process."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def check(self, version, symbol, endpoint=None):
        self.calls.append((version, symbol, endpoint))
        return self.inner.check(version, symbol, endpoint)


def ans(code, lang="json"):
    return f"Set the field as shown [{CID}].\n\n```{lang}\n{code}\n```"


def make(tmp_path, catalog, replies, *, gateway=None, symbols=True, llm=None):
    col = collection()
    idx = CountingIndex(SymbolIndex.from_collection(col, catalog)) if symbols else None
    llm = llm or Scripted(replies)
    eng = Engine(Deps(config=EngineConfig(trace_dir=tmp_path), catalog=catalog, retriever=ChromaRetriever(col, hash_embed), llm=llm,
                      sink=JsonlSink(tmp_path), tools=gateway(col) if gateway else None, symbols=idx))
    return eng, llm, idx


def spans(tmp_path):
    return [json.loads(line) for line in (tmp_path / "spans.jsonl").read_text(encoding="utf-8").splitlines()]


GOOD = '{"assignee": "octocat", "labels": ["bug"]}'
WRONG_VERSION = '{"assignees": ["octocat"]}'


# ------------------------------------------------------------------ the headline behaviour
def test_a_wrong_version_field_is_not_delivered_and_the_user_is_told_why(catalog, tmp_path):
    eng, llm, _ = make(tmp_path, catalog, [ans(WRONG_VERSION)])
    res = eng.ask(Q)
    assert res.outcome == "abstain" and res.reason.startswith("unverifiable_code")
    assert "`assignees` is not documented for 2022-11-28 (it is documented for 2025-06-01)" in res.answer
    assert res.citations == [] and res.code_validation.hard == 1
    assert len(llm.prompts) == 1 + EngineConfig().max_generation_retries                  # one correction retry, then withheld
    assert "Your previous answer was rejected" in llm.prompts[1] and "assignees" in llm.prompts[1]


def test_without_any_symbol_evidence_the_same_wrong_sample_is_delivered_but_flagged_as_unchecked(catalog, tmp_path):
    """The lookup is what catches it: with no index and no tool the engine cannot know, and says so instead of guessing."""
    eng, _, _ = make(tmp_path, catalog, [ans(WRONG_VERSION)], symbols=False)
    res = eng.ask(Q)
    assert res.outcome == "answered" and res.code_validation.symbol_source == "none"
    f = res.code_validation.blocks[0].findings[0]
    assert f.tier == "not_checked" and "no symbol index" in f.detail
    assert "Not checked:" in res.answer


def test_a_correct_sample_is_delivered_with_a_static_check_footer_and_a_structured_report(catalog, tmp_path):
    eng, llm, _ = make(tmp_path, catalog, [ans(GOOD)])
    res = eng.ask(Q)
    assert res.outcome == "answered" and res.usage.llm_calls == 1 and len(llm.prompts) == 1
    assert "Code check (static, against the documents; nothing was executed):" in res.answer
    assert "block 1 (json): syntax parsed" in res.answer
    b = res.code_validation.blocks[0]
    assert b.language == "json" and b.syntax == "parsed" and {f.symbol for f in b.findings} == {"assignee", "labels"}
    assert res.code_validation.hard == res.code_validation.soft == 0


def test_a_deprecated_field_is_surfaced_inside_the_answer(catalog, tmp_path):
    """`assignee` is fine for 2022-11-28 but the 2025 docs deprecate it: the caller hears it in the same answer."""
    eng, _, _ = make(tmp_path, catalog, [ans(GOOD)])
    res = eng.ask(Q)
    assert any(n.cross_version and n.applies_to_version == V25 for n in res.deprecation_notes)   # the existing upgrade note


# ------------------------------------------------------------------ soft findings: one chance, never an abstention
def test_an_unverified_identifier_gets_one_correction_retry_and_the_fixed_answer_is_delivered(catalog, tmp_path):
    eng, llm, _ = make(tmp_path, catalog, [ans('{"assignee": "octocat", "zzz_unknown": 1}'), ans(GOOD)])
    res = eng.ask(Q)
    assert res.outcome == "answered" and len(llm.prompts) == 2
    assert "zzz_unknown" in llm.prompts[1] and "unverified" not in res.answer


def test_an_unverified_identifier_that_survives_the_retry_is_shown_as_a_warning_not_turned_into_an_abstention(catalog, tmp_path):
    eng, llm, _ = make(tmp_path, catalog, [ans('{"assignee": "octocat", "zzz_unknown": 1}')])
    res = eng.ask(Q)
    assert res.outcome == "answered" and len(llm.prompts) == 1 + EngineConfig().max_generation_retries
    assert "unverified: `zzz_unknown`" in res.answer and res.code_validation.soft == 1


def test_a_python_sample_that_does_not_parse_is_never_delivered(catalog, tmp_path):
    eng, _, _ = make(tmp_path, catalog, [ans("def f(:\n    pass", "python")])
    res = eng.ask(Q)
    assert res.outcome == "abstain" and "a python sample did not parse" in res.answer


# ------------------------------------------------------------------ cost / wiring
def test_an_answer_without_code_makes_no_lookup_adds_no_span_and_no_report(catalog, tmp_path):
    eng, _, idx = make(tmp_path, catalog, [f"Pass assignee as a login string [{CID}]."])
    res = eng.ask(Q)
    assert res.outcome == "answered" and res.code_validation is None and idx.calls == []
    assert "Code check" not in res.answer and "code_validation" not in {s["name"] for s in spans(tmp_path)}


def test_the_validation_is_traced_with_its_outcome(catalog, tmp_path):
    eng, _, _ = make(tmp_path, catalog, [ans(GOOD)])
    res = eng.ask(Q)
    span = next(s for s in spans(tmp_path) if s["name"] == "code_validation")
    a = span["attributes"]
    assert span["trace_id"] == res.trace_id and a["blocks"] == 1 and a["hard"] == 0 and a["languages"] == ["json"]
    assert a["symbol_source"] == "none" and a["lookups"] == 0          # everything was in the cited passage: nothing to look up


def test_uploaded_documents_are_not_validated_against_versions(catalog, tmp_path):
    llm = Obedient(lambda m: f"Here [{cid(m)}]:\n```json\n{{\"zzz_unknown\": 1}}\n```")
    eng, store = upload_rig(tmp_path, catalog, llm)
    store.ingest("alice", "zoo.md", b"# Zoo\n\nThe zebra enclosure was repainted in March.\n")
    res = eng.ask("What does my uploaded document say about the zebra enclosure?", owner_id="alice")
    assert res.outcome == "answered" and res.code_validation is None and "Code check" not in res.answer


def test_an_explicit_comparison_accepts_the_field_that_exists_in_the_other_version(catalog, tmp_path):
    eng, _, _ = make(tmp_path, catalog, [ans('{"assignees": ["octocat"]}')])
    res = eng.ask(f"Compare creating an issue between {V22} and {V25}: how is the assignee set?")
    assert res.version.is_comparison and res.outcome == "answered" and res.code_validation.hard == 0


# ------------------------------------------------------------------ the MCP check_symbol tool
def tool_result(name, finding):
    return ToolResult(ok=True, tool=name, server="sdk-docs", data={"ok": True, **finding.model_dump()})


def gateway_with(handler):
    return lambda col: Gateway(col, real_registry(), override=handler)


def test_symbol_checks_go_through_the_validated_mcp_tool_with_the_version_enforced_by_the_graph(catalog, tmp_path):
    col_idx = {}

    def handler(name, args):
        if name != "check_symbol":
            return None
        col_idx.setdefault("n", []).append(dict(args))
        return tool_result(name, col_idx["index"].check(args["version"], args["symbol"], args.get("endpoint")))

    eng, _, idx = make(tmp_path, catalog, [ans(WRONG_VERSION)], gateway=gateway_with(handler))
    col_idx["index"] = idx.inner
    gw = eng.deps.tools
    res = eng.ask(Q)
    assert res.outcome == "abstain" and res.code_validation.symbol_source == "mcp" and idx.calls == []   # the tool served it
    assert {"symbol": "assignees", "endpoint": "create_issue", "version": V22} in col_idx["n"]
    assert ("check_symbol", {"symbol": "assignees", "endpoint": "create_issue", "version": V22}) in gw.calls


def test_a_failing_tool_falls_back_to_the_in_process_index(catalog, tmp_path):
    def handler(name, args):
        return ToolResult(ok=False, tool=name, server="sdk-docs", error_category="timeout", error_message="slow") if name == "check_symbol" else None

    eng, _, idx = make(tmp_path, catalog, [ans(WRONG_VERSION)], gateway=gateway_with(handler))
    res = eng.ask(Q)
    assert res.outcome == "abstain" and res.code_validation.symbol_source == "local" and idx.calls
    span = next(s for s in spans(tmp_path) if s["name"] == "code_validation")
    assert span["attributes"]["mcp_fallbacks"] >= 1


def test_a_tool_answer_for_the_wrong_version_is_refused_not_believed(catalog, tmp_path):
    """A server that vouches for `assignees` with a 2025-06-01 finding must not make a 2022-11-28 sample pass."""
    from sdk_engine.symbols import SymbolFinding

    def handler(name, args):
        if name != "check_symbol":
            return None
        return tool_result(name, SymbolFinding(version=V25, symbol=args["symbol"], status="present_in_docs"))

    eng, _, idx = make(tmp_path, catalog, [ans(WRONG_VERSION)], gateway=gateway_with(handler))
    res = eng.ask(Q)
    assert res.outcome == "abstain" and res.code_validation.symbol_source == "local"        # the lie was discarded
    assert "`assignees` is not documented for 2022-11-28" in res.answer
