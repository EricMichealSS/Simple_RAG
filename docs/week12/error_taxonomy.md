# Week 12 — Failure-mode taxonomy, re-scored on the integrated engine

Date: 2026-11-04 · Method: the Week 5 taxonomy (`developer-rag/taxonomy.md`, 5 modes over 20 legacy traces) re-scored on the
integrated engine, plus the modes the integration itself created. Evidence: `evidence/phase9/`.

## 1. What was measured, and how far to trust it

* **Sample.** All 45 questions of the Week 7 golden set (15 easy / 15 medium / 15 hard; 36 answerable, 9 unanswerable), run live
  through the whole engine (version resolution → MCP tools → `openai/gpt-oss-120b` → verification) on 2026-11-04. This is the *author's*
  golden set, not production traffic: frequencies below are frequencies on this set, **not** in the field. It is also not held out:
  the set was used in Week 7 and for Phase 2's retrieval comparison, so it is optimistic.
* **Run conditions.** The model call was paced to the key's measured limit (8,000 tokens/minute), so provider errors (Week 5 mode 1) were
  *prevented by the harness*, not handled by the engine, in this run. Latency from this run is invalid for the same reason (Phase 10 measures it separately).
* **Judging.** `openai/gpt-oss-20b` graded each answer against the golden answer (the Week 7 grader prompt). Its agreement with a
  human is **not measured** (needs your labels, owner decision Q7). I therefore also read all 35 answered outputs and the 10
  abstentions against the golden answers myself: I agreed with the judge on all 45, and found one defect the judge was not asked
  about (mode 7). That reading is one reader, not independent labels.
* **Denominators are small** (45, 36, 35, 15). One question is 2.2 points of the 45. Nothing here supports a claim about unseen questions.

Headline on this set: **43/45 judged correct** (answerable 34/36; the 9 unanswerable questions were all refused; easy 14/15, medium 15/15,
hard 14/15). 35 answered, 10 abstained, 0 degraded, 0 errors, 0 clarifications. Wrong-version chunks retrieved: **0** of 225 passages.
Cost: $0.017443 for the 45 (mean $0.000388, max $0.000837), every query priced.

## 2. The taxonomy (7 modes, 5 re-scored from Week 5 and 2 new, 1 of them integration-created)

