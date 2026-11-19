# Week 12 — Final review

Date: 2026-11-04 · Branch `week12_implementation` · Nothing committed, pushed or deployed; `git diff` is empty (every Week 12 file is new and untracked).

## 1. Verdict

The integrated SDK-docs answer engine exists, runs from one CLI, and is covered by 750+ tests and a 40-case evaluation suite. **One definition-of-done item is not met and is not claimed: judge agreement against hand labels is NOT MEASURED**, because it needs a person's labels (owner decision Q7) and I must not label for them. Four more items are met only with limits that matter and are stated below. The two most important things the work found are about answer correctness, not infrastructure: a **wrong answer can be delivered with a citation that looks like proof** (`easy_11`), and **abstention depends entirely on the model** because the retrieval gate never fires.

## 2. Definition of done (brief §18), item by item

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | correct branch and prior work identified | met | `architecture_current.md` §5; owner chose `week12_implementation` from `week11_implementation` |
| 2 | original components inspected | met | `architecture_current.md` (some legacy files header-only, tagged) |
| 3 | one documented entry point | met | `python -m sdk_engine`; `sdk_engine/README.md` |
| 4 | LangGraph integrated and tested | met | `graph.py`, 14 nodes; `tests/test_graph*.py` |
| 5 | version resolved before version-sensitive retrieval | met (official corpus) | graph edge order; 71 versioning tests |
| 6 | version filtering enforced in the retriever | met | filter inside the Chroma query, re-checked twice; wrong-version chunks 0/225 live; mutation-checked |
| 7 | citations carry the correct version and source | **met with a gap** | structure verified; **support of the claim by the cited text is not checked** (`easy_11`) |
| 8 | deprecations and removals surfaced proactively | **met with limits** | notes on 40/45 live results; the corpus never says "removed", so none is claimed; pagination scenarios are not in this corpus |
| 9 | three tool capabilities | met | `search_docs`, `lookup_changes`, `check_symbol` |
| 10 | tools discovered at runtime | met | `tools/list` at client start; `health --deep` prints them |
| 11 | our server callable by a separate client | met | stdlib JSON-RPC client in `tests/raw_mcp_client.py` |
| 12 | code parsing and version-specific symbol checks | **met with limits** | `ast`/JSON/shell/curl parsed; imports, signatures, timeouts, execution not checkable with this corpus |
| 13 | uploads indexed, searched, cited, deleted safely | met within limits | physical delete verified 0/40; **no authentication** |
| 14 | injection and PII regression tests | met, residual risk named | 107 security tests + redaction tests; answer wording can be steered (1/28 live) |
| 15 | one trace id across agent and MCP hops | met | e2e test; `evidence/phase7/sample_trace.txt` |
| 16 | integrated suite of 25–40 cases | met | 40 (29 offline / 11 live / 5 regression) |
| 17 | **judge agreement vs hand labels, with denominator** | **NOT MET: NOT MEASURED** | 0 human labels; 23-item blind sheet ready (`evidence/phase8/HAND_LABELS_NEEDED.md`) |
| 18 | 4–7 taxonomy modes incl. an integration-created one | met | `error_taxonomy.md`: 7 modes; mode 7 |
| 19 | trajectory evaluation ≥10 cases | met, limited | 10; measures rules, not LLM tool choice |
| 20 | RED/GREEN failure-to-test loop | met | `eval regress`: 5/5 real defects (RED with the defect re-created, GREEN now) |
| 21 | cost per query and stage costs | met | `cost_by_stage.md`: $0.000388 mean; only generation is billed |
| 22 | 10× analysis with a bottleneck | met | `tenx.md`: provider token quota first (hit during this work); engine ≈7 req/s second |
| 23 | README lets a stranger install, configure, index, query, evaluate | **partly demonstrated** | a fresh subagent checked the README against the code (18 mismatches, all fixed) and the unit suite passes in a clean checkout (735); **the full clean-checkout walkthrough (embedding install, index rebuild, query, eval) was not completed**: one attempt hit Windows' path limit, the other was stopped by the system for low memory (`phase11.md`) |
| 24 | full suite and security checks run | met | see §4 |
| 25 | known weaknesses and non-coverage explicit | met | `known_weaknesses.md`, technical guide §20 |
| 26 | no secrets, invented measurements or unverified claims | **checked, not proven** | shape-based secret scan: 0 hits; every number cites a file; I corrected three of my own wrong statements during Phase 10 (listed in `phase10.md`); absence of any unverified claim cannot be proven |

## 3. What the final report must contain (brief §19) and where it is

| # | Item | Where |
|---|---|---|
| 1 | branch used and why | `week12_plan.md` §1, §9a |
| 2 | what existed before Week 12 | `architecture_current.md`, `baseline_results.md` |
| 3 | reused / modified / added / not added | technical guide §2 |
| 4 | final architecture and request data flow | `architecture_target.md`, technical guide §8, §10 |
| 5 | exact setup, index, query and evaluation commands | `sdk_engine/README.md` |
| 6 | test results, denominators, known failures | §4 below |
| 7 | judge agreement and trajectory metrics | judge: **not measured**; trajectory: `trajectory_eval.md` (path ok 8/10, tool choice 21/23, outcome 10/10) |
| 8 | the regression case's RED and GREEN | `evidence/phase8/regress_red_green.txt`, `evidence/phase9/regress_red_green_after_fix.txt` |
| 9 | a sample end-to-end trace, redacted | `evidence/phase7/sample_trace.txt`, `phase7.md` |
| 10 | cost per question and the 10× bottleneck | `cost_by_stage.md`, `tenx.md` |
| 11 | blockers, weaknesses, unsupported claims | §5 below, `known_weaknesses.md` |
| 12 | the path to the 10-minute four-part demo | `demo_script.md` |

