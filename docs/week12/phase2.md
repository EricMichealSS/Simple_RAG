# Phase 2 — Version resolution and hard version filtering

Date: 2026-11-02 · **Status: PARTIAL.** Resolver and filter-enforcing retriever are done, wired into the graph, tested and measured on the real index. Chunk-metadata enrichment (content hash, index version, evidence-backed deprecation fields) is NOT done.

## Objective
Resolve the caller's version before retrieval and enforce it in the vector-store query — never in the prompt — with fail-closed behaviour on unknown or ambiguous metadata.

## Corpus facts this phase is built on (decision Q3 = corpus B, `race_docs`)
39 chunks from 24 hand-labelled pages: GitHub REST `2022-11-28` / `2025-06-01` (13 pages) and Swagger Codegen `2.x` (5) / `3.x` (6); 10 endpoints/generators; 20 distinct `(api_version, endpoint)` pairs. Metadata per chunk: `source_file, page, anchor, content_type (spec|diff), api_version, endpoint, has_diff`. **The only "what changed" text lives on the NEWER version's page** (e.g. the `assignee`→`assignees` deprecation is on `2025-06-01`; the `2022-11-28` page is silent). The corpus says "deprecated", never "removed".

## Files created
| File | Purpose |
|---|---|
| `sdk_engine/versioning.py` | `VersionCatalog` (loaded from `page_metadata.json`, not hard-coded), `resolve_version`, endpoint/product detection, comparison-intent handling |
| `sdk_engine/retrieval.py` | `RetrievalScope` (validated, frozen), `build_where`, `ChromaRetriever` (dense + BM25-over-filtered-pool + RRF, optional reranker), `enforce_scope` (fail closed), `open_collection` (errors if the index is missing), `upload_collection_name` |
| `tests/test_versioning.py`, `tests/test_retrieval_scope.py`, `tests/test_integration_real_index.py` | 35 + 36 unit tests, 3 integration tests (counts from `pytest --collect-only`) |
Edit to a Phase 1 file: `schemas.Passage` gained `endpoint`. No legacy file touched.

## Design decisions
- **Resolver precedence:** explicit > question > operator default > ask. Operator default only applies to the product the question is about and carries confidence 0.6.
- **Never substitute:** `2024-01-01`, `v4`, `4.x` → `UNSUPPORTED` + clarification; an explicit-but-unsupported version does not fall back to the question or config.
- **Conflict ⇒ ask:** explicit `2022-11-28` vs question naming `2025-06-01` (no comparison words) → `AMBIGUOUS`. Two products named → `AMBIGUOUS`.
- **Cross-version only by explicit comparison intent** (`migrate/upgrade/compare/between/from … to …`): resolves to the *from* version and records `comparison_versions`; retrieval then needs a `comparison` scope, and non-first-version passages are labelled `cross_version=True`.
- **Filter in both retrieval legs.** The legacy filtered path dropped BM25/RRF; the legacy unfiltered BM25 spanned every version. Here BM25 is built only over the already-filtered pool.
- **No fake distances:** sparse-only hits have `dense_distance=None` (legacy Week 5 Mode 5 wrote a placeholder `1.0`).
- **Defence in depth:** after retrieval every passage is re-checked; any out-of-scope chunk raises `ScopeViolation`. Missing or `"unknown"` `api_version` cannot satisfy the filter.
- **Fail loudly on a missing index** (`get_collection`), unlike the legacy `get_or_create_collection`.

