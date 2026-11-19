# Phase 8 — Integrated evaluation suite

Date: 2026-11-04 (runs are stamped UTC, 2026-11-03 evening) · **Status: COMPLETE except one requirement: judge agreement with human labels is NOT MEASURED (0 labels).** That needs you; everything is prepared (`evidence/phase8/HAND_LABELS_NEEDED.md`, 23 items). The suite has 40 cases covering all 16 areas in the brief, deterministic assertions decide every case, the final full run is 40/40, and five real observed failures are permanent RED/GREEN regressions. The 40/40 comes **after iteration** (the first full run was 37/39; see below), so read it with the limits at the end.

## The brief's requirements
| Requirement | Status |
|---|---|
| Week 6 suite inspected and preserved; reuse where suitable | **Done.** Untouched (`git status` clean for `eval/`, `taxonomy.md`, `evaluate*.py`, `golden_set.jsonl`, `agent_race/`). Its 26 cases are tied to Stack A's corpus, so its six *modes* are re-expressed on this corpus and the Stack B golden set supplies 9 live questions (below) |
| 25–40 meaningful cases covering the listed areas | **40 cases** (29 offline, 11 live, 5 regression); **16/16 areas** covered. 40 is the top of the range |
| Deterministic assertions before judges | **Done.** Every case is decided by code/metadata/parser/symbol-check assertions (128 checks in the final run). The judge never gates a case |
| Re-check the judge against hand labels on the integrated system; report cases, agreement calculation, ≥85%? | **NOT MEASURED.** Labels must be yours; sheet and command ready. What I *can* report is below, clearly marked as not human labels |
| Record: full-suite results; pass rate by taxonomy mode; retrieval/version-filter correctness; wrong-version code rate; code parse/symbol outcomes; citation correctness; judge agreement + denominator; non-coverage | **Done**, with small denominators stated (judge: not measured) |
| ≥1 permanent integration regression test from a real observed failure, RED then GREEN | **5** (`reg01`–`reg05`), each re-creatable RED and verified GREEN by `eval regress` |

