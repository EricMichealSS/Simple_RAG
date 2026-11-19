# Phase 6 — Code correctness and deprecation checks

Date: 2026-11-03 · **Status: PARTIAL.** For the code that the corpus can actually speak about (curl/REST samples, JSON bodies, the Swagger Codegen CLI, Python request samples) every fenced block is parsed and every identifier is checked, in evidence tiers, against the caller's version, and a wrong-version sample is **not delivered**. Several things the brief lists are **not possible with this corpus** and are reported as "not checked" instead of faked: imports, SDK method signatures, mandatory client-side arguments such as timeouts, and execution. **The blocking path was never triggered by the live model** in 19 live question-runs (11 distinct questions; see "Live evidence"): it is proven by tests, not by observed behaviour.

## The brief's seven requirements, honestly
| # | Requirement | Status |
|---|---|---|
| 1 | Extract the code block and identify its language | **Done** for fenced blocks (``` and ~~~); language from the fence, else sniffed (curl/swagger-codegen/JSON), else `unknown` (reported) |
| 2 | Parse with an appropriate parser | **Python** (`ast`, never executed), **JSON** (`json`), **shell** (`shlex`, quote-aware command splitting; understands `curl` and the `swagger-codegen` CLI). Any other language (Java, YAML, JS…) → "no parser for `<lang>`", no findings |
| 3 | Check imports against the SDK package / symbol index | **Not possible**: the corpus is REST reference pages and a generator CLI; there is no SDK package or import index. Python imports are listed as "not checked" and never described as resolved |
| 4 | Check called methods and signatures against version docs | **Partly.** Not for SDK method calls (no metadata; counted and reported). Done for HTTP routes (against the *retrieved* passages), headers, request-body/query keys, CLI flags, Python kwargs and dict keys (against retrieved passages, then the version's symbol index through MCP `check_symbol`) |
| 5 | Required arguments such as mandatory timeouts | **Partly.** "Required." markers in the REST parameter tables are read and a curl sample that omits one gets a *warning* (not a block). Timeouts: the indexed docs state none, so that check is listed as not performed |
| 6 | Removed / deprecated methods | **Deprecated:** surfaced with the replacement and chunk id. **Absent:** when a later version's docs state a symbol did not exist, the engine says plainly it does not exist in the caller's version, cites that source and names the version that documents it. **"Removed" is never claimed** (the corpus never says it). The corpus has no removed-method or pagination scenario (G-COR-1/2), so the migration path here is "documented from 2025-06-01; move to it or ask for a comparison" |
| 7 | Report the exact result and any unsupported checks | **Done.** `AnswerResult.code_validation` (structured) plus a deterministic footer in the answer: "Code check (static, against the documents; nothing was executed)", per-block parse result, counts by tier, deprecated/unverified/missing items, and a "Not checked:" line |

The footer never says a sample is valid, correct or works: `parsed` means well-formed text, `documented` means the docs mention the identifier.

## What was built
| File | Purpose |
|---|---|
| `sdk_engine/codecheck.py` (new) | pure module: extraction, parsers, evidence tiers, policy (`failures_for`), `render_footer`, `explain_blocked`. The symbol lookup is **injected**, so it is testable with no model, network or MCP server |
| `sdk_engine/schemas.py` (edited, additive) | `CodeFinding`, `CodeBlockReport`, `CodeValidationReport`; `AnswerResult.code_validation` (optional) |
| `sdk_engine/graph.py` (edited) | `code_validation` step inside `verify`; `_symbol_lookup` (MCP first, in-process fallback); footer in `finalize`; plain explanation in `abstain`; `Deps.symbols`; span `code_validation` |
| `sdk_engine/toolbox.py` (edited) | `check_symbol_via_tools`: same four gates as every tool call, version enforced by the graph, a finding for another version/symbol is refused (`scope_mismatch`) |
| `sdk_engine/factory.py` (edited) | `LazySymbolIndex` (the in-process fallback, built on first use) |
| `sdk_engine/symbols.py` (edited) | `appears_in(..., squash=)`, `deprecations_in` (public helpers); **sentence-boundary fix** in the absence matcher (below) |
| `sdk_engine/answering.py` (edited) | prompt rule 5 asks for code in a fenced block that names its language; **same-product scoping** of the stray-version check (below) |
| `tests/test_codecheck.py`, `test_graph_code.py` (new); `test_symbols.py`, `test_answering.py`, `test_integration_real_index.py`, `test_mcp_graph_e2e.py` (extended) | see Tests |

No tracked file was modified (`git diff --name-status` empty).

## How a code sample is judged
Evidence tiers, strongest first (`codecheck.py` docstring has the full text):

| Tier | Meaning | Effect |
|---|---|---|
| `cited` | appears (strict whole-word match) in a passage the model was shown | info |
| `elsewhere_in_version` | documented elsewhere in the same version (symbol index via MCP `check_symbol`, else in-process) | info |
| `deprecated` | this version's own docs say so (replacement + chunk id) | warning, shown |
| `absent_per_docs` | a later version's docs state it did not exist in this version | **HARD** |
| `other_version_only` | not documented for this endpoint/version, documented for another | **HARD** when the endpoint is known and the *same endpoint* documents it elsewhere, else soft |
| `unverified` | looked for, found nowhere | **SOFT** |
| `not_checked` | no index available / lookup failed / per-answer limit (12 distinct identifiers) | warning, shown |

* **HARD** findings keep the draft from being delivered: one correction retry (the model is told which identifier and why), then the answer is replaced by a plain, deterministic explanation. **SOFT** findings get the same one retry but, if it does not fix them, the answer is **delivered with a visible "unverified" warning** — a soft finding can never turn a good answer into an abstention. Only Python syntax errors are hard among parse failures; JSON/shell parse failures are warnings (docs often elide JSON).
* Headers and CLI flags are version-wide, so they are looked up **without** an endpoint scope; parameters (body/query keys, kwargs) are looked up scoped to the endpoint when one is known.
* Requests plumbing (`Accept`, `Authorization`, `Content-Type`, curl flags, `requests` kwargs such as `json`/`headers`/`timeout`) is never looked up and never reported.
* Evidence that comes from the MCP server (another process) is reduced to a safe alphabet before it can reach user-visible text, and a finding that answers for a different version is refused, not believed.
* Cost: no extra model call unless a retry is needed; an answer without a fenced block adds no span, no lookup and no footer.

## Defects found during this phase
1. **A Phase 3 heuristic mis-cited authoritative evidence.** The index's absence matcher looks 40 characters past a symbol for "did not exist", across sentence boundaries. In "…`assignee` is deprecated in favor of `assignees`. The `type` field did not exist in 2022-11-28" it called **`assignees`** absent, citing a sentence that is about `type`. The verdict happened to be right but the citation was wrong, and the new code would have printed "`assignees` does not exist in 2022-11-28 [chunk]". Fixed: a marker only counts inside the symbol's own sentence (both directions). Regression test added; the existing real-corpus facts test (`test_real_index_matches_the_documented_facts`) still passes.
2. **Squashed matching is too lenient for "cited".** The index's whitespace-squashed matching lets `assignees` match inside `assignee string` (`assigneestring`). Harmless for the index's purpose, wrong for deciding that a passage *shows* an identifier. The cited tier now uses strict whole-word matching. (My first guard test for this did not actually fool the matcher; the mutation run exposed it and the test was rewritten.)
3. **Live: my shell parser split blocks by physical line.** A multi-line `-d '{ … }'` body (the most natural way to write curl) produced a "syntax error" and listed the JSON keys as shell *commands* in the footer. Fixed with a quote-aware splitter.
4. **Live: a pre-existing check withheld a correct answer.** `verify_answer` treats any known version label as a claim, and `normalize_version` turns a semver value into a Swagger label, so a GitHub 2025-06-01 release example with `"tag_name": "v2.0.0"` was rejected as "claims about versions outside the allowed set: ['2.x']". Reproduced deterministically; fixed by policing only versions **of the same product**. The case the check exists for — another GitHub date, e.g. `X-GitHub-Api-Version: 2022-11-28` in a 2025-06-01 answer — is still rejected (tested both ways).
5. **A calibration correction to my own earlier note.** I had recorded the extra `X-GitHub-Api-Version` header as an "unsupported" element. The index documents it for 2022-11-28 (one chunk); it simply was not in the passages the model was shown. It is now classified `elsewhere_in_version` (a pass) and is looked up version-wide.
6. **An unexplained one-off test failure.** `tests/test_uploads.py::test_uploads_and_the_official_corpus_never_mix` failed once, in a slow (117 s) full run, and passed in isolation, as a whole file, in the immediate full rerun (509 passed), in three reruns of the seven Chroma-using test files (182 tests each) and in the three later full runs (570, 577, 578 passed). It touches only the process-wide Chroma ephemeral client, not anything changed here. **Cause unknown; not reproduced.** Every later full run was captured with tracebacks.

## Live evidence (real model, real MCP server, real retrieval)
Two runs, not pooled: `evidence/phase6/live_code.py`, `live_code_results.json` (the second run; full answers; no key-shaped strings).

* **Run 1 (8 questions, $0.0040):** found defects 3 and (indirectly) 4. No hard or soft finding.
* **Run 2 (11 questions, after both fixes; $0.0047, mean $0.00043 per question):** 6 answers contained code, 5 were model abstentions ("documents do not contain the answer"). Across the 6 code blocks: **0 hard, 0 soft findings; 0 symbol lookups** (every identifier was in a cited passage); the multi-line curl now parses with its route and all identifiers supported; one JSON block with `//` comments was reported as "not strict JSON (comments or elisions?)" and not blocked. Answered code questions took 1.3–2.2 s, except the first question of the process (5.5 s; cause not investigated). Three **pressure** questions explicitly asked for a field the version lacks (`assignees` and `make_latest` on 2022-11-28, `--generate-markdown` on 2.x): **in all three the model itself abstained** instead of inventing the field.

**What this shows and does not show.** Version-scoped retrieval plus the model's own abstention kept wrong-version code out of all 19 live question-runs (11 distinct questions), so the code check's blocking path and the `check_symbol` lookup path were **not exercised live**. They are exercised by: scripted-lookup unit tests (every tier), graph tests with a real `SymbolIndex`, real-corpus tests with real retrieval (a 2025 field in a 2022 sample; a `make_latest` the next version says did not exist; the docs' own curl validates clean; the version header passes; `--generate-markdown` blocked for 2.x and fine for 3.x), and a real-subprocess e2e test where the discovered `check_symbol` tool blocks a wrong-version field under one trace id. Six live code blocks with zero false positives is **not** a false-positive rate. The questions were written by me.

## Mutation checks
Throwaway pytest plugins in `evidence/phase6/`. Counts are failed tests against the test set at the time.

| Mutation | Result |
|---|---|
| `validation_off` (graph never validates code) | 44 failed |
| `hard_never_blocks` | 10 failed |
| `endpoint_scope_off` (parameters never scoped to the endpoint) | 7 failed |
| `no_sentence_cut` (defect 1 reintroduced) | 3 failed |
| `physical_lines` (defect 3 reintroduced) | 3 failed |
| `soft_always_blocks` | 2 failed |
| `python_syntax_warn_only` | 2 failed |
| `any_product_counts` (defect 4 reintroduced) | 2 failed |
| `cited_tier_lenient` (defect 2) | **1 failed** (thin; the first version of its guard test caught nothing) |
| `tool_answer_trusted` (no version/symbol check on a tool finding) | **1 failed** (thin) |
| `no_sanitising` (server-supplied ids reach the footer) | **1 failed** (thin) |

## Commands run and actual results
* `pytest -q` (no API key in the environment): **578 passed** in 76 s (includes 8 real-index integration tests and 4 real-subprocess e2e tests). Progression this session: 509 → 570 (+61) → 578 (+8).
* Per file (`--collect-only`): `test_codecheck` 45, `test_graph_code` 14, `test_symbols` 24, `test_answering` 23, `test_integration_real_index` 8, `test_mcp_graph_e2e` 4.
* Real-corpus probes (`evidence/phase6/explore_corpus.py`): `X-GitHub-Api-Version` present for 2022-11-28; `assignees` not found for 2022-11-28/`create_issue` and present for 2025-06-01; `make_latest` absent for 2022-11-28/`create_release`; `--generate-markdown` absent for 2.x. The corpus has curl samples (5 chunks per GitHub version) and no fenced code, no imports, no `timeout`.
* GitNexus: `finalize`, `AnswerResult` (and, earlier, `output_safety_failures`) are not in the index (untracked files) → `risk: UNKNOWN`, resolved by text search (35 matches of a broad usage pattern — `AnswerResult(`, `Deps(`, `verify_answer(`, `.model_dump()`, `result.answer|citations|security` — across 13 files; every schema/`Deps` change is an additive optional field). `detect_changes` has **not** been run because nothing is being committed.

## Known weaknesses (open)
1. **The live model never produced a wrong-version sample**, so the blocking path has no live observation; the false-positive rate on honest answers is unmeasured beyond 6 live blocks. The 25–40 case suite (Phase 8) is where this should be measured on a larger, non-authored-by-me set.
2. **Languages:** Python, JSON, shell (`curl`, `swagger-codegen`) only. Everything else is reported unsupported.
3. **No import, SDK-signature, timeout or execution checks** — impossible with this corpus; reported on every answer.
4. **Routes and required parameters are checked against the *retrieved* passages only** (no MCP tool exposes routes). A correct route that retrieval missed is `unverified` → one retry → shown as a warning. Required-parameter misses are warnings only.
5. **Values are not validated** (header values, option values, enum values) except version strings through the stray-version check.
6. **"cited" means "in a retrieved passage"**: a poisoned document can cite a bad identifier. Phase 5's layers limit what a poisoned document can *do*, not whether code built from it is correct.
7. **The index's absence/squash matching is heuristic text matching over PDF-extracted text.** A sentence-boundary bug was found this phase; more may exist, and `absent_per_docs` is evidence-backed only to the extent of that matcher. 12 real-corpus facts are pinned by tests.
8. **Prompt change not A/B tested** (rule 5 asks for a language-named fence).
9. Fail-closed or lossy edges: blocks longer than 150 lines or past the sixth block are not analysed (counted); more than 12 distinct identifiers per answer are not looked up (reported).
10. The unexplained one-off flake in `test_uploads` (defect 6).

## Reproduce
```
cd developer-rag
..\.venv\Scripts\python -m pytest -q                              # no key needed
# live (needs GROQ_API_KEY in developer-rag/.env; 11 paced questions ≈ $0.005): evidence/phase6/live_code.py
```
The evidence scripts contain machine-specific absolute paths. Mutation plugins are used as `MUTATION=<name> PYTHONPATH=<dir> pytest -p mutate7 …`.
