# Week 12 — Baseline Results (Phase 0)

Date: 2026-11-02 · Machine: Windows 11, Python 3.14.8 · Branch `week11_implementation` @ `5e63530`.

**Headline: no live baseline could be produced.** Nothing the project depends on is installed, there is no `.env`/API key, and no vector index exists. No paid API was called. Every number below is either (a) something I ran in this session, or (b) explicitly labelled HISTORICAL and copied from a committed report.

## 1. What I ran

| # | Check | Command / method | Result |
|---|---|---|---|
| 1 | Git state | `git status --short --branch`, `git branch -a -vv`, `git log --all --graph`, `git stash list`, `git remote -v` | On `week11_implementation`, tracking `origin/week11_implementation`, in sync. Untracked: `.claude/skills/`, `AGENTS.md`, `CLAUDE.md`. 2 stashes (untouched). |
| 2 | Branch ancestry | `git rev-list --count main..HEAD`; `git branch -a --no-merged HEAD` | `main` is 12 commits behind, 0 ahead; no merge commits; **no** branch is un-merged into HEAD. |
| 3 | Syntax of all tracked Python | `ast.parse` over `git ls-files '*.py'` | **57 / 57 parse, 0 failures.** (An earlier attempt of mine reported failures because my script built the file paths wrongly; that output was a script bug and is discarded. The corrected run is the one above.) |
| 4 | Dependency availability | `importlib.util.find_spec` | **Missing:** openai, chromadb, sentence_transformers, python-dotenv, streamlit, pypdf, numpy, mcp, langgraph, langchain, pydantic, pytest, python-docx, fastapi, anyio, torch, google-genai, ragas. |
| 5 | Credentials | `.env*` search; env var presence (value never printed) | No `.env`; `GROK_API_KEY` unset. |
| 6 | Vector index | `ls data/`; read-only `sqlite3` open of tracked `chroma_db/chroma.sqlite3` | `data/` does not exist. Tracked `chroma_db/` holds **0 collections, 0 embeddings**. |
| 7 | Secret scan | `git grep -I -l` for key-shaped patterns (Groq/Google/OpenAI/xAI prefixes, bearer tokens, private-key headers) across tracked text files (PDF/sqlite/bundle excluded) | **No matches.** No literal `GROK_API_KEY = "..."` assignments. (Binary files were not scanned.) |
| 8 | Eval data integrity | JSON parse | `eval/cases.json`: **26 cases** — baseline 15, version_confusion_w6 3, mode1_groq_error 2, mode2_relevance_gate_false_refusal 2, mode3_answerable_wrong_chunks 2, mode4_cross_document 2. `eval/labels_25.json`: 26 entries. `symbol_registry.json`: 32 SDK symbols, 18 params, 6 endpoints, 39 named operations, 10 headers, 3 status codes. |
| 9 | Offline regression test | `python -B agent_race/week11/test_regression_w11.py` (output to scratchpad) | **GREEN, 1/1, exit 0.** Runs the real `agent.py`/`tools.py` with the OpenAI client, Chroma and embedding model **faked** (`week11/fakes.py`). Validates control flow and default flags (`prompt_version=agent-v2-mitigated-default`, `mitigated=True`) only — **not** model behaviour. `git status` unchanged afterwards. |
| 10 | Corpus size | file sizes / keyword grep | v3 SDK docs: 6 `.md` files, 9,821 bytes total; keyword hits for `pagina|deprecat|removed|migrat|changelog|since v|v2`: 3, all in `client-reference.md`. v2 side: 5 REST-guide PDFs (≈2.0 MB). Stack B: 3 PDFs. |

## 2. What I could not run, and why

| Item | Why not run | Unblocks with |
|---|---|---|
| `eval/run_eval.py` (Week 6, 26 cases) | needs `openai`, `chromadb`, `sentence-transformers`, `GROK_API_KEY`, built index; live Groq | deps + index + key |
| `eval/judge.py` re-validation | live Groq (`openai/gpt-oss-20b`) | key |
| `test_rag.py`, `test_retrieval.py` | live index | deps + index |
| `agent_race/trajectory_eval.py` | live Groq + `race_docs` index | deps + index + key |
| MCP server/client round-trip | needs `mcp`, `chromadb`, `sentence-transformers`, and the `race_docs` index | deps + index |
| Streamlit app | needs everything above | — |

I made **no claim** that any of these pass or fail today.

## 3. Historical numbers carried forward (NOT re-run; from committed reports)

| Metric | Value | Source |
|---|---|---|
| Week 6 judge agreement, judge_v1 vs human labels | 21/26 = **80.8%** | `eval/week6_report.md` |
| Week 6 judge agreement, judge_v2 | 20/26 = **76.9%** (net regression; v1 stays in production) | same |
| Week 5 taxonomy | 5 named modes, 20 of 23 traces (seed 42): Mode 1 Groq 413/TPM 4/20 (20%, HIGH); Mode 2 relevance-gate false refusal 1/20 (5%, HIGH); Mode 3 answerable-but-wrong-chunks 2/20 (10%, MEDIUM); Mode 4 cross-document 1/20 (5%, MEDIUM); Mode 5 distance placeholder 20/20 (100%, LOW) | `developer-rag/taxonomy.md` |
| Week 8 trajectory (10 cases, `race_docs`) | baseline: tool_choice_accuracy 1.0, outcome pass rate 0.8; `mitigated` alone: outcome 0.6; optimized (mitigated + parallel_hint + compact_history): 0.8 | `agent_race/week8_report.md`, comments in `agent.py` |
| Week 10 squad vs single | accuracy 7/7 vs 7/7; tokens 25,129 vs 21,118 (1.2×); cost/q $0.00040 vs $0.00030; failure test: squad "lied" | `agent_race/squad/verdict.md` |
| Week 11 incident drill | 00:47 (mm:ss) to locate, per its own doc | `agent_race/week11/drill.md` |

