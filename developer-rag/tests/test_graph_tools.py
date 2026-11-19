"""Graph + MCP tools. A scripted in-process gateway with the REAL tool schemas exercises decision, validation,
fallback and fail-closed behaviour deterministically (no subprocess)."""

import asyncio
import json

import pytest

from conftest import hash_embed
from sdk_engine import mcp_server
from sdk_engine.config import EngineConfig
from sdk_engine.graph import Deps, Engine
from sdk_engine.llm import LLMResult
from sdk_engine.mcp_client import ToolResult
from sdk_engine.retrieval import ChromaRetriever, RetrievalScope
from sdk_engine.tool_policy import ServerConfig, build_registry
from sdk_engine.trace import JsonlSink

V22, V25 = "2022-11-28", "2025-06-01"
CID22, CID25 = "gh:p4:spec:create-an-issue:0", "gh:p5:spec:create-an-issue:1"
ALL = ("search_docs", "lookup_changes", "check_symbol")


def real_registry(allowed=ALL):
    tools = asyncio.run(mcp_server.mcp.list_tools())   # the schemas a real client would discover
    return build_registry(ServerConfig(name="sdk-docs", command="x", allowed_tools=tuple(allowed)), tools)


class FakeLLM:
    model = "openai/gpt-oss-120b"

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def complete(self, messages, *, max_tokens, temperature=0.0):
        self.calls += 1
        return LLMResult(text=self.reply, prompt_tokens=50, completion_tokens=10, model=self.model)


class Gateway:
    """Implements the ToolGateway protocol with scripted behaviour over the real synthetic collection."""

    def __init__(self, col, registry, override=None):
        self._reg, self._override, self.calls = registry, override, []
        self._retr = ChromaRetriever(col, hash_embed, max_top_k=10, hybrid=False)

    def tools(self):
        return self._reg

    def call(self, name, arguments, *, trace=None, timeout_s=None):
        self.calls.append((name, dict(arguments)))
        if self._override:
            out = self._override(name, dict(arguments))
            if out is not None:
                return out
        if name == "search_docs":
            res = self._retr.search(arguments["query"], RetrievalScope(versions=(arguments["version"],)), arguments.get("top_k", 5))
            data = {"ok": True, "version": arguments["version"], "truncated": False,
                    "passages": [mcp_server._passage_dict(p, [10 ** 9]) for p in res.passages]}
            return ToolResult(ok=True, tool=name, server="sdk-docs", data=data)
        if name == "lookup_changes":
            mine = arguments["version"]
            versions = (mine, V25) if mine == V22 else (mine,)
            scope = (RetrievalScope(mode="comparison", versions=versions, content_types=("diff",)) if len(versions) > 1
                     else RetrievalScope(versions=versions, content_types=("diff",)))
            res = self._retr.fetch(scope)
            changes = []
            for p in res.passages:
                d = mcp_server._passage_dict(p, [10 ** 9])
                d["applies_to_version"], d["cross_version"] = p.api_version, p.api_version != mine
                changes.append(d)
            facts = [{"version": V25, "deprecated": "assignee", "replacement": "assignees", "chunk_id": CID25,
                      "endpoint": "create_issue", "cross_version": mine != V25}]
            return ToolResult(ok=True, tool=name, server="sdk-docs", data={"ok": True, "changes": changes, "deprecations": facts})
        raise AssertionError(f"unexpected tool {name}")


def engine(col, catalog, tmp_path, gateway=None, retriever=None, llm=None):
    cfg = EngineConfig(trace_dir=tmp_path)
    return Engine(Deps(config=cfg, catalog=catalog, retriever=retriever, llm=llm or FakeLLM(f"Use assignee [{CID22}]."),
                       sink=JsonlSink(tmp_path), tools=gateway))


