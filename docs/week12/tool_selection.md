# Week 12 — Tool Selection (proposed; nothing below is installed yet)

Date: 2026-11-02. Principle carried over from Week 11 and the brief: **extend what exists; add a dependency only when a measured need or a hard requirement justifies it.** No paid API is introduced. Every "new" row needs your approval (decision Q4) before I install it.

Legend — *Cost*: free/open-source vs paid · *Creds*: credentials needed · *Status*: Keep (already used) / New (proposed) / Rejected.

## 1. Keep (already in the project)

| Concern | Choice | Why keep | Cost / Creds | Status |
|---|---|---|---|---|
| Vector store | ChromaDB `PersistentClient`, cosine | Works, local, supports metadata `where` filters and multiple collections (needed for upload isolation). No reason to swap. | free / none | Keep |
| Embeddings | local `BAAI/bge-small-en-v1.5` (sentence-transformers) | Already the live query encoder; zero API cost; indexing cost is local compute only. The legacy Gemini path (`ingest.py`) is dimension-incompatible and stays unused. | free / none | Keep |
| Sparse retrieval | in-repo `BM25` class + RRF (`rag_hybrid.py`) | Works; must be made metadata-aware (gap G-COR-5/6), not replaced. Decide fusion strategy on the Week 6 suite. | free | Keep, modify |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Already in place; CPU. Keep behind a flag so its contribution can be measured. | free | Keep |
| Chunker | `chunkers.py` structure-aware | Already benchmarked in Weeks 3–4 (docs not re-run here). The brief says not to replace without reason. | free | Keep |
| LLM provider | Groq via the `openai` SDK (`base_url=https://api.groq.com/openai/v1`) | Existing credential path and free tier; wrap behind a tiny `LLMClient` protocol so it stays replaceable. Models in use: `openai/gpt-oss-120b` (agent), `groq/compound` (app/eval answers), `openai/gpt-oss-20b` (judge). | free tier / `GROK_API_KEY` | Keep |
| MCP | official `mcp` Python SDK (`FastMCP` server, `stdio_client` client) | Already used in Week 9; **pin the version**. | free | Keep |
| Cost accounting | `agent_race/pricing.py` | Extend with an explicit "unpriced" state instead of `KeyError`; never invent a rate. | free | Keep, modify |
| Tracer pattern | `squad/metering.Tracer` | Generalise into one tracer rather than adopting an external platform. | free | Keep, generalise |
| Eval assets | `eval/cases.json` (26), `labels_25.json` (26), `assertions.py`, `judge.py`, `symbol_registry.json` | Mandatory per brief; extend, never shrink. | free | Keep |
| Agent loop | hand-built bounded ReAct (`agent.py`) | Four budgets already enforced; reuse as an inner step where tool-use is genuinely open-ended. | free | Keep |

## 2. New (need approval)

| Candidate | Purpose | Justification | Alternative considered | Cost / Creds | Risk | Status |
|---|---|---|---|---|---|---|
| **`langgraph`** | Typed `StateGraph` for the outer pipeline: explicit nodes/edges, conditional routing, recursion limit, one place to carry request/trace id | The brief asks for it "where it provides a clear benefit". Benefit here: a bounded, inspectable request pipeline with typed state and testable nodes. Honest caveat: the project has deliberately avoided frameworks for 11 weeks and a hand-written state machine would also work. **Design rule to limit lock-in:** every node is a plain function `state -> partial state` with no LangGraph import; the graph file is thin wiring only. No checkpointer persisting raw user text. | Hand-rolled loop; LangChain (rejected: much heavier, no need) | free / none | New transitive deps (`langchain-core` etc.); **Python 3.14 compatibility unverified**; API churn | New |
| **`pydantic` v2** | Schemas for state, decisions (version, tool, specialist, sufficiency, clarify/abstain), tool args, MCP results | Needed for "structured, schema-validated decisions". Already an indirect dependency of `mcp`; promote to explicit. | dataclasses + manual validation (weaker, more code) | free | none material | New (explicit) |
| **`pytest`** | Unit/integration/security/regression tests | The repo has no suite; scripts exit-code style is not CI-friendly. Dev-only. | stdlib `unittest` | free | none | New (dev) |
| **`python-docx`** | DOCX text extraction for uploads | Brief asks for DOCX support; `pypdf` already covers PDF; `.md`/`.txt` need only the stdlib. Parse text only — **never** execute macros/embedded objects. | none lighter | free | parser attack surface ⇒ size cap, no external-entity/macro handling, extraction only | New (optional) |
| `openai` (declare) | LLM SDK | Imported today but missing from `requirements.txt` | — | free | none | Fix manifest |
| `mcp` (declare + pin) | MCP | Imported today but missing from `requirements.txt` | — | free | none | Fix manifest |

