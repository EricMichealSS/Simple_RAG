# Phase 1 — Contracts, configuration, redaction, tracing, LangGraph pipeline, CLI

Date: 2026-11-02 · Branch `week12_implementation` · **Status: COMPLETE for its scope** (typed contracts, config, redaction, tracing, bounded LangGraph pipeline with a pluggable LLM, one CLI entry point). What the graph does **not** yet contain (MCP discovery/tools, specialist nodes beyond retrieval/changelog, code validation, injection flagging, uploads) belongs to Phases 3–6 and is listed under limitations.

## Objective
Typed contracts and the cross-cutting safety plumbing (config without import-time secrets, redaction before logging, one trace id with parent-linked spans and a propagation carrier), so every later phase builds on tested foundations.

## Files inspected
See `phase0.md`. Additionally for this phase: `agent_race/page_metadata.json` (read in full — it is the source of truth for the version catalog), `agent_race/ingest.py`, `agent_race/questions.py`, `trajectory_specs.py`, `mcp_config.json`, `verify_docs_search_server.py`.

## Files created (all new, additive; no existing file's code was modified)
| File | Purpose |
|---|---|
| `developer-rag/sdk_engine/__init__.py` | Package doc + module map |
| `developer-rag/sdk_engine/config.py` | `EngineConfig` (paths anchored to the file, bounded limits, env loader, lazy API-key getter; **no secret read at import**) |
| `developer-rag/sdk_engine/redaction.py` | Conservative redaction: provider keys, bearer/auth headers, secret assignments, credentialled connection strings, private-key blocks, JWTs, email, SSN-shape, phone-shape, Luhn-checked cards; `redact_obj` with sensitive-key handling and depth limit |
| `developer-rag/sdk_engine/trace.py` | `TraceContext` (32-hex trace id, parent-linked spans via `contextvars`), `carrier()`/`from_carrier()` for cross-process propagation, redacting + truncating `JsonlSink`; sink failure never breaks a request |
| `developer-rag/sdk_engine/schemas.py` | Pydantic contracts: `VersionResolution` (with invariants), `Passage`, `Citation`, `RouteDecision`, enums |
| `developer-rag/pytest.ini` | Collects only `tests/` (legacy root `test_*.py` are live-index scripts, not pytest tests) |
| `developer-rag/tests/{conftest,test_redaction,test_trace}.py` | Foundation tests |
| `developer-rag/sdk_engine/graph.py` | LangGraph `StateGraph` (11 nodes, conditional edges, bounded loops) + `Engine.ask` request wrapper; the **only** model call is `generate` |
| `developer-rag/sdk_engine/answering.py` | Pure prompt construction (policy in the system message; passages quoted as untrusted data inside delimiters they cannot break out of) and deterministic answer verification (citations exist, in scope, no claims about other versions) |
| `developer-rag/sdk_engine/llm.py` | `LLMClient` protocol, `GroqChat` adapter (lazy client, never logs provider error text), `LLMUnavailable` |
| `developer-rag/sdk_engine/pricing.py` | Per-token cost with an explicit "unpriced" state (`None`, never a silent 0; fixes G-OBS-2 semantics) |
| `developer-rag/sdk_engine/factory.py`, `cli.py`, `__main__.py` | `build_engine`; `python -m sdk_engine {query,versions,health}` |
| `developer-rag/tests/{test_graph,test_answering}.py` | Graph and answer-verification tests |
Files modified: none. Files deleted: none. Untracked-only change set (nothing committed).

Environment changes (not tracked): git-ignored `.venv/` on Python 3.14.8 — `langgraph 1.2.19`, `pydantic 2.14.2`, `openai 3.42.1`, `mcp 1.30.0` (**pinned `<2`**), `chromadb 1.5.9`, `sentence-transformers 6.2.0`, `torch 2.15.0`, `pypdf 6.21.4`, `python-docx 1.2.0`, `python-dotenv 1.2.4`, `numpy 2.5.4`, `pytest 9.2.0`. `data/chroma` (git-ignored) now holds `race_docs` (39 chunks) built by the unmodified `agent_race/ingest.py`.

## Impact analysis (GitNexus, index re-built 2026-11-02 on this branch: 1,516 nodes / 2,337 edges / 120 flows)
| Symbol | Disambiguated result | Decision |
|---|---|---|
| `developer-rag/rag_hybrid.py:retrieve` | **HIGH** — 3 direct callers (`app.py`, `eval/generate.py:generate_answer`, `evaluate_hybrid.py`), 3 processes incl. the Week 6 `eval/run_eval.py:main` | **Do not edit in place.** New scoped retriever lives in `sdk_engine/retrieval.py` |
| `developer-rag/agent_race/agent.py:run_agent` | **CRITICAL** — 9 direct callers (`race.py`, `trajectory_eval.py`, `compare_one.py`, `squad/run_squad_race.py`, `squad/demo_live.py`, `week11/extract_trace.py`, `agent_optimized_experiment.py`, `budget_demo.py`, itself), 9 processes | **Do not edit in place.** Reuse its patterns; wrap, don't modify |
| `MCPToolRouter` | exact; imported by `squad/orchestrator_mcp.py`, `squad/squad_tools_server.py`; no processes | New allowlisting client will be a new class; the old one stays as is |
(An earlier statement of mine that nothing was HIGH/CRITICAL came from the ambiguous multi-candidate summary; the disambiguated numbers above are the authoritative ones.)