These are single-run, small-sample, one-machine (Mac) historical results. They are inputs to planning, not Week 12 results.

## 4. Baseline defects confirmed from source (no execution needed)

1. Version filter never applied by any caller (`app.py:261`, `eval/generate.py:71`).
2. Uploads share the official collection (`ingest_live.py`).
3. Week 5 Mode 5 distance-overwrite still in code (`rag_hybrid.py:319-320`).
4. No redaction code; `traces.jsonl` (raw Q&A) is tracked in git.
5. `requirements.txt` omits `openai` and `mcp`.
6. `config.py` raises at import without an API key; `CHROMA_PATH` is cwd-relative.
7. `pricing.py` has no entry for `groq/compound`.

## 5a. Post-environment baseline (added 2026-11-02, after the venv was built)

Environment now: `.venv` (git-ignored) on Python 3.14.8 with `chromadb 1.5.9`, `sentence-transformers 6.2.0`, `torch 2.15.0`, `langgraph 1.2.19`, `pydantic 2.14.2`, `openai 3.42.1`, `mcp 1.30.0` (**pinned `<2`**: `mcp 2.3.0` removed `mcp.server.fastmcp`, which the three existing servers import), `pypdf 6.21.4`, `python-docx 1.2.0`, `pytest 9.2.0`. All imports the existing code needs resolve. `race_docs` index built with the unmodified `agent_race/ingest.py` (placeholder `GROK_API_KEY` set for that one process only, because `chunkers`→`config` raises without a key): **39 chunks** (github 18, swagger v2 8, swagger v3 13). At the time of this probe a Groq key was still absent (it arrived 2026-11-03; live results are in `phase1.md`'s addendum).

| # | Probe (throwaway scripts, not in the repo) | Result |
|---|---|---|
| 11 | **Wrong-version leakage of the existing unfiltered `tools.search_docs`.** 20 queries, one per distinct `(api_version, endpoint)` pair in `page_metadata.json`, templated mechanically (`"{endpoint} API version {version} parameters"`), top-5 each. | 100 chunks returned. **51/100 (51.0%) belong to a different `api_version` than the caller's** (44 same-product other-version, 7 other-product). **20/20 queries** had ≥1 wrong-version chunk in the top 5. *Caveats:* retrieval layer only (the production agent mitigates by calling `get_openapi_spec` with exact enums); single-version intent only (cross-version chunks are legitimately needed for migration questions); templated queries, small 39-chunk corpus. |
| 12 | **MCP stdio round trip** against the unmodified `docs_search_server.py`, launched with the venv interpreter | Initialize + spawn 8.6 s (model load). `tools/list` discovered `search_docs`, `get_openapi_spec`, `check_deprecation` at runtime. Calls 4–77 ms. `check_deprecation(2025-06-01, create_issue)` returns the "Previous version (2022-11-28)" paragraph; the same call for the *older* version returns `found:false` with a direction hint. Bad `api_version="v4"` is rejected by FastMCP/pydantic with `isError=True`. `search_docs` schema: `top_k` is an **unbounded integer**; no trace/correlation field anywhere in any tool schema. |
| 13 | `python3` resolution | `python3` on PATH is `…\WindowsApps\python3.EXE` (the Store alias), not the venv. `mcp_config.json` hard-codes `"command": "python3"`. Launching it was **not tested**. |

These are the **before** numbers for Phases 2 and 3: the Week 12 scoped retriever must reduce #11's wrong-version rate to 0 on single-version queries (checked by a deterministic assertion, not a judge).

## 5. Environment prerequisites to establish a real baseline

1. A virtual environment (git-ignored) on a Python version the ML stack supports — **Python 3.14 support for `chromadb`/`torch` is unverified** (decision Q2).
2. `pip install` of the existing requirements plus `openai` and `mcp`. `sentence-transformers` pulls in PyTorch (large, multi-GB class install; used for the local bge embedder and cross-encoder — already the project's design).
3. A Groq API key in a local, git-ignored `.env` (only for live generation/judge/agent runs; free-tier rate limits were already the Week 11 bottleneck).
4. Run `ingest_combined.py` (Stack A) and `agent_race/ingest.py` (Stack B) to rebuild `data/chroma` — local embeddings, no API cost.
5. Then run, in order: `eval/run_eval.py` → judge re-check → `trajectory_eval.py`, and record RED/GREEN honestly.

Until then, all Week 12 work can proceed only on **deterministic, dependency-free** components (version resolver, filter construction, symbol/deprecation checks, redaction, security rules, tracing, schema validation) tested against in-memory fakes of the retriever.