## 3. Built in-repo with the stdlib (no new dependency)

| Capability | Approach | Why not a package |
|---|---|---|
| SDK version resolver | explicit arg → config → parse from question (`v2.4`, `2.4.1`, ranges) → else **ask/abstain**; never silent guess | Small, deterministic, must be auditable |
| Version-scoped filter builder | one function producing the Chroma `where` for dense **and** a metadata predicate for BM25; fail-closed on unknown version | Core safety property; must be tested exhaustively |
| Python code validation | stdlib `ast` (syntax, imports, call names) + versioned symbol metadata; JS via existing `node --check` pattern | No execution of untrusted code in-process. Honest limit: parse + symbol check ≠ runtime correctness |
| Redaction | tested regex rules (API keys, bearer tokens, `Authorization`, passwords, connection strings, emails, phone/ID-like numbers) | Presidio/spaCy is a large model download; names/addresses are **not** detected by regex — documented limitation |
| Prompt-injection controls | untrusted-content delimiting, instruction-pattern flagging into telemetry, deterministic output/tool checks, tool allowlist | Classifier-only defence is explicitly disallowed by the brief |
| Tracing | `contextvars` trace/span ids + JSONL sink + `trace_id` carried across the MCP hop (tool-call metadata/argument) + CLI to query by trace/time/version/tool/status/cost | Week 11 reasoning stands: JSONL suffices; OTel/Phoenix/Langfuse add a service for no required gain |
| Independent MCP client test | a ~60-line **stdlib JSON-RPC-over-stdio client** in a separate process (does not use the `mcp` SDK) that runs `initialize → tools/list → tools/call` | Satisfies "independent client implementation" without another dependency; I will **not** call it external fresh-eyes verification |
| CLI | `argparse` subcommands: `query`, `index`, `upload`/`uploads list|delete`, `eval`, `trajectory`, `history`, `mcp-serve` | One documented entry point; no web service needed |
| Offline test doubles | in-memory retriever/LLM fakes (pattern from `week11/fakes.py`) | Lets the deterministic core be tested with no index/key |

## 4. Explicitly rejected (with reason)

| Candidate | Reason |
|---|---|
| FastAPI / any HTTP API | No API exists today; the brief says start one only "if needed". CLI + existing Streamlit suffice. Revisit only if you want an API. |
| LangChain, LlamaIndex, CrewAI, AutoGen | Not needed; would replace working components. |
| OpenTelemetry, Arize Phoenix, Langfuse | Same reasoning as Week 11; adds a service/SDK for no required capability. |
| New vector DB (Qdrant, pgvector), Redis, message broker, Kubernetes, cloud platform | No measured need. 10× analysis will *project* when these become necessary, labelled as projection. |
| Presidio / spaCy NER | Large model download; regex redaction is conservative and testable. Limitation documented. |
| Any paid API by default | Brief rule 7. Judge and generation stay on the Groq free tier. |
| Executing validated code in a sandbox | Windows lacks a trustworthy lightweight sandbox; Week 12 will do **static** validation only and say so. |
| Fine-tuning / training an LLM | Out of scope per brief. |
| Semantic answer cache | Not present today; building one is only justified if eval/cost data shows repeated near-identical queries. If built, the key must include normalised question, resolved SDK version, official `index_version`, upload-scope id **and** upload-collection revision, prompt version and model id; never shared across users. **Deferred, to be decided from measurements.** |

## 5. Dependency footprint summary (if all "New" rows are approved)

Direct additions: `langgraph`, `pydantic`, `pytest`, `python-docx` (+ declaring `openai`, `mcp`). Largest install by far is **already** `sentence-transformers`→PyTorch (existing design), not these. All versions will be pinned in a lock/constraints file and recorded in `phase11.md`.
