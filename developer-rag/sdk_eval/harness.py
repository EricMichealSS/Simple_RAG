"""Harness for the integrated evaluation suite: builds REAL engines (real index, real local embeddings, optionally the real MCP server
subprocess and the real model) and records what each case did.

Design rules:
* Deterministic assertions decide pass/fail. A model is used only where the case is about the model's behaviour (tier `live`), or is
  SCRIPTED to simulate a specific behaviour (an obedient model, a model that writes a wrong-version sample). A scripted model is named
  `scripted/eval-fixture`, which is not in the price table, so its cost is `unavailable` (never a made-up number) and it is excluded from
  the live-only metrics (citation correctness, wrong-version rate).
* One shared trace directory per run, so `python -m sdk_engine trace ...` can inspect a suite run like any other traffic.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import chromadb

from sdk_engine.config import EngineConfig, get_api_key
from sdk_engine.graph import Deps, Engine
from sdk_engine.llm import GroqChat, LLMResult, LLMUnavailable
from sdk_engine.mcp_client import McpToolClient, ToolResult, default_server_config
from sdk_engine.retrieval import ChromaRetriever, make_embed_fn, make_embed_many_fn, open_collection
from sdk_engine.schemas import AnswerResult
from sdk_engine.sqlite_store import SqliteUploadClient
from sdk_engine.symbols import SymbolIndex
from sdk_engine.trace import JsonlSink
from sdk_engine.uploads import UploadStore
from sdk_engine.versioning import VersionCatalog

SCRIPTED_MODEL = "scripted/eval-fixture"


class Skip(Exception):
    """The case needs something this run does not have (a live model); it is reported as skipped, never as passed or failed."""


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


# ------------------------------------------------------------------------------------------------ models
class Scripted:
    """A model that says exactly what the case scripts. Used to simulate a behaviour (obedience, a wrong-version sample), not to
    measure one. Tokens are 0 and the model is unpriced: nothing here pretends to be a measured cost."""

    model = SCRIPTED_MODEL
    provider = "scripted"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, messages, *, max_tokens, temperature=0.0):
        self.calls.append(messages)
        text = self.reply(messages) if callable(self.reply) else self.reply
        return LLMResult(text=text, prompt_tokens=0, completion_tokens=0, model=self.model)


class PacedLLM:
    """The real model, paced to the key's measured limit (8,000 tokens/minute: about 3-4 answers a minute at ~2.2k tokens each) and
    retried once after a rate-limit error. A second failure propagates, so the case sees a degraded answer and fails honestly."""

    provider = "groq"

    def __init__(self, inner: GroqChat, pace_s: float):
        self.inner, self.pace_s, self.model, self._last = inner, pace_s, inner.model, 0.0

    def complete(self, messages, *, max_tokens, temperature=0.0):
        wait = self._last + self.pace_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            try:
                return self.inner.complete(messages, max_tokens=max_tokens, temperature=temperature)
            except LLMUnavailable as exc:
                if "RateLimit" not in str(exc):
                    raise
                time.sleep(45)
                return self.inner.complete(messages, max_tokens=max_tokens, temperature=temperature)
        finally:
            self._last = time.monotonic()


def first_chunk_id(messages) -> str:
    found = re.findall(r'<document id="([^"]+)"', messages[1]["content"])
    return found[0] if found else "no:document:given"


def reply_with(code: str, lang: str = "json", prefix: str = "Set it as shown"):
    """A scripted answer: a prose line citing the first retrieved document, then one fenced code block."""
    return lambda m: f"{prefix} [{first_chunk_id(m)}].\n\n```{lang}\n{code}\n```"


# ------------------------------------------------------------------------------------------------ MCP doubles
class TimeoutGateway:
    """An MCP gateway whose tools were discovered normally but whose every call times out (the server hung). Schemas come from the
    real client's registry, so selection and validation behave exactly as in production up to the call itself."""

    def __init__(self, registry):
        self._reg, self.calls = registry, []

    def tools(self):
        return self._reg

    def call(self, name, arguments, *, trace=None, timeout_s=None):
        self.calls.append((name, dict(arguments)))
        result = ToolResult(ok=False, tool=name, server="sdk-docs", error_category="timeout", error_message="no result within the limit")
        if trace is not None:        # the real client records an `mcp.call` span for every call, failed ones included; the double does too
            with trace.span("mcp.call", tool=name, arg_keys=sorted(arguments), arg_chars=0) as sp:
                sp.set(server="sdk-docs", ok=False, latency_ms=0.0, truncated=False, error_category="timeout")
        return result


