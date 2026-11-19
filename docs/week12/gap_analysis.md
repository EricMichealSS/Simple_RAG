# Week 12 — Gap Analysis

Date: 2026-11-02 · Branch: `week11_implementation` @ `5e63530` · Evidence tags as in `architecture_current.md`.

Severity: **S1** blocks a required capability or is a safety/privacy defect · **S2** required capability partly there · **S3** hygiene/quality.

## A. Environment and prerequisites (blockers)

| ID | Gap | Evidence | Sev | Needs |
|---|---|---|---|---|
| G-ENV-1 | No dependency installed; only Python 3.14.8 present | `importlib.find_spec` for openai, chromadb, sentence_transformers, dotenv, streamlit, pypdf, numpy, mcp, langgraph, pydantic, pytest, docx, fastapi, torch: all missing (RAN) | S1 | Approval to create a git-ignored `.venv`; decision on Python version (Q2) |
| G-ENV-2 | No `.env` / `GROK_API_KEY` | `find` for `.env*`: none; env var unset (checked without printing) | S1 | A Groq key from you for any live run; offline work does not need it |
| G-ENV-3 | No vector index | `data/` absent; `chroma_db/chroma.sqlite3` has 0 collections (RAN) | S1 | Rebuild via ingest (local embeddings, no API cost) once deps install |
| G-ENV-4 | `requirements.txt` incomplete, unpinned | lacks `openai`, `mcp`; no pins/lock; no Python pin | S2 | Fix in Phase 11 |
| G-ENV-5 | `config.py` raises at import without `GROK_API_KEY` | `config.py:11-15` | S2 | Lazy/validated config so deterministic modules import without a key |
| G-ENV-6 | `CHROMA_PATH` is cwd-relative | `config.py:24` vs absolute path in `agent_race/tools.py:31` | S2 | Anchor to repo root |
| G-ENV-7 | GitNexus index stale and FTS degraded | `list_repos` staleness "behind 2"; FTS error 126; empty query result | S3 | Re-index before edits (`CLAUDE.md` requires impact analysis before editing symbols) |

## B. Corpus and version awareness (central requirement)

| ID | Gap | Evidence | Sev | Needs |
|---|---|---|---|---|
| G-COR-1 | **SDK corpus cannot support the brief's scenarios.** v3 SDK docs are 6 files / ≈9.8 KB with 3 keyword hits (`pagina|deprecat|removed|migrat|changelog|since v|v2`) all in `client-reference.md`; v2 side is REST *guides*, not an SDK. No changelog, migration guide, version-tagged code samples, Stack Overflow export. | `documents/v3-sdk/*.md` sizes + grep (RAN); `documents/*.pdf` | S1 | **Decision Q3** — the brief forbids inventing SDK docs/signatures |
| G-COR-2 | Two corpora, two version vocabularies, two metadata schemas | §1 of `architecture_current.md` | S1 | Q3 |
| G-COR-3 | No SDK version resolver | no module; `config.DEFAULT_SDK_VERSION="v2"` is an ingest default, not a caller-version source | S1 | Phase 2 |
| G-COR-4 | Version filter exists but is **never applied** | `retrieve(where=...)` exists (`rag_hybrid.py:203`); `app.py:261` and `eval/generate.py:71` do not pass it. Default queries mix v2, v3 and uploads. | S1 | Phase 2 |
| G-COR-5 | Filtered path loses BM25/RRF | `rag_hybrid.py:245-265` | S2 | Apply the filter to both legs, or filter BM25 candidates by metadata, then compare on the eval suite |
| G-COR-6 | BM25 index has no metadata awareness; unfiltered BM25 can surface any version | `rag_hybrid.py:296-313` | S1 | with G-COR-5 |
| G-COR-7 | Chunk metadata lacks `source_type`, `doc_hash`, `index_version`, `deprecated`, `removed_in`, `replacement`, `code_sample_id`, scope/owner | `ingest_live.py` metadata dict (READ) | S2 | Phase 2 schema |
| G-COR-8 | Unknown/ambiguous version metadata is not handled; missing `sdk_version` becomes `""` | `rag_hybrid.py:190` | S2 | Fail-closed rule: unknown ⇒ excluded from version-specific claims |
| G-COR-9 | **Measured:** existing unfiltered retrieval returns a different-version chunk for 51.0% of returned chunks (20/20 queries affected) | probe #11, `baseline_results.md` §5a | S1 | Phase 2 hard filter; target 0% on single-version intent |