## Commands run and actual results
- `pytest -q` (no key, whole suite incl. graph and answering tests): **171 passed** = 168 unit + 3 integration against the real index (integration alone ≈28 s incl. model load). Phase 2's own files: versioning 46, retrieval_scope 37, integration 3.
- **Measurement on the same 20 templated queries used for the baseline** (`baseline_results.md` §5a, probe #11):

| Retriever | Wrong-version chunks | Expected endpoint in top-5 | …as top-1 | p50 / p95 latency |
|---|---|---|---|---|
| Legacy unfiltered `tools.search_docs` (BEFORE) | **51/100 (51.0%)**; 20/20 queries affected | n/m | n/m | n/m |
| Scoped, dense + BM25 + RRF | **0/100** | 20/20 | 13/20 | 73 / 134 ms |
| Scoped, dense only | **0/100** | 20/20 | 16/20 | 67 / 80 ms |
| Scoped, dense + BM25 + RRF + cross-encoder | **0/100** | 20/20 | 14/20 | 1,167 / 1,633 ms |

**Interpretation, with caveats.** Wrong-version leakage on single-version queries went from 51.0% to 0% in every variant, and the filtered pool still returned the right endpoint every time (so the filter is not "safe by returning nothing"). The top-1 differences between variants (13 / 16 / 14 of 20) are within noise for 20 templated queries over a 39-chunk corpus; **I draw no conclusion about hybrid vs dense vs rerank from them.** The cross-encoder costs ≈1.1 s p50 on this CPU with no top-1 gain here. Strategy choice is deferred to the Phase 8 eval using the golden set's `expected_chunk_id`s. Queries are templated and include the endpoint name, so this overstates real-world recall.

## Retrieval-strategy decision from the 45-question golden set (added after the graph was built)
Ground truth: `agent_race/golden_sets/gs_{easy,medium,hard}.json` (45 questions; 9 negative). Each entry's `metadata.target_file` / `page_number` is the correct source page (**lists for comparison questions**: one page per version). A passage is a hit if its `(source_name, page)` is a target. 31 questions are scored; 5 version-less questions are correctly sent to clarification by the resolver; 9 negative cases are for the abstention tests in Phase 8.

**Resolver on all 45 human-written questions:** 25 single-version, 15 comparison, 5 ask-the-user (all five name no version), 0 ambiguous; **0 resolved versions contradicted the golden `api_version`**. (Before widening the comparison markers it was 25 / 11 / 5 / 4 ambiguous.)

Variants were defined before running; n = 31, one corpus of 39 chunks:

| Variant | hit@1 | hit@3 | hit@5 | all targets @5 (7 multi-page Qs) |
|---|---|---|---|---|
| A hybrid, raw question (what the graph did) | 15 | 26 | 30 | 3 |
| H hybrid + endpoint boost | 24 | 29 | 30 | 3 |
| E dense-only, raw | 22 | 27 | 31 | 3 |
| **G dense-only + endpoint boost (chosen)** | **28** | 30 | **31** | 3 |
| B hybrid, version strings stripped | 15 | 27 | 29 | 4 |
| C hybrid, stripped + boost | 22 | 29 | 29 | 4 |
| D dense-only, stripped + boost | 26 | 28 | 30 | 3 |
| F hybrid + cross-encoder, stripped + boost | 27 | 30 | 31 | 5 |

Isolated effects: **endpoint boost +9 (hybrid) / +6 (dense) hit@1; stripping version strings −2 in both pairs (not adopted); dense beats hybrid on this corpus (28 vs 24 with boost); the cross-encoder (27) is no better than dense+boost (28) for ≈1.1 s extra p50 on CPU.**
**Decision / defaults:** dense-only + endpoint boost on the raw question; no stripping; reranker off; hybrid off (all env-switchable: `SDK_ENGINE_HYBRID`, `SDK_ENGINE_USE_RERANKER`).
**Caveats that matter:** I chose among 8 variants using the same 31 questions I scored them on, so 28/31 is **optimistic**; differences of 1–2 questions are noise; the corpus is 39 chunks (BM25's usual advantage on rare tokens barely applies); hit@k is page-level, not answer quality. Held-out cases in the Phase 8 suite must re-check this choice.

## Tests (what they prove)
- Strict scope never returns another version — for all four versions, hybrid and dense-only, 7 queries each, including a query that names another version in its text.
- Unfiltered Chroma *does* return other versions on the same data (so the filter is doing real work, verified in-test).
- The BM25 leg runs over the filtered pool only (`filtered_pool == 2` for `2022-11-28`).
- Chunks with missing / `unknown` version metadata are never returned.
- Comparison scope returns both versions, labelled; content-type scope returns only changelog text.
- 11 invalid scope constructions are rejected; scope is immutable.
- A store whose filter is broken (ignores `where`) is caught by the post-check (`ScopeViolation`).
- `top_k` is clamped; empty pool never calls the embedder; reranker count mismatch is an error.
- Resolver: 46 test cases (incl. four verbatim real golden questions and a negative control) covering never-guess, unsupported, conflict, comparison ordering, stray `version 17` ignored when a known version exists, ISO dates never misread as semver.
- Mutation checks (see `phase1.md`) confirm these tests fail when the filter, post-check, redaction or resolver is broken.

## Security considerations
The version restriction cannot be bypassed by prompt text: it is a `where` clause plus a post-check. A broken store filter fails closed. Unknown metadata is excluded, not assumed compatible.

## Remaining limitations
- The endpoint alias table in `versioning.py` is a hand-authored heuristic used only to infer the product; aliases like `python`, `java`, `release`, `pr` can produce false positives (they err toward asking, not guessing).
- The sparse leg scans the filtered pool (`≤ 5000` chunks, else skipped and reported); fine for this corpus, a known scaling limit for `tenx.md`.
- Chunk metadata does **not** yet carry `doc_hash`, `index_version`, `source_type`, or evidence-backed `deprecated/replacement` fields (G-COR-7). Plan: an additive enrichment step (`collection.update`) so legacy tools keep working.
- Single-version-intent only. Migration/changelog retrieval (the labelled cross-version path) exists in the retriever but the **changelog tool is Phase 3**.
- Real-world query recall is unmeasured; Phase 8 will use the 45-question golden set.

## Acceptance
Hard filtering and the resolver: **accepted** (tested, mutation-checked, measured). Phase 2 overall: **open** pending metadata enrichment and graph wiring.

## Next
Graph skeleton + CLI (finish Phase 1), then Phase 3: MCP server tools (version-scoped search, changelog/deprecation, symbol check), allowlisting client with timeouts/size caps/trace propagation, independent stdlib client test.