## Connections / data flow
`EngineConfig` → paths/limits for every module. `TraceContext.span(...)` → `redact_obj` + `truncate` → `JsonlSink` (`data/traces/spans.jsonl`, git-ignored via `data/`). `carrier()` → (Phase 3) MCP request metadata → server `from_carrier()`.

## Commands run and actual results
- `pytest -q` with `GROK_API_KEY` **unset**: **171 passed** = 168 unit + 3 integration (per file, from `pytest --collect-only`: answering 15, graph 25, redaction 33, retrieval_scope 37, trace 12, versioning 46, integration 3). Unit run ≈4 s; with integration ≈25–35 s (model load).
- **CLI against the real `race_docs` index, no API key** (`python -m sdk_engine …`, from `developer-rag/`): `health` → `status: ok`, 39 chunks, key present: false; `versions` lists both products; `query "On API version 2022-11-28, how do I set the assignee…"` → **degraded** (no model) answer with cited, version-labelled excerpts from the 2022-11-28 page plus a labelled *upgrade note* from the 2025-06-01 page; `query "How do I create an issue?"` → **clarify** (asks which version, 0 retrieval, 0 LLM calls); `query "What is the capital of France?"` → **abstain** (out of scope); migration question → degraded answer naming both versions. (That CLI run was made before a key existed, hence the degraded answers; the live-model runs are in the addendum at the end of this file.) Run time ≈1.3–2.2 s.
- **Mutation checks** (deliberate bugs applied via a throwaway pytest plugin, never committed): widened version filter → many failures; scope check disabled → exactly its 2 dedicated tests fail; redaction rules removed → redaction + trace tests fail; resolver that substitutes the nearest version → its 2 tests fail. Un-mutated suite: 115 passed again.

## Tests
`tests/test_redaction.py`: every secret shape is hidden, and **telemetry fields are proven not to be corrupted** (`2025-06-01`, `3.x`, `max_tokens=512`, `"total_tokens": 3324`, chunk ids, `request_id`, "Basic authentication"); a 16-digit number with a bad Luhn checksum is left alone. `tests/test_trace.py`: parent/child linkage, redaction at persist time, truncation marker, error recorded then re-raised, **trace survives a simulated process boundary via the carrier**, invalid carriers start a fresh trace without raising, unwritable sink never breaks a request.

## Security and privacy
Redaction is applied to every persisted span. **Not covered by regex:** person names, street addresses, free-form personal facts, secrets with no recognisable shape — documented limitation. Raw prompts and uploaded text are not logged by default (spans carry bounded, redacted attributes only). Secret-shaped test fixtures are assembled at runtime so the repo's secret scan stays clean.

## Defects the tests and real-data runs found in my own code (all fixed, each with a regression test)
1. **Endpoint aliases missed literal ids.** `create_issue` did not match the alias `issue` because `_` is a word character, so migration questions naming the id got no endpoint, fell back to an untuned distance gate, and abstained. Fix: an endpoint's own id and spaced form always count (`test_literal_endpoint_ids_are_detected`).
2. **Redaction blanked its own telemetry.** The redaction-kind label `api-key` equalled a sensitive *key* name, so `redact_obj` replaced the counter `{"api-key": 1}` with `[REDACTED:key-name]`. Fix: label renamed `provider-key` plus an invariant test that no kind label may equal a sensitive key.
3. **Secret-shaped test fixtures tripped the project's own secret scan** (a literal `Bearer …` and a private-key header). Fix: assembled at runtime; rescanned clean.
4. **Resolver comparison markers too narrow:** four real golden questions ("across both v2 and v3", "missing in v2", "introduced in v3 … did not exist in v2", "replace the … heuristic used in 2022-11-28") were sent to clarification. Fix: strong phrases added; a negative control proves a generic word like "new" still does not create a comparison.
5. **My own evaluator** treated list-valued golden targets (`['swagger_v3…','swagger_v2…']`, `[3, 2]`) as scalars and reported four phantom retrieval "misses". Fixed in the throwaway script; corrected numbers are in `phase2.md`.