## C. Orchestration, decisions, specialists

| ID | Gap | Evidence | Sev | Needs |
|---|---|---|---|---|
| G-ORC-1 | No LangGraph; no typed state; no single orchestrated entry point | grep: no `langgraph` import in any `.py` | S1 | Q4 (approve new deps) |
| G-ORC-2 | No schema-validated decisions (version, tool, specialist, sufficiency, clarify/abstain) | no `pydantic` import in any `.py` | S1 | Phase 1 |
| G-ORC-3 | The "existing Pydantic router / classical routing models" named in the brief were not found | grep + parent-dir listing | S2 | **Q5** — where are they? |
| G-ORC-4 | Specialist agents: Week 10 evidence says the squad did not help (7/7 vs 7/7, ~1.2× tokens, dead worker ⇒ confident wrong answer) | `squad/verdict.md` (READ) | S2 | Start with specialists as bounded LangGraph nodes/functions that return structured findings; **measure** before calling anything an agent |
| G-ORC-5 | Existing agent is unaware of the SDK corpus | `tools.py` hard-codes `race_docs`, `KNOWN_ENDPOINTS` | S1 | Re-point tools to the scoped retriever |
| G-ORC-6 | No sufficiency check, no clarify/abstain decision beyond the relevance gate | `is_relevant()` only | S2 | Phase 1/2 |

## D. MCP

| ID | Gap | Evidence | Sev | Needs |
|---|---|---|---|---|
| G-MCP-1 | Server exposes `race_docs`, not the SDK corpus; no version-scoped search | `docs_search_server.py:34,98-118` | S1 | Phase 3 |
| G-MCP-2 | Three required capabilities not all present: version-scoped docs search ✗, changelog/deprecation lookup ~ (exists for race_docs only), symbol-existence check ✗ | `tools.py`, server | S1 | Phase 3/6 |
| G-MCP-3 | Client has no allowlist, no client-side arg validation, no per-call timeout, no output cap; any discovered tool from a configured server is callable | `mcp_agent.py:123-150` | S1 | Phase 3/5 |
| G-MCP-4 | No reproducible independent-client test run on record | `verify_docs_search_server.py` exists (NOT READ in full) | S2 | Phase 3 |
| G-MCP-5 | No trace id across the MCP hop | `trace_id` absent from `mcp_agent.py` and server | S1 | Phase 7 (e.g. pass trace id in tool arguments/`_meta`, log on both sides) |
| G-MCP-6 | Tool descriptions enter the prompt unsanitised | `_mcp_tool_to_openai_schema` | S2 | Treat as untrusted; allowlist by name+schema hash |
| G-MCP-7 | `mcp_config.json` (and the existing verifier) hard-code `"command": "python3"`: Unix-specific, and on this machine resolves to the Store alias rather than the venv; server would not get the project's dependencies | `mcp_config.json`; probe #13 | S2 | Resolve the server command from `sys.executable` |
| G-MCP-8 | `search_docs.top_k` is an unbounded integer in the published tool schema (output-size / resource risk) | probe #12 `inputSchema` | S2 | Cap in the new server and in client-side validation |
| G-MCP-9 | `mcp` 2.x removes `mcp.server.fastmcp`; an unpinned install breaks all three servers | `pip` resolved 2.3.0; import failed | S1 (resolved by pin) | Pin `mcp<2` in the manifest (done in the venv; manifest in Phase 11) |