| # | Mode | Origin | Count | Denominator | % | Severity | Representative trace id |
|---|---|---|---|---|---|---|---|
| 1 | Generation unavailable: no generated answer, excerpts instead | Week 5 mode 1 (was 4/20 = 20%) | **0** in the paced quality run; **26** in the unpaced latency run (provider daily quota exhausted) | 45 / 45 | 0% / **57.8%** | HIGH | `1f35a95d9efb4dfdb9d6606e9d0533b0` (medium_04, degraded: `RateLimitError`) |
| 2 | Retrieval miss: the answer-bearing chunk is not retrieved for an answerable question | Week 5 mode 3 (was 2/20 = 10%) | 2 | 36 answerable | 5.6% (page-level proxy says 1/36 = 2.8%) | MEDIUM | `aa392f021d174b9ea58f5967401d04e8` (hard_03), `d9566c777e434934a0eb76e96d7a9899` (easy_11) |
| 3 | Confident wrong answer whose citation is a retrieved chunk that does not state it | **new** (a consequence of mode 2; the verifier checks that a citation exists and is in scope, not that it entails the claim) | 1 | 45 (35 answered) | 2.2% (2.9% of answered) | **HIGH** | `d9566c777e434934a0eb76e96d7a9899` (easy_11) |
| 4 | Sufficiency gate cannot tell answerable from unanswerable | Week 5 mode 2 re-scored (was "gate false refusal", 1/20) | gate false refusals 0; unanswerable questions the gate stopped **0 of 9** | 36 / 9 | 0% / 0% | MEDIUM (latent: the model's refusal is the only defence, and it held 9/9) | `dff71fa91302442ba982d7ac3c31dea7` (easy_13, top-1 distance 0.315 vs threshold 0.65) |
| 5 | Cross-version question gets only one side | Week 5 mode 4 (was 1/20 = 5%) | 0 | 15 comparison questions | 0% | MEDIUM | none; passing exemplar `688b57ef5dc040318f03fa45cccad7b4` (hard_01) |
| 6 | Retrieval distance logged as a placeholder 1.0 | Week 5 mode 5 (was 20/20 = 100%) | 0 | 225 passages | 0% | LOW | none |
| 7 | **Hyphen look-alikes in the delivered answer (U+2011 and kin) inside identifiers, flags and dates** | **integration-created**: the model, the version verifier and the delivery path each assumed the others used an ASCII hyphen | 17 (8 of them in dates) | 35 answered | **48.6%** before the fix; 0 of 4 re-run after it | HIGH before the fix (silent, plausible, breaks a paste) | `65982cb5f64c4fccaa1a18e07e8a0f7a` (easy_03: `okhttp‑gson` with U+2011; after the fix `a8f3f20a…`) |

Observation, not a mode: 1 of 45 questions (`medium_10`, `5b8f6c8449604e33992720d6b2741525`) needed a second model call because the
verifier rejected the first draft for "claims about versions outside the allowed set: ['2.x']". The retry succeeded. **The rejected
draft is not kept anywhere**, so I cannot say whether the rejection was right or an over-block; that is an observability gap (ledger G-OBS-7).

## 3. Notes per mode

**Mode 1 — generation unavailable (0/45 paced; 26/45 unpaced).** Week 5's biggest mode (4/20) came from sending too much to a rate-limited provider. In the paced quality run the harness waited between calls, so 0/45 proved nothing about handling a 429. The Phase 10 latency run then hit the provider's real limit: **tokens per day, limit 200,000, used 198,646** (`evidence/phase10/provider_limits/`), and from the 19th question on, **26 of 45 requests degraded** with `llm_unavailable: provider call failed: RateLimitError`. Handling was clean: every one returned in 203–904 ms with cited excerpts that pass the same output checks, no exception, no retry storm (`max_retries=0`, no engine retry). It is still the highest-severity *user* outcome: 58% of the requests in that window got excerpts that "may not answer your question". The cause this time is quota, not payload size; the mitigation is capacity and backpressure (`tenx.md` §5), not a smaller prompt. Offline cases `a02` and `reg04` also cover the path.

**Mode 2 — retrieval miss (2/36).**
* `hard_03` ("What top-level configuration key was introduced in Swagger Codegen v3 config files to nest generator-specific options…"):
  both versions were searched (`search_docs` ×2 + `lookup_changes`), but the answer-bearing page (`swagger_v3_reference:p6`, chunk `p6:spec:top:11`)
  was not in the 5 passages. The model refused ("I don't know based on the provided documents"). Refusing is the right behaviour for the evidence it had.
* `easy_11` (default of `allowUnicodeIdentifiers`, Swagger Codegen 3.x): the answer-bearing chunk is `swagger_v3_reference:p3:spec:top:3`; it was not
  retrieved. The page-level proxy scored this a hit because the *diff* chunk from the same page (`p3:diff:top:4`) was retrieved. Why: in the spec chunk, PDF extraction split the
  option name across lines (`allowUnicodeIdentifie | rs`, and `sortParamsByRequire | dFlag`), so the token the question names is not intact there; it is intact (four times) in the diff chunk, which ranked higher.
  Evidence: `evidence/phase9/golden_results.json`, row `easy_11`; chunk texts read from the index.

**Mode 3 — confident wrong answer (1/45).** For `easy_11` the model answered "**true**" and cited `swagger_v3_reference:p3:diff:top:4`. That chunk says, about **2.x**, that defaults were
"written inline in prose, e.g. '(Default: true)'" — an illustration of the 2.x *style*, not a default value. Gold: "false". The engine's checks all passed: the citation exists, is
in the retrieved set, and is in the right version. None of them asks whether the cited text supports the claim. The judge caught it; the engine did not.
This is the most serious finding of the phase: it is the case the verification stage claims to protect against and does not.

**Mode 4 — the sufficiency gate does not discriminate.** Measured with no model call (`evidence/phase9/distance_check.json`, dense retrieval in the resolved version scope):

| | n | top-1 cosine distance: min / median / max |
|---|---|---|
| answerable | 36 | 0.124 / 0.179 / 0.250 |
| unanswerable | 9 | 0.161 / 0.233 / 0.334 |

The ranges overlap and every question sits far below the placeholder threshold 0.65 (the largest top-1 over all 45 is 0.334; the largest of any passage is 0.41). So the distance gate
never fires on this set: **all 10 abstentions were made by the model** (`model_abstained`), 0 by the gate. The gate's Week 5 failure (refusing answerable questions) is gone because it refuses nothing.
The unanswerable questions are adjacent to the corpus (same product, absent feature), which is the hard case. The 9/9 model refusals are the only thing standing between those questions and a fabricated answer.

**Mode 5 — one-sided cross-version retrieval (0/15).** The graph issues one strictly scoped search per version for a comparison, so both versions' chunks
were retrieved for all 15. (`hard_03` shows the remaining gap: both sides' *pages* were present, not the answer-bearing one: mode 2.)

**Mode 6 — placeholder distance (0/225).** No retrieved passage had distance exactly 1.0 or missing. Sparse-only hits carry `None`, covered by `tests/test_retrieval_scope.py::test_sparse_only_hits_have_no_fake_distance`.
Per-chunk distances are **not** written to the spans (the retrieval span records chunk ids and counts), so this was re-scored by re-running retrieval, not read from traces.

**Mode 7 — look-alike hyphens (17/35 answers; integration-created).**
* *What happened.* The model writes typographic hyphens: U+2011 (non-breaking hyphen) 65 times, U+2013 22 times, in 17 of 35 answers; the earlier date-only counter showed 8. In two answers
  the character was inside a code span: `okhttp‑gson` (easy_03) and a library list (hard_05). A user who pastes it gets a string that is not the library name.
* *History.* Phase 8's live suite found the *input* side (pasted dates read as "unsupported"; a model's wrong-version claim written with U+2011 passed the verifier) and fixed it (`normalize_dashes`, regression `reg05`).
  Phase 9's golden run exposed the *output* side: the engine verified the model's text but delivered it unchanged.
* *Why integration-created.* No component was wrong alone: the model's typography is ordinary, the verifier's date grammar was ASCII, the delivery path passed text through. The defect exists between them.
* *Fix, this phase.* `answering.ascii_hyphens` rewrites U+2010/2011/2012 everywhere and U+2013/2014/2212 when glued between two letters or digits, **once, where the draft is stored**, so code validation, verification and delivery all see the same text. Spaced dashes (" — ", "5 – 10") are left alone.
  Verified by 2 unit tests, 1 graph test, `reg05` strengthened (RED with the defect re-created: all 3 of its checks fail; GREEN: all pass; 5/5 regression cases still verified), and 4 live re-runs of affected questions: 0 look-alikes in 4 of 4, `okhttp-gson` now ASCII, judge PASS 4/4. The other 13 affected answers were not re-run live.
* *Not covered.* U+202F (narrow no-break space) appears 93 times, always in prose (before citations, in "400 KB"); no non-ASCII character other than U+2011 occurred inside a code span in these 35 answers. Smart quotes appear in prose only.

## 4. Fix priority

1. **Modes 2 and 3 together** (HIGH: a wrong cited answer). Two independent fixes, neither done here because both change the index or add a model call and are yours to approve:
   (a) normalise PDF-extracted identifiers at ingest (re-join option names split across lines) and rebuild the index — directly addresses the `easy_11` retrieval miss and probably the table-heavy `hard_03` page;
   (b) a *claim-support* check before delivery for questions that ask for a value: the number/word the answer states must appear in the cited chunk near the name asked about
   (deterministic, no model call, would have rejected `easy_11`: the cited chunk contains "true" only inside an example). Both need a regression case; `easy_11` and `hard_03` are the reproductions.
2. **Mode 4** (MEDIUM, latent). Replace the placeholder distance gate with something that discriminates, or stop presenting it as a gate. On this corpus no single distance separates the two sets (0.161–0.250 overlap), so a threshold cannot be tuned from this data.
3. **Observability** (G-OBS-7): keep the rejected draft (redacted, truncated) on the `verify` span so over-blocking becomes measurable.
4. **Mode 1**: done by accident in Phase 10 (above). What remains is capacity: a paid plan or a second provider, and a client-side token bucket with a queue so a 429 becomes a wait (`tenx.md` §5).

## 5. Prediction for the next iteration (falsifiable)

If fix 1(a) is made and the index rebuilt: `easy_11` retrieves `p3:spec:top:3` and answers "false"; modes 2 and 3 drop to 1/36 and 0/45 (`hard_03` stays unless the p6 table is also re-chunked).
If 1(b) alone is made: mode 3 drops to 0/45 and `easy_11` becomes an abstention (mode 2 unchanged at 2/36). Re-measure with `python -m sdk_engine eval golden` and compare with `evidence/phase9/golden_results.json`.