def spans(tmp_path):
    return [json.loads(line) for line in (tmp_path / "spans.jsonl").read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------------ decide -> validate -> call
def test_retrieval_uses_the_validated_search_tool_and_the_graph_owns_the_version(synth_collection, catalog, tmp_path):
    gw = Gateway(synth_collection, real_registry())
    res = engine(synth_collection, catalog, tmp_path, gw).ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.retrieval_via == "mcp"
    name, args = gw.calls[0]
    assert name == "search_docs" and args["version"] == V22 and set(args) == {"query", "top_k", "version"}
    assert {c.api_version for c in res.citations} == {V22}


def test_changelog_goes_through_lookup_changes_and_surfaces_structured_deprecations(synth_collection, catalog, tmp_path):
    gw = Gateway(synth_collection, real_registry())
    res = engine(synth_collection, catalog, tmp_path, gw).ask(f"On {V22} how do I create an issue?")
    assert [n for n, _ in gw.calls] == ["search_docs", "lookup_changes"]
    assert gw.calls[1][1] == {"version": V22, "endpoint": "create_issue"}      # caller's version; endpoint from the rule router
    assert res.deprecation_facts and res.deprecation_facts[0]["replacement"] == "assignees" and res.deprecation_facts[0]["cross_version"]
    assert res.deprecation_notes and all(n.cross_version and n.applies_to_version == V25 for n in res.deprecation_notes)


def test_comparison_is_a_union_of_strictly_scoped_calls_labelled_relative_to_the_callers_version(synth_collection, catalog, tmp_path):
    gw = Gateway(synth_collection, real_registry())
    llm = FakeLLM(f"Before [{CID22}]; now [{CID25}].")
    res = engine(synth_collection, catalog, tmp_path, gw, llm=llm).ask(f"How do I migrate create_issue from {V22} to {V25}?")
    searches = [a for n, a in gw.calls if n == "search_docs"]
    assert [a["version"] for a in searches] == [V22, V25]                      # one strict call per version, never a mixed call
    assert res.outcome == "answered" and {c.api_version for c in res.citations} == {V22, V25}


# ------------------------------------------------------------------ discovery is not authorization
def test_an_unauthorized_search_tool_is_never_called_and_the_graph_falls_back_locally(synth_collection, catalog, tmp_path):
    gw = Gateway(synth_collection, real_registry(allowed=("check_symbol",)))    # operator did not allow search_docs
    local = ChromaRetriever(synth_collection, hash_embed)
    res = engine(synth_collection, catalog, tmp_path, gw, retriever=local).ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.retrieval_via == "local"
    assert gw.calls == []                                                       # nothing was sent to the unauthorized tool
    sp = next(s for s in spans(tmp_path) if s["name"] == "retrieve")
    assert sp["attributes"]["mcp_fallback"] == "unauthorized_tool"


def test_without_any_backend_the_failure_is_explicit(synth_collection, catalog, tmp_path):
    gw = Gateway(synth_collection, real_registry(allowed=()))
    res = engine(synth_collection, catalog, tmp_path, gw, retriever=None).ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "error" and res.reason.startswith("no_retrieval_backend")


@pytest.mark.parametrize("category", ["timeout", "transport_error", "output_too_large", "malformed_result", "tool_error"])
def test_tool_failures_fall_back_to_the_local_retriever(synth_collection, catalog, tmp_path, category):
    def fail(name, args):
        return ToolResult(ok=False, tool=name, server="sdk-docs", error_category=category, error_message="boom")

    gw = Gateway(synth_collection, real_registry(), override=fail)
    local = ChromaRetriever(synth_collection, hash_embed)
    res = engine(synth_collection, catalog, tmp_path, gw, retriever=local).ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.retrieval_via == "local"
    assert next(s for s in spans(tmp_path) if s["name"] == "retrieve")["attributes"]["mcp_fallback"] == category


def test_a_malformed_passage_from_a_tool_is_a_fallback_not_a_crash(synth_collection, catalog, tmp_path):
    def junk(name, args):
        return ToolResult(ok=True, tool=name, server="sdk-docs", data={"ok": True, "passages": [{"chunk_id": 5, "text": None}]})

    gw = Gateway(synth_collection, real_registry(), override=junk)
    res = engine(synth_collection, catalog, tmp_path, gw, retriever=ChromaRetriever(synth_collection, hash_embed)).ask(
        f"On {V22} how do I create an issue?")
    assert res.outcome == "answered" and res.retrieval_via == "local"


# ------------------------------------------------------------------ a lying server is a security event
def test_wrong_version_chunks_from_a_tool_fail_closed_and_are_not_papered_over_by_falling_back(synth_collection, catalog, tmp_path):
    def lie(name, args):
        if name != "search_docs":
            return None
        p = mcp_server._passage_dict(ChromaRetriever(synth_collection, hash_embed).search(
            "create an issue", RetrievalScope(versions=(V25,)), 1).passages[0], [10 ** 9])   # a 2025-06-01 chunk for a 2022 call
        return ToolResult(ok=True, tool=name, server="sdk-docs", data={"ok": True, "passages": [p]})

    gw = Gateway(synth_collection, real_registry(), override=lie)
    local_calls = []

    class SpyLocal:
        def search(self, *a, **k):
            local_calls.append(a)
            raise AssertionError("must not fall back after a scope violation")

    llm = FakeLLM("x")
    res = engine(synth_collection, catalog, tmp_path, gw, retriever=SpyLocal(), llm=llm).ask(f"On {V22} how do I create an issue?")
    assert res.outcome == "error" and res.reason.startswith("scope_violation")
    assert local_calls == [] and llm.calls == 0 and res.citations == []
    ev = [s for s in spans(tmp_path) if s["attributes"].get("security_event") == "scope_violation"]
    assert ev and ev[0]["status"] == "error"


def test_a_top_k_above_the_tools_maximum_is_clamped_instead_of_being_rejected_into_a_silent_fallback(synth_collection, tmp_path):
    """Found by review: the graph doubles top_k on a retry (up to 20); the tool schema stops at 10, so such a call was rejected as
    `invalid_arguments` and the request quietly fell back to the in-process retriever."""
    from sdk_engine import toolbox
    from sdk_engine.trace import TraceContext

    assert toolbox.SEARCH_TOP_K_LIMIT == mcp_server.MAX_TOP_K                    # the clamp cannot drift from the schema
    gw = Gateway(synth_collection, real_registry())
    passages = toolbox.search_via_tools(gw, TraceContext(sink=JsonlSink(tmp_path)), "create an issue assignee", [V22], 20)
    assert [a["top_k"] for n, a in gw.calls if n == "search_docs"] == [10] and passages
    assert toolbox.search_via_tools(gw, TraceContext(sink=JsonlSink(tmp_path)), "create an issue", [V22], 0) is not None   # a floor of 1, never 0
    assert gw.calls[-1][1]["top_k"] == 1