## E. Uploads

| ID | Gap | Evidence | Sev | Needs |
|---|---|---|---|---|
| G-UP-1 | Uploads are written into the **official** collection (`sdk_version="uploaded"`) and are visible to every query | `ingest_live.py:73-141`, `app.py` unfiltered retrieve | S1 | Separate collection or hard `scope`/`owner` metadata filter + per-user collection |
| G-UP-2 | No owner/session/collection; cross-user leakage possible by construction | same | S1 | Phase 4 |
| G-UP-3 | No file size/type hardening beyond extension; `.md` decoded as UTF-8 with no error path; chunk ids built from the user filename | `ingest_live.py:14,49,94` | S1 | Validate, sanitise names, cap sizes |
| G-UP-4 | No content-hash dedup, no delete/list API | id-existence check only | S2 | Phase 4 |
| G-UP-5 | DOCX / TXT unsupported | `.pdf`/`.md` only | S3 | `python-docx` (Q4) |

## F. Security and privacy

| ID | Gap | Evidence | Sev | Needs |
|---|---|---|---|---|
| G-SEC-1 | No prompt-injection defence: retrieved text interpolated raw into the prompt | `app.py:91-132` | S1 | Phase 5 — delimiting + policy + deterministic output checks (not classifier-only) |
| G-SEC-2 | No redaction anywhere; raw questions and answers persisted, and `traces.jsonl` is **tracked in git** | grep (none); `app.py:42-75`; `git ls-files` | S1 | Phase 5/7; decide what to do with already-committed traces (Q6) |
| G-SEC-3 | Tool arguments executed as `TOOL_IMPLS[name](**args)`; only two tools validate enums | `agent.py:201-207` | S2 | Schema validation layer |
| G-SEC-4 | No security tests of any kind | repo | S1 | Phase 5/8 |
| G-SEC-5 | No secrets found in tracked text files (positive finding) | pattern scan (RAN) | — | keep it that way; add to CI |

## G. Code correctness and citations