## Week 6 inspected and preserved
* **What it is:** `eval/cases.json` (26 cases: baseline 15, version_confusion_w6 3, mode1_groq_error 2, mode2_relevance_gate_false_refusal 2, mode3_answerable_wrong_chunks 2, mode4_cross_document 2), `assertions.py`, `judge.py` with `judge_v1.txt`/`judge_v2.txt` (model `gpt-oss-20b`), 26 hand labels (`labels_25.json`), `week6_report.md`. **Historical** judge agreement (on *Stack A's* frozen outputs): v1 21/26 = 80.8%, v2 20/26 = 76.9%, **both below 85%**.
* **Why its cases cannot be asked of this engine:** they are questions about an "OctoClient" SDK in Stack A's corpus (`developer_docs`); this engine answers from the real GitHub/Swagger docs (`race_docs`, your choice of corpus B). I did **not** run the legacy suite: it generates with `groq/compound` (built-in tools, unpriced here) and needs Stack A's index. If you want that run, it needs your go-ahead.
* **What carried over:** the six modes became cases on this corpus: version_confusion → `v01`/`v02`; mode1 (generation error) → `a02` (no model: cited excerpts); mode2 (false refusal) → every answerable live case is a false-refusal check; mode3 (answerable but wrong chunks) → `f01` (endpoint recall within the version); mode4 (cross-document) → `v04`, `g07`, `g08`. The judge rubric was carried over (`sdk_eval/judge_prompt.txt`, with Week 6's two lessons: "question and answer silently about different things", "one-sided comparison is a retrieval failure").
* **Golden set reuse (Stack B, `agent_race/golden_sets/`):** `g01`=easy#12, `g02`=easy#07, `g03`=medium#01, `g04`=medium#02, `g05`=medium#07, `g06`=medium#09, `g07`=hard#06, `g08`=hard#11, `g09`=easy#13 (unanswerable). The golden set also has 8 more unanswerable questions (`None` token/chunk) that are **not** in the suite (one is enough for the abstention path; the rest are a cheap extension).

## The suite (`developer-rag/sdk_eval/`, about 1,500 lines; `python -m sdk_engine eval list|run|judge|label-sheet|agreement|regress`)
* **Offline tier (29 cases):** real index, real local embeddings, the real MCP server subprocess where a case is about tools, **no model API**. Where a case needs a model's behaviour it uses a **scripted** model (an obedient one, one that writes a wrong-version sample), named `scripted/eval-fixture`: unpriced (cost `unavailable`, never a made-up number) and excluded from the live-only metrics. 24 scripted calls in the final run.
* **Live tier (11 cases):** the real model through the real stack, paced to the key's measured limit (17 s between calls); one retry after a rate-limit error.
* **Fixtures made on purpose, and labelled:** a leaky vector store (broken filter), poisoned official pages, poisoned uploads, a community-style page with an obsolete symbol, an MCP gateway whose every call times out.
* **Per-request telemetry:** the whole run shares one trace directory, so `python -m sdk_engine trace ...` can inspect a suite run like any traffic.

| Area (primary + "covers") | cases | | Area | cases |
|---|---|---|---|---|
| version resolution | 5 | | MCP discovery/selection/failure | 5 |
| strict filtering | 3 | | code parsing + symbol validation | 8 |
| breaking changes (pagination is not in this corpus) | 9 | | multi-document / multi-hop | 2 |
| deprecation reporting | 4 | | tool selection + specialist routing | 3 |
| version-tagged citations | 8 | | trace propagation + PII redaction | 4 |
| abstention | 3 | | cache isolation + invalidation | 2 |
| community content, obsolete symbols | 1 | | uploaded-document QA | 3 |
| prompt injection (retrieved + uploaded) | 4 | | cross-user isolation | 2 |

## Results
**Final full run** (`evidence/phase8/run_final/`, 2026-11-03T19:45Z, model `openai/gpt-oss-120b`, index 39 chunks): **40 of 40 pass** (128 checks). **First full run: 37 of 39**, and before it the first offline run was 26 of 28. All four early failures were investigated rather than adjusted away:
* `m03`: my fixture (`target_commitish`) was already in the retrieved text (a version-scoped search also returns a `create_release` chunk), so the engine correctly needed no lookup. Fixed the fixture (`has_issues`).
* `m02`: my timeout double emitted no `mcp.call` span, so the trace showed no tool failure. Fixed the double to mirror the real client.
* `g07`, `g08` (live): "both versions are named in the answer" failed because the model writes dates with **U+2011 non-breaking hyphens**. My check was brittle — **and chasing it found a real engine defect** (next section).

| Taxonomy mode (tag) | executed | pass |
|---|---|---|
| version_confusion | 15 | 15 |
| false_refusal | 12 | 12 |
| unsafe_content | 6 | 6 |
| code_wrong_version | 5 | 5 |
| over_blocking *(created by the integration itself)* | 5 | 5 |
| tool_path_failure | 5 | 5 |
| isolation_breach | 4 | 4 |
| retrieval_miss | 3 | 3 |
| cross_document | 3 | 3 |
| generation_unavailable | 2 | 2 |
| unsupported_answer | 2 | 2 |

(A case carries one or more tags. These 11 tags are the suite's labelling scheme; Phase 9 re-scores the Week 5 taxonomy into 4–7 *observed* modes.)

| Required metric | Result (denominator) |
|---|---|
| Retrieval / version-filter correctness | **0 wrong-version chunks of 325 returned**; asked endpoint retrieved within its version **20/20** pairs |
| Wrong-version code samples **delivered** | live model: **0 of 1** code sample (n = 1: not a rate; larger live evidence is in Phase 6 [6 blocks] and Phase 7 [1], not pooled here); scripted wrong-version samples **blocked 3/3** (a 2025 field, a field the next version says did not exist, a CLI flag 3.x added) |
| Code parse + symbol outcomes | 11 answers with code (1 live, 10 scripted): syntax parsed 10, error 1 (the scripted bad-Python case); tiers cited 10, elsewhere_in_version 2, deprecated 1, other_version_only 1, absent_per_docs 2; **delivered with a blocking finding: 0** |
| Citation correctness (live answers) | **12 of 12** correct, where correct = retrieved in this request AND version within the allowed set AND has source name and location. *Whether a citation supports its claim is not checked deterministically* |
| Cost of the run (user-query cost) | **$0.004141 priced over 11 live model calls** (mean $0.000376); 24 scripted calls unpriced by design. Judge cost is separate (below) |

## The defect this phase found (and how)
**Non-ASCII hyphens (real, in the engine).** Probe over six dash characters (U+2010, 2011, 2012, 2013, 2014, 2212):
1. A user's question with `2025‑06‑01` (pasted from a web page or autocorrected by a word processor) resolved to **`unsupported/None`**, so they were asked to clarify and could not proceed.
2. A 2022-11-28 caller's answer saying "in 2025‑06‑01 the field is `assignees`" **verified `ok=True`**: the wrong-version-claim check only read ASCII hyphens, and the model emits U+2011 routinely.

RED: 23 new tests failed (resolver, explicit versions, comparisons, endpoint words, the answer-side check). Fix: one `normalize_dashes` applied at the entry points of `versioning.py` (`_extract_mentions`, `resolve_version`, `detect_endpoints`, `detect_products`). GREEN: 98 tests in the two affected files. **How it was found, honestly:** by investigating a *failing check of mine*, not by a check designed for it. The suite then gained `reg05` so it cannot come back. This is also the clearest candidate for "a failure mode created by the integration itself" in Phase 9 (the model's typography meeting a version parser).

## Suite sensitivity: does it fail when the engine is broken?
Nine engine-level defects re-created in-process, one at a time (`evidence/phase8/suite_sensitivity.py`; a simulation of each defect, not historical code), offline tier of 29 cases:

| Injected defect | Detected by |
|---|---|
| broken vector-store filter **and** its post-check removed | 11 cases (`v01 v02 f01 f02 a02 x03 m02 k01 k02 reg01 reg02`) |
| output safety checks off | 4 (`i01 i02 reg02 reg04`) |
| code validation off | 6 (`o01 m03 k01 k02 k03 reg03`) |
| uploads not isolated by owner | **1** (`x01`) |
| unknown version guessed instead of asked | 9 |
| tool failure not handled | 5 |
| PII redaction off | **1** (`t02`) |
| changelog lookup returns nothing | 2 (`d01 m01`) |
| citation verification off | 2 (`c01 reg05`) |

**9 of 9 detected**; two rest on a single case (uploads isolation, PII redaction), so those areas are thin. This measures the suite, not the engine.

## Regression RED/GREEN (`python -m sdk_engine eval regress`; `evidence/phase8/regress_red_green.txt`)
| Case | Real observed failure (when) | RED with the defect re-created | GREEN now |
|---|---|---|---|
| `reg01` | a correct release example with `"tag_name": "v2.0.0"` was withheld as a Swagger version claim (Phase 6 live) | fail | pass |
| `reg02` | a correct curl answer was withheld because the user's own pasted token was echoed in `Bearer …` (Phase 7 live demo) | fail | pass |
| `reg03` | a multi-line `-d '{…}'` body produced a syntax error and JSON keys listed as shell commands (Phase 6 live) | fail | pass |
| `reg04` | with no answer model, a poisoned page's auto-loading image was printed unchecked (Phase 5 live) | fail | pass |
| `reg05` | a pasted or model-written date with a non-breaking hyphen was not a version (Phase 8 live) | fail | pass |

**5 of 5 verified.** A defect re-creation is a simulation of the old behaviour. The *original* RED→GREEN runs were recorded when each was found: e.g. the dash fix went from 23 failed to 98 passed; the Bearer fix from "1 failed, 35 passed" to "101 passed"; the semver case reproduced `ok=False failures=['…2.x']` before and `ok=True` after.

## The judge, and what is and is not known
* **Design:** model `openai/gpt-oss-20b` (as in Week 6), the Week 6 rubric adapted, temperature 0. **It never decides a case.** It stays a secondary signal until it clears 85% against *human* labels on a sample large enough to mean something (the tool flags n < 20).
* **Not human labels (informational only):**
  * On the 10 real live outputs it said **PASS 10/10**; all 10 are cases the deterministic checks also passed. An all-PASS sample cannot show the judge catches a wrong answer, which is why I added negatives.
  * On **13 answers I deliberately corrupted** (5 wrong fact, 3 false refusal, 2 answers to a different question, 2 one-sided comparisons, 1 invented figure) it said **FAIL 13/13**. This is measured against *my construction*, a sanity check that it can say FAIL. My negatives are blatant; Week 6's judge disagreed with a human on *subtle real* cases (80.8% / 76.9%). 13/13 here does not predict it clears 85% on those.
* **Judge cost, reported separately from user-query cost:** $0.001498 for the first 10 (10 PASS), then $0.003302 for the 23-item set (38,388 tokens).
* **To finish (needs you):** label the 23 items blind (`evidence/phase8/HAND_LABELS_NEEDED.md`): 10 real + 13 constructed, shuffled under opaque ids; your label stands even if you pass one of my "bad" answers. The command reports `n`, agreement, ≥85%?, every disagreement, and how often you differed from my construction. With 0 labels it prints **NOT MEASURED** (tested), never 0% or 100%.

## Commands run and actual results
* `pytest -q` (no API key in the environment): **679 passed** in 60 s; progression this session 615 → 679 (+64): `test_sdk_eval` 35 (new; 2 are integration tests that run the real offline tier and the RED/GREEN check), `test_versioning` 71 (+25), `test_answering` 27 (+4). No tracked file modified; Week 5/6/8 files untouched.
* Live tier: 11 cases, 11 model calls, about 3 minutes paced (the 11 live cases took 177 s in total; none took long enough to suggest a rate-limit retry, though the runner does not count retries).
* GitNexus: the changed symbols (`versioning` functions, `cli.main`) are in untracked files, so `risk: UNKNOWN`, resolved by text search; `detect_changes` **not** run (nothing is being committed).

## Known weaknesses and non-coverage (also in every report.md)
1. **40/40 is after iteration**: four early failures were fixed (two were my fixtures, one my brittle check, one a real engine defect). The live tier is 11 cases, one run each, on a provider that is not perfectly deterministic (earlier live runs differed run to run). There is no variance estimate.
2. **Judge vs human is unmeasured**; my negatives are blatant; the historical prior is below the bar.
3. **Deterministic assertions check form, not truth**: gold tokens, version labels, citation shape, parse/symbol evidence. They do not check that a citation *supports* its claim or that an answer is complete beyond its gold token; some wrong answers would pass them (that gap is what the judge and a human are for).
4. **Small denominators**: wrong-version live code rate n = 1; citation correctness 12; each taxonomy tag 2–15 cases.
5. **Scripted-model cases test the defences, not the model**; `o01` is one planted page, not real community content.
6. **Not covered**: version-specific pagination and removed-method scenarios (not in the corpus), a persistent cache (none exists), a hostile MCP server (unit tests only), PDF/DOCX uploads (unit tests; `u01` uses Markdown), the 8 other unanswerable golden questions, concurrency and load (Phase 10), the live prompt-injection run (Phase 5 evidence, not this suite), the 26 Week 6 cases themselves.
7. **Thin detection** for uploads isolation and PII redaction (one case each); the suite is at the maximum of the 25–40 range.
8. The suite needs the local index and embedder; it is not runnable in CI without them (CI is Phase 11).

## Reproduce
```
cd developer-rag
..\.venv\Scripts\python -m sdk_engine eval list
..\.venv\Scripts\python -m sdk_engine eval run                       # offline tier: no key needed
..\.venv\Scripts\python -m sdk_engine eval run --live --out <dir>    # + the 11 live cases (needs GROQ_API_KEY; ~$0.004, ~3 min)
..\.venv\Scripts\python -m sdk_engine eval label-sheet --run <dir>   # the blind sheet + template (23 items)
..\.venv\Scripts\python -m sdk_engine eval judge --run <dir>         # judge verdicts (secondary signal)
..\.venv\Scripts\python -m sdk_engine eval agreement --run <dir> --labels <yours> --verdicts <...> --key <...>
..\.venv\Scripts\python -m sdk_engine eval regress                   # RED/GREEN for the 5 regression cases
```