## Remaining limitations / not done in this phase
- The graph has **no MCP discovery/tool-selection node, no code-validation node, no injection flagging beyond delimiting, no upload scope** yet (Phases 3–6). Specialists are represented only as labelled roles in the routing decision (`documentation_researcher`, `deprecation_migration`, `code_validation`); they are plain nodes/functions, **not autonomous agents**.
- Routing is purely rule-based (`made_by="rule"`); no model-based router exists or is needed yet. Its quality on real questions is unmeasured until Phase 8/9.
- Sufficiency uses an endpoint-coverage check when an endpoint is detected, else a **placeholder, untuned** cosine-distance threshold (`max_dense_distance=0.65`).
- `request_id`/`trace_id` now flow through the new graph, but **not** through `agent_race/agent.py` (CRITICAL-risk symbol, deliberately untouched) and not yet across MCP (Phase 3/7).
- `trace.py` has an unused `os` import (cosmetic).
- ~~Generation has never run against a live model~~ — **superseded 2026-11-03:** it has now; see the "Live-run addendum" at the end of this file. Prompt quality over a *sample* is observed; systematic quality, cost and latency distributions are still unmeasured until the Phase 8 suite runs.

## Acceptance
Accepted for Phase 1's scope: foundations + a bounded, tested pipeline + one entry point, all green offline and exercised against the real index in degraded mode.

## Next
Phase 3 — MCP server tools, allow-listing client, independent stdlib client test, trace propagation across the hop.


---

## Live-run addendum — first calls to a real model (2026-11-03)

A Groq key was added to the git-ignored `developer-rag/.env` under the name **`GROQ_API_KEY`**. My code and the legacy scripts read `GROK_API_KEY` (the project's historical spelling), so the key was invisible at first. I did not edit the user's `.env`: `sdk_engine.config.get_api_key` now accepts both names (the provider's name wins). **Legacy scripts (`config.py`, the Week 6 suite) still require `GROK_API_KEY`**, so those runs need it mapped at run time. The key is never printed, logged, or committed (`.env` is git-ignored and untracked; a scan of everything I created finds no key-shaped string).

**First live smoke call** (`openai/gpt-oss-120b`, tiny prompt): OK, 2.7 s, 79 + 51 tokens, $0.0000424 computed from the price table. The reply was 2 characters but used 51 completion tokens: **this is a reasoning model and hidden reasoning tokens count toward `max_tokens`.**

**First live end-to-end generations** (real index, real MCP server, real model) exposed **three defects that no offline test could have found**; each is fixed and has a regression test:

| # | Live symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Every answerable question abstained ("unverifiable: cites chunks that were not retrieved: `doc:<real id>`") | My system prompt's *example* citation id started with `doc:`; the model copied that style. The verifier correctly rejected it — **no fabricated citation reached the user** — but the product was useless. | The example is now a **real retrieved id**; the prompt forbids prefixes; the retry message lists the valid ids |
| 2 | Next, a correct, grounded answer was discarded as "no citations" | gpt-oss emits citations as full-width `【id】`; the verifier accepted only ASCII `[id]` | Both bracket styles are accepted, **but the id inside must still be an exact retrieved id** (a mangled id inside `【】` is rejected like one inside `[ ]`); the final answer is normalised to ASCII |
| 3 | A migration answer ended mid-citation (`… [`) and was returned | `max_tokens=700` was exhausted by reasoning + answer; the verifier did not look at `finish_reason` | `finish_reason` is captured; `"length"` is a verification failure (retry, else withhold); default budget raised to 1500 |

**Live results after the fixes** (3 queries, 1 sample each — an observation, not an evaluation):
- *"On API version 2022-11-28, how do I set the assignee when creating an issue?"* → **answered**, 1 model call, $0.00046, ≈5.5 s end to end (includes starting the MCP server); citation `[github_combined_reference:p4:spec:top:4]` verified; the body describes only 2022-11-28; "assignee is deprecated in favor of assignees in 2025-06-01" appears separately, labelled as newer than the caller's version.
- *"How do I migrate create_issue from 2022-11-28 to 2025-06-01?"* → **answered**, 1 call, $0.00070, ≈6.4 s; cites both versions' pages.
- *"On Swagger Codegen 2.x, which flag generates markdown documentation?"* → **abstained** (`model_abstained`), 1 call, $0.0003. Honest: the corpus documents `--generate-markdown` only for 3.x.

**An unsupported detail the verifier cannot catch (recorded as a real, observed failure for Phase 6):** the 2022-11-28 answer's `curl` example includes `-H "X-GitHub-Api-Version: 2022-11-28"`. The retrieved 2022-11-28 page's example has **no such header**; that header appears only in the 2025-06-01 page's example. The model added it from prior knowledge, despite the prompt rule "code samples must use only parameters shown in the documents". Today's checks verify citations and version mentions, not that every identifier in a code block is supported by the retrieved passages. This is the case the Phase 6 code-validation node must catch (a candidate for the required RED/GREEN regression with a *real* observed failure).

**Costs seen so far (all live calls this session, including the failing pre-fix runs and a two-call debug capture):** about **$0.0065** in total, summed from the per-run costs the CLI printed (0.00228 + 0.00183 + 0.00147) plus the debug capture's logged token counts priced with the table (≈0.00089) plus the smoke call (0.00004). A successful answered question costs $0.0005–0.0007 (one call); the pre-fix failures cost more (two calls each) for no answer. Rate limits were not hit.