## 4. Test and measurement results (2026-11-04)

| what | command | result | denominator / caveat |
|---|---|---|---|
| everything, integration tests included | `python -m pytest -q` | **749 passed**, 247 s (about 02:08, after Phase 9) | includes the 11 tests that need the real index and the local embeddings |
| after the review fixes: unit, protocol, security | `python -m pytest -m "not integration" -q` | **743 passed**, 11 deselected, 208 s (03:09) | |
| after the review fixes: integration (real index, real embeddings) | `python -m pytest -m integration -q` | **11 passed**, 743 deselected, 94 s (21:48 the same evening, after waiting for memory) | together with the row above: all **754** tests pass on the final code |
| clean checkout (no `data/`, no `.env`), fresh venv from `requirements-dev.txt` only (no torch) | same | **735 passed**, 11 deselected, 61 s | before the review fixes; Windows 11, Python 3.14.8 |
| regression RED/GREEN | `python -m sdk_engine eval regress` | **5/5 verified** (RED with the defect re-created, GREEN now); `reg05` RED fails 3 of 3 checks | `evidence/phase9/regress_red_green_after_fix.txt` |
| integrated suite, final run | `eval run --live` (Phase 8) | 40/40 after iteration (first full run 37/39); sensitivity: 9/9 injected engine defects detected, 2 by a single case | `phase8.md`; the suite was tuned until it passed |
| golden set, live, whole engine | `eval golden` | **43/45** judged correct (answerable 34/36, unanswerable refused 9/9); wrong-version chunks 0/225 | `error_taxonomy.md`; judge agreement with a human **not measured** |
| trajectory | `eval trajectory` | path ok 8/10, tool choice 21/23, outcome 10/10, failed calls 0 | rules, not LLM choices |
| local load test | `eval load` (scripted instant model) | 160 requests, 0 failures; 5.4 / 6.9 / 6.8 / 3.1 req/s at concurrency 1 / 2 / 4 / 8 | one machine; `tenx.md` |
| index integrity | `index check` | ok: 39 chunks, 24/24 pages, 0 version mismatches, 0 untagged | real index |
| deep health | `health --deep` | status ok; MCP start-up 23.7 s; three tools authorised; probe search 95.8 ms | real index |
| secret tripwire | `python scripts/scan_secrets.py` | **397 files, 0 suspicious lines** | shape-based: unknown key formats are not found |
| tracked files modified | `git diff --name-status` | **none** | everything is new and untracked |

**Known failures:** `easy_11` (a wrong answer with a citation) and `hard_03` (a false abstention) in the golden run; 26 of 45 requests degraded in the latency run when the provider's daily token quota ran out; the clean-checkout walkthrough of the README was not completed (`phase11.md`).

## 5. What is still wrong or unproven

1. **Wrong cited answer** (`easy_11`, 1/45): no claim-support check; cause upstream is a PDF-extraction retrieval miss (`known_weaknesses.md` #1). Two fixes proposed; **both await your approval** because one rebuilds the index and one decides what happens to an unsupported answer.
2. **Abstention rests on the model**: the distance gate admitted 9/9 unanswerable questions; the model refused 9/9 with one model at temperature 0 (#2).
3. **Headline numbers are not independent**: judge-vs-human agreement is not measured; the golden set is not held out; the suite was iterated on (#3).
4. **Provider capacity**: the free key allows about 109 answers a day; the engine has no queue or backpressure (`tenx.md`).
5. **Retrieval will not scale as written**: the whole-pool fetch per query (ledger G-OPS-5).
6. **No authentication**; **pasted secrets still reach the provider** (your decision); **answers whose code could not be checked are still `answered`** (your decision).
7. **CI has never run on GitHub**; Linux and a from-scratch embedding install are untested; the clean-checkout walkthrough is incomplete (`phase11.md`).
8. The Pydantic router the brief mentions was not found on this machine (Q5); tracked `traces.jsonl` untouched (Q6).

## 6. Decisions waiting for you

Q5 (router location), Q6 (tracked `traces.jsonl`), **Q7 (hand labels: the one unmet requirement)**, the outcome label for unchecked code, redaction before the provider, and the two approvals in §5.1. Nothing has been committed: say when and how you want it (the working tree is entirely new, untracked files plus ignored data).

## 7. If I had one more day

In order: your hand labels → judge agreement; the claim-support check with `easy_11` and `hard_03` as regression cases; the chunk-level re-join of split identifiers and an index rebuild; a paid key and a client-side token bucket; replace the whole-pool fetch; a second review of the technical guide by a person outside the project.