class LeakyCollection:
    """A vector store whose metadata filter is broken: it ignores `where` and returns everything. Nothing may trust it."""

    def __init__(self, real):
        self._r = real

    def get(self, where=None, include=None, limit=None):
        return self._r.get(include=include)

    def query(self, query_embeddings, n_results, where=None, include=None):
        return self._r.query(query_embeddings=query_embeddings, n_results=n_results, include=include)


# ------------------------------------------------------------------------------------------------ environment
class Env:
    def __init__(self, trace_dir: Path, *, live: bool, pace_s: float = 17.0, cfg: Optional[EngineConfig] = None):
        base = cfg or EngineConfig.from_env()
        self.trace_dir = Path(trace_dir)
        self.cfg = base.model_copy(update={"trace_dir": self.trace_dir})
        self.catalog = VersionCatalog.from_page_metadata(self.cfg.page_metadata_path)
        self.collection = open_collection(self.cfg)
        self.embed = make_embed_fn(self.cfg.embedding_model)
        self.embed_many = make_embed_many_fn(self.cfg.embedding_model)
        self.symbols = SymbolIndex.from_collection(self.collection, self.catalog)
        got = self.collection.get(include=["metadatas"])
        self.chunk_versions: Dict[str, Optional[str]] = {i: (m or {}).get("api_version") for i, m in zip(got["ids"], got["metadatas"])}
        self.sink = JsonlSink(self.trace_dir)
        self.live = bool(live and get_api_key())
        self.pace_s = pace_s
        self._live_llm: Optional[PacedLLM] = None
        self._mcp: Optional[McpToolClient] = None
        self.workdir = Path(tempfile.mkdtemp(prefix="sdk_eval_"))

    # -- resources shared by cases
    def live_llm(self) -> PacedLLM:
        if not self.live:
            raise Skip("needs the live model (no API key, or --live was not given)")
        if self._live_llm is None:
            self._live_llm = PacedLLM(GroqChat(self.cfg), self.pace_s)
        return self._live_llm

    def raw_llm(self) -> GroqChat:
        """The real model WITHOUT pacing inside the call. Used for latency measurement: the caller spaces whole requests apart instead, so a
        request's own latency is not inflated by an artificial wait (PacedLLM sleeps inside `complete`, which would show up in every span)."""
        if not self.live:
            raise Skip("needs the live model (no API key, or --live was not given)")
        return GroqChat(self.cfg)

    def mcp(self) -> McpToolClient:
        if self._mcp is None:
            self._mcp = McpToolClient([default_server_config(self.cfg)], call_timeout_s=self.cfg.tool_timeout_s,
                                      max_output_chars=self.cfg.max_tool_output_chars, log_dir=self.trace_dir, sink=self.sink).start()
        return self._mcp

    def close(self) -> None:
        if self._mcp is not None:
            self._mcp.close()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def retriever(self, collection=None) -> ChromaRetriever:
        return ChromaRetriever(collection or self.collection, self.embed, max_top_k=self.cfg.max_top_k, hybrid=self.cfg.hybrid_retrieval)

    def engine(self, *, llm=None, tools=None, retriever=None, uploads=None, symbols: Any = "env", collection=None) -> Engine:
        return Engine(Deps(config=self.cfg, catalog=self.catalog, retriever=retriever or self.retriever(collection), llm=llm, sink=self.sink,
                           tools=tools, uploads=uploads, symbols=self.symbols if symbols == "env" else symbols))

    def upload_store(self, name: str = "u") -> UploadStore:
        cfg = self.cfg.model_copy(update={"upload_collection_prefix": f"ev{abs(hash((name, time.time()))) % 10**8}_"})
        return UploadStore(SqliteUploadClient(self.workdir / f"{name}.sqlite3"), self.embed, self.embed_many, cfg)

    def augmented_collection(self, extras: Sequence[Tuple[str, str, Dict[str, Any]]]):
        """The real collection plus extra chunks (a CONTROLLED FIXTURE: e.g. a poisoned page). Returns (collection, {id: version})."""
        got = self.collection.get(include=["documents", "metadatas", "embeddings"])
        col = chromadb.EphemeralClient().create_collection(name=f"aug_{abs(hash(time.time())) % 10**10}", metadata={"hnsw:space": "cosine"})
        ids, docs, metas, embs = list(got["ids"]), list(got["documents"]), list(got["metadatas"]), [list(e) for e in got["embeddings"]]
        for cid, text, meta in extras:
            ids.append(cid), docs.append(text), metas.append(meta), embs.append(self.embed(text))
        col.add(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
        return col, {cid: meta.get("api_version") for cid, _, meta in extras}

    def version_endpoint_pairs(self) -> List[Tuple[str, str]]:
        import json

        meta = json.loads(self.cfg.page_metadata_path.read_text(encoding="utf-8"))
        found = set()
        for fname, pages in meta.items():
            if fname.startswith("_") or fname.startswith("known_"):
                continue
            for pm in pages.values():
                found.add((pm["api_version"], pm["endpoint"]))
        return sorted(found)


# ------------------------------------------------------------------------------------------------ per-case recorder
class Rec:
    """What a case did. `ask` builds a real engine, asks, and tallies the metrics the report needs (retrieval version correctness for
    every ask; citation correctness only for answers from the live model)."""

    def __init__(self, env: Env, spec: Any):
        self.env, self.spec = env, spec
        self.checks: List[Check] = []
        self.results: List[AnswerResult] = []
        self.notes: Dict[str, Any] = {}
        self.retrieved = self.wrong_version_chunks = 0
        self.live_code_samples = self.live_wrong_version_samples = 0
        self.citations_total = self.citations_bad = 0
        self.extra_versions: Dict[str, Optional[str]] = {}
        self.questions: Dict[str, str] = {}          # trace id -> the question that produced it (kept in the run's outputs, never in a trace)

    def check(self, name: str, ok: Any, detail: Any = "") -> bool:
        self.checks.append(Check(name, bool(ok), str(detail)[:300]))
        return bool(ok)

    def tally_retrieval(self, chunk_ids: Sequence[str], allowed: Sequence[str]) -> None:
        for cid in chunk_ids:
            v = self.extra_versions.get(cid, self.env.chunk_versions.get(cid))
            self.retrieved += 1
            self.wrong_version_chunks += v not in allowed

    def ask(self, question: str, *, llm=None, tools=None, owner_id=None, scope="auto", explicit_version=None, retriever=None,
            uploads=None, symbols: Any = "env", collection=None) -> AnswerResult:
        eng = self.env.engine(llm=llm, tools=tools, retriever=retriever, uploads=uploads, symbols=symbols, collection=collection)
        res = eng.ask(question, explicit_version=explicit_version, owner_id=owner_id, scope=scope)
        self.results.append(res)
        self.questions[res.trace_id] = question
        self._tally(res)
        return res

    def _tally(self, res: AnswerResult) -> None:
        v = res.version
        if res.corpus != "official" or v is None or v.version is None:
            return
        allowed = list(v.comparison_versions) if v.is_comparison else [v.version]
        self.tally_retrieval(res.retrieved_chunk_ids, allowed)
        if res.outcome != "answered" or (res.usage.model or "").startswith("scripted"):
            return                                                    # citations/code are judged only for the live model's answers
        for c in res.citations:
            self.citations_total += 1
            self.citations_bad += not (c.chunk_id in res.retrieved_chunk_ids and c.api_version in allowed and c.source_name and c.location)
        report = res.code_validation
        if report is not None and report.blocks:
            self.live_code_samples += 1
            self.live_wrong_version_samples += independent_wrong_version(self.env, res, allowed)


def independent_wrong_version(env: Env, res: AnswerResult, allowed: Sequence[str]) -> int:
    """An oracle that does NOT trust the engine's own gating: re-validate the DELIVERED code blocks against the full symbol index with no
    passages (so nothing counts as 'cited'). 1 if any identifier is evidenced as belonging only to another version, else 0."""
    from sdk_engine import codecheck

    rep = codecheck.validate_code(res.answer, version=allowed[0], versions=list(allowed), passages=[],
                                  lookup=lambda v, s, e: env.symbols.check(v, s, e), question_endpoints=[])
    return int(bool(rep and any(f.tier in ("absent_per_docs", "other_version_only") for b in rep.blocks for f in b.findings)))