| ID | Gap | Evidence | Sev | Needs |
|---|---|---|---|---|
| G-COD-1 | Code validation is JS-only (`node --check`) with a regex that matches ```` ```js ```` or bare fences; no Python `ast` check | `assertions.py:35,46-80` | S2 | Phase 6 |
| G-COD-2 | Symbol registry is a flat **v3** list; no per-version symbols, no removed/deprecated/replacement fields, no required-arg metadata | `symbol_registry.json` (COUNTED) | S1 | Phase 6 — only with evidence-backed content (Q3) |
| G-COD-3 | Citations are bare `[chunk_id]`; no SDK version/source name/passage check; no verification that cited ids were retrieved | `app.py:122-124` | S1 | Phase 6 |
| G-COD-4 | `api_version_stated` is a keyword heuristic | `assertions.py:119-145` | S3 | Replace with resolver-backed assertion |

## H. Evaluation, observability, cost

| ID | Gap | Evidence | Sev | Needs |
|---|---|---|---|---|
| G-EVA-1 | Judge agreement 80.8% (v1) / 76.9% (v2) — below 85% | `week6_report.md` (HISTORICAL) | S1 | New **human** hand-labels on integrated outputs; I will not relabel to hit a number (Q7) |
| G-EVA-2 | No integrated suite (25–40 cases) covering version, MCP, uploads, injection, trace, cache | — | S1 | Phase 8 (reuse the 26 Week 6 cases where applicable) |
| G-EVA-3 | Week 5 Mode 5 (distance placeholder) still present | `rag_hybrid.py:320`; `run_eval.py` guard text | S2 | Fix with a RED/GREEN test (candidate for the required regression) |
| G-EVA-4 | Trajectory specs are for `race_docs` questions only | `trajectory_specs.py` | S2 | New ≥10 specs for the integrated agent |
| G-EVA-5 | Only Stack A eval requires live API (Groq) | `eval/generate.py`, `judge.py` | S2 | Offline deterministic tier first; live tier gated on key |
| G-OBS-1 | Three unconnected trace mechanisms; no single trace id | §2.7 | S1 | Phase 7 |
| G-OBS-2 | `pricing.py` has no `groq/compound` entry | `pricing.py` | S2 | Mark cost UNAVAILABLE for unpriced models; never invent |
| G-OBS-3 | No span model (parent/child), no query tool by trace/time/version/tool/cost | — | S2 | Phase 7 (stdlib JSONL + CLI) |
| G-OBS-4 | Brief assumes Week 11 PII redaction + incident drill; redaction not implemented, drill used fakes | `week11/phase1.md:80`, `phase0.md` | S1 | Phase 7 |

## I. Packaging and operations

| ID | Gap | Evidence | Sev |
|---|---|---|---|
| G-OPS-1 | No single documented entry point / CLI; `READMe.md` is 6 bytes | `ls -la` | S1 |
| G-OPS-2 | No CI, no `.env.example`, no pytest suite | `ls .github` fails; no files | S2 |
| G-OPS-3 | `app.py` and `rag_hybrid.py` do heavy work at import; hard to test | READ | S3 |
| G-OPS-4 | Generation model is `groq/compound` (compound system with built-in tools) while agent uses `gpt-oss-120b`; two different models for the same answer role | `config.py:21`, `pricing.py:12` | S3 |

## Gap status ledger (updated after Phase 11, 2026-11-04)

"Closed" = implemented **and** covered by a test that was shown to fail when the behaviour is broken (mutation check) or by a measured before/after. "Partial" = real progress, named remainder. Nothing below is marked closed on the strength of documentation alone.

| Gap | Status | Evidence / remainder |
|---|---|---|
| G-ENV-1, -3, -7 | Closed | venv built on Python 3.14.8; `race_docs` index rebuilt (39 chunks); GitNexus re-indexed |
| G-ENV-2 | Closed (2026-11-03) | a Groq key is in the git-ignored `developer-rag/.env` under the name `GROQ_API_KEY` (the engine accepts that and the legacy `GROK_API_KEY`); first live calls made — see the live-run addendum in `phase1.md`. **Legacy scripts still require the name `GROK_API_KEY`** (their `config.py` raises otherwise), so the Week 6 suite needs it mapped at run time |
| G-ENV-4 | Closed | new pinned `requirements-sdk-engine.txt`, `-embeddings.txt`, `-dev.txt` and the 137-package `.lock.txt`; the legacy unpinned `requirements.txt` is left as it was. The pins installed cleanly on Python 3.14.8 in a fresh venv (`phase11.md`) |
| G-ENV-5, -6 | Mitigated | `sdk_engine.config` has no import-time secret and anchored paths; legacy `config.py` unchanged |
| G-COR-1, -2 | Decided (Q3 = corpus B) | real GitHub/Swagger versions; pagination/removal scenarios from the brief are not in the corpus and are not invented |
| G-COR-3 | Closed | resolver, 46 tests incl. real golden questions |
| G-COR-4, -5, -6, -8, -9 | Closed | filter in both legs; unknown version excluded; wrong-version chunks 51/100 → 0/100 |
| G-COR-7 | **Open** | no `doc_hash`/`index_version`/evidence-backed deprecation metadata on chunks (deprecation facts are derived at query time by `symbols.py`) |
| G-ORC-1, -2 | Closed | LangGraph pipeline with bounded loops; Pydantic contracts |
| G-ORC-3 | Open (needs owner) | no Pydantic router / classical models found (Q5 unanswered) |
| G-ORC-4 | Partial | specialist *roles* exist as labelled nodes/functions; no autonomous agents; no measurement that more would help |
| G-ORC-5 | Partial | new tool layer targets the SDK corpus; legacy `agent_race` tools unchanged |
| G-ORC-6 | Partial | sufficiency + clarify + abstain exist and are tested; sufficiency threshold for endpoint-less questions is an untuned placeholder |
| G-MCP-1, -2 | Closed for the new server | search, changelog and symbol tools exist and the graph now calls all three (`check_symbol` from the code check, version enforced, in-process fallback); verified across the real process boundary (`test_mcp_graph_e2e`). Legacy Week 9/10 server untouched |
| G-MCP-3, -4, -5, -6, -7, -8 | Closed (new client/server) | allowlist, schema validation, timeout, cap, independent stdlib client test, one trace id across the hop, untrusted descriptions, `sys.executable`, `top_k ≤ 10`. Legacy Week 9/10 MCP code is untouched and still has these weaknesses |
| G-MCP-9 | Closed | `mcp==1.30.0` pinned in `requirements-sdk-engine.txt` (2.x removed `mcp.server.fastmcp`) |
| G-UP-1, -2 | Closed | uploads in a separate collection per owner **and** a physically separate store from the official index; owner-key filter + post-check; mutation-checked; cross-owner tests |
| G-UP-3 | Closed | size/type/content/macro/zip-bomb/traversal/encrypted-PDF/page/text/time limits; filename sanitised to a display-only basename; nothing written to disk or executed. *Parsing is in-process (documented)* |
| G-UP-4 | Closed | content-hash dedup (per owner), list, delete, delete_all; **physical** deletion verified on disk (0/40 cycles leave text) after a measured redesign away from Chroma |
| G-UP-5 | Closed | PDF, DOCX, Markdown, text supported (python-docx, pypdf) |
| (new) G-UP-6 | **Open** | **no authentication**: the owner id is caller-supplied; isolation is between scopes, not identities |
| (new) G-UP-7 | **Open** | no OCR; in-process parsers can only be abandoned on timeout; CLI cold start ≈12–13 s per call |
| G-SEC-1 | Partial (Phase 5 built; residual named) | policy/data separation, detector (telemetry only; 0 false positives in 72 real units), deterministic output checks (canary, policy text, secrets, allow-listed hosts, auto-fetch, scheme/network-path), degraded-mode checks; each layer mutation-checked. **Open:** answer *wording* can be steered (1 of 28 live trials passed every layer — `phase5.md`); detector recall unmeasured |
| G-SEC-2 | Partial | redaction implemented and tested; `traces.jsonl` still tracked (Q6: left alone) |
| G-SEC-3 | Closed for the new tool path | schema-validated arguments, enforced version |
| G-SEC-4 | Partial | adversarial suite exists (107 tests in `test_injection`/`test_output_safety`/`test_security_adversarial`/`test_degraded_safety`, against a worst-case obedient model) plus three small live runs against the real model. **Open:** live runs not repeated after the URL hardening; samples tiny and authored by me |
| G-COD-1, -2, -3 | Partial (Phase 6 built; limits named) | fenced blocks parsed (Python `ast`, JSON, shell/curl/swagger-codegen); identifiers checked in evidence tiers per version with HARD/SOFT policy; per-version symbol evidence with deprecation/absence/replacement; structured report + footer listing what was not checked; wrong-version samples are not delivered. **Not possible with this corpus:** imports, SDK signatures, timeouts, execution. **Open:** live model never triggered the blocking path (`phase6.md`); routes/required params checked only against retrieved passages; "claim supported by the cited passage" for *prose* is still not checked |
| G-COD-4 | Open for the legacy suite | the new engine polices version claims with the resolver-backed `verify_answer` (same-product scoping fixed in Phase 6); the legacy `assertions.py:api_version_stated` keyword heuristic is unchanged and is replaced only when the Week 6 suite is re-run (Phase 8) |
| G-EVA-1 | **Open — blocked on the owner's labels (Q7)** | the judge check is prepared: 23 blind items (10 real live answers + 13 answers I deliberately corrupted, opaque ids) + the command; judge verdicts exist; **agreement is NOT MEASURED (0 human labels)**. Informational only: judge 13/13 FAIL on my corrupted answers, 10/10 PASS on the real ones (not human labels; my negatives are blatant). Historical Week 6: 80.8% / 76.9% |
| G-EVA-2 | Closed | 40 integrated cases (29 offline, 11 live, 5 regression), all 16 brief areas, deterministic assertions only; final run 40/40 (first full run 37/39 — fixtures and one real defect fixed along the way); suite sensitivity 9/9 injected engine defects detected (2 rest on a single case) |
| G-EVA-3 | Open for legacy Stack A (untouched); not present in the new engine | the new engine never fabricates a distance (`None` when only BM25 found a chunk); `rag_hybrid.py:320` is unchanged. The required real-failure regression is satisfied by `reg01`–`reg05` (RED with the defect re-created, GREEN now) |
| G-EVA-4 | Closed (Phase 9, with named limits) | 10 trajectory specs written before the run (file last modified 3 min before it), scored from spans with the version the *server* saw: path ok 8/10, tool-choice 21/23, outcome 10/10, failed calls 0; the 2 "not ok" paths are an unnecessary but cheap `lookup_changes` (spec/detector disagreement, not resolved by editing the spec). **Measures the rules, not LLM tool judgement** (the graph chooses tools) — `trajectory_eval.md`. Week 5 taxonomy re-scored on all 45 golden questions (7 modes, 1 integration-created) — `error_taxonomy.md` |
| G-EVA-5 | Closed | offline tier (29 cases, no model API) runs first; live tier (11) is gated on the key |
| (new) G-COR-10 | Closed | a date with a non-ASCII hyphen (what a user pastes and what the model writes: U+2011) was `unsupported` as a question and invisible to the wrong-version-claim check in an answer; found by the Phase 8 live run; `normalize_dashes` + 25 tests + `reg05` |
| (new) G-COR-11 | Closed | found by the Phase 9 golden run: 17 of 35 delivered answers carried hyphen look-alikes (U+2011…) between alphanumerics, 2 inside code spans (`okhttp‑gson`): the verifier read them, delivery passed them through. `answering.ascii_hyphens` at the single point the draft is stored; 2+1 tests; `reg05` strengthened (RED 3/3 checks with the defect re-created, GREEN now); 0 of 4 live re-runs affected. The other 13 affected answers were not re-run |
| (new) G-COR-12 | **Open — HIGH, needs owner approval** | a confident wrong answer with a valid-looking citation (`easy_11`, trace `d9566c77…`): the cited chunk contains "true" only inside an example. The verifier checks that a citation exists and is in scope, **not that it supports the claim** (this also restates the G-COD "prose claim support" remainder with live evidence). Fix options: a deterministic value-in-cited-chunk check for value questions; both need a regression case |
| (new) G-COR-13 | **Open — needs owner approval (rebuilds the index)** | retrieval misses on 2/36 answerable questions (`easy_11`, `hard_03`): PDF extraction splits option names across lines (`allowUnicodeIdentifie│rs`), so the token a question names is not intact in the answer-bearing chunk. Candidate fix: re-join split identifiers at ingest, then rebuild |
| (new) G-ORC-7 | **Open** | the distance sufficiency gate does not discriminate: answerable top-1 0.124–0.250, unanswerable 0.161–0.334 (overlap) vs threshold 0.65; all 10 abstentions in 45 were made by the model, 0 by the gate. G-ORC-6's "untuned placeholder" is now *measured as inert on this corpus* |
| (new) G-OBS-7 | **Open** | a draft the verifier rejects is not kept (1 of 45 needed a retry, `medium_10`; whether that was an over-block cannot be audited); per-chunk distances are not written to spans |
| (new) G-COR-14 | Closed | found by review: a retry doubles `top_k` up to 20 but the MCP `search_docs` schema stops at 10, so such requests were rejected as `invalid_arguments` and silently served by the in-process fallback; the call is now clamped (`toolbox.SEARCH_TOP_K_LIMIT`, kept equal to the server's maximum by a test) |
| (new) G-OPS-5 | **Open** | `ChromaRetriever.search` fetches the whole filtered pool, documents included, on every query even with hybrid off: 3 ms at 39 chunks, 26 ms at 1,000, 327 ms at 5,000, **1,208 ms at 20,000** (synthetic; 98% is the fetch). Harmless today, a wall for a real corpus; not changed because it is on the version-filter path (`tenx.md` §4) |
| (new) G-OPS-6 | **Open — capacity** | the provider's token quota is the first bottleneck and was hit during evaluation: free tier 200,000 tokens/day = ≈109 answers/day; 26 of 45 requests in the latency run degraded (cleanly). No queue, no client-side limiter, no backpressure (`tenx.md`) |
| (new) G-OPS-7 | **Open** | one MCP subprocess over one session (≈7 req/s with an instant model, falling to 3.1 at 8 concurrent); **no automatic restart** of a dead server found in the client; ≈632 MB per server |
| (new) G-OPS-8 | Closed | found by review: printing an answer with U+202F/curly quotes to a redirected Windows stdout raised `UnicodeEncodeError`; the CLI now replaces what the code page cannot encode; test reproduces the crash first |
| (new) G-OPS-9 | Closed | found by review: `index rebuild` failed for anyone without the legacy `GROK_API_KEY` set (the legacy chunker raises at import) and silently mismatched the index if `SDK_ENGINE_EMBEDDING_MODEL` was changed; the child now gets a placeholder key, and a non-default embedding model is refused |
| G-OBS-1 | Closed for the new stack | one trace id request→graph→MCP client→server (e2e test); legacy `agent_race` loops still have their own logs |
| G-OBS-2 | Closed in `sdk_engine` | unpriced model → `cost_status="unavailable"`, never 0 |
| G-OBS-3 | Closed (new stack) | `python -m sdk_engine trace find/show/stats`: by trace id, time range, version, endpoint, tool, status, code finding, security, cost, latency; roots-only scan (1.5 s at 30,000 synthetic requests) |
| G-OBS-4 | Partial | redaction on persisted spans verified on a live run (every persisted file scanned: 0 secrets/PII/question text); incident drill repeated on the new stack **twice, blind, on a synthetic log**: it narrowed 422 requests to the 4 that contained the planted one but did not single it out (by design: no answer text is stored). Not a real incident; n = 2 |
| (new) G-OBS-5 | **Open** | no log rotation/retention; queries are a linear scan (fine to ~10⁴ requests); concurrent multi-process appends to one log untested |
| (new) G-OBS-6 | **Open — owner decision** | an answer whose code could not be checked is still `outcome=answered` (footer + trace flag only); pasted secrets/PII in a question still reach the model provider (redacted in logs only) |
| G-OPS-1 | Closed | one CLI, `python -m sdk_engine {query, versions, health [--deep], index check/rebuild, mcp-serve, upload, uploads, trace, eval …}`; `sdk_engine/README.md`; a fresh-eyes review of the README against the code found 18 confirmed mismatches, all addressed (`fresh_eyes_review.md`) |
| G-OPS-2 | Partial | tests, `.env.example`, pinned requirements and a CI workflow now exist; the offline suite passes in a clean checkout and fresh venv on Windows (735 + 11 integration tests); **the workflow has never run on GitHub** and its Linux leg is untested; no secret scan beyond the shape-based tripwire |
| G-OPS-3, -4 | Open / by design | |

## Items verified as already adequate (do not rebuild)
Structure-aware chunker (`chunkers.py`) · hybrid retrieval primitives (BM25, RRF, cross-encoder) · hand-built bounded ReAct loop with four budgets · MCP runtime discovery in `MCPToolRouter` · Week 6 case set, deterministic assertions pattern, judge harness · `pricing.cost_usd` · `squad/metering.Tracer` pattern (candidate to generalise into the single tracer) · Week 11 regression-test pattern.
