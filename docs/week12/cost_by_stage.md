# Week 12 — Cost and latency by stage

Date: 2026-11-04 · Machine: one Windows 11 laptop-class machine, Python 3.14.8, CPU only · Evidence: `evidence/phase9/` (45-question quality run), `evidence/phase10/` (latency run, load test, probes). Nothing here is an invoice: cost is **calculated** from token counts the provider returned and the price table in `sdk_engine/pricing.py`.

## 1. How to read the numbers (and what went wrong in the measurement)

* **Rates.** gpt-oss-120b $0.15 input / $0.60 output per 1M tokens; gpt-oss-20b (judge) $0.075 / $0.30. These match Groq's published model pages as looked up on 2026-11-04 ([120b](https://console.groq.com/docs/model/openai/gpt-oss-120b), [20b](https://console.groq.com/docs/model/openai/gpt-oss-20b)); the search result noted that some Groq pages carry old dates, so re-check before budgeting. Groq lists a 50% cached-input discount; the engine does not model it, so cost is a slight **over**statement if caching applies.
* **Two runs, different purposes.** The *quality run* (45 golden questions, model paced inside the call) gives tokens and cost for all 45: its latency is invalid because the pacing sleeps inside the model call. The *latency run* (45 questions, no pacing, one request every 20 s) was meant to give latency, and **broke**: from `medium_04` on, the provider answered `429` ("tokens per day (TPD): Limit 200000, Used 198646, Requested 1455"), so **26 of 45 requests degraded** (cited excerpts, no model). Only **19 requests reached the model** — 15 easy, 4 medium, **no comparison question** — so there is no with-model latency for comparison questions, and every p95 below is the slowest of n = 19 (not a stable percentile). p99 is not reported anywhere.
* **Interference.** While the latency run executed I ran other jobs on the same machine (a 24 MB text search, a read-only review agent). Local-stage timings, especially of the 26 degraded requests (measured 02:28–02:37), are indicative, not clean.
* **Temperature 0 is not deterministic.** Re-running the same 19 questions gave the same outcome and judge verdict 19/19, but only 11/19 answers were word-for-word identical after hyphen repair. The wrong answer to `easy_11` reproduced.

## 2. Cost per question

Quality run, all 45 golden questions (`evidence/phase9/golden_analysis.json`, `golden_results.json`), 1 model call each except one (`medium_10`, a verification retry: $0.000837, the maximum):

| | n | mean | p50 | p95 | max |
|---|---|---|---|---|---|
| **cost per question (USD)** | 45 | **0.000388** | 0.000335 | 0.000695 | 0.000837 |
| tokens per question | 45 | 1,836 | 1,765 | 2,437 | 3,530 |
| prompt tokens | 45 | 1,587 | 1,597 | 1,755 | 2,846 |
| completion tokens (includes the model's hidden reasoning) | 45 | 249 | 150 | 684 | 789 |

By shape: single-version question $0.000333 mean (n = 30, 1,738 tokens); version comparison $0.000496 (n = 15, 2,033 tokens, two retrievals so more passages). By level: easy $0.000308, medium $0.000376, hard $0.000479. By outcome: answered $0.000409 (35), abstained $0.000314 (10): a refusal still costs a full model call. A **degraded** request (no model) costs **$0 in API charges** (26 of them in the latency run).
Total for the 45: **$0.017443** (82,632 tokens). The latency run's 19 model calls cost $0.006146 (mean $0.000323, all single-version).
The prompt is 86% of the tokens (1,587 of 1,836), so cost follows the number and size of retrieved passages far more than the length of the answer.

## 3. By stage

Stage times are the engine's own spans (`request` is the root; nested stages overlap, so the rows do **not** add up). Latency run, **requests that reached the model, n = 19** (single-version questions only):

| brief's category | engine stage(s) | model call | API cost | time p50 / p95 / max | notes |
|---|---|---|---|---|---|
| routing and decision calls | `validate_request`, `select_corpus`, `resolve_version`, `route`, `assess_sufficiency` | **none** (rules) | $0 | 0.02–0.43 ms / ≤ 10.6 ms | 0 model calls for routing; the "repeated routing calls" risk does not exist in this design |
| retrieval and reranking | `retrieve` (MCP `search_docs`, including the query embedding) | none | $0 | **98 / 451 / 451 ms** | server side 84 ms p50; the real bge query embedding alone is 80 ms p50 (n = 45); the reranker is off (it measured ~1.1 s p50 on CPU in Phase 2) |
| specialist / agent model calls | none exist | **none** | $0 | n/a | the "specialists" are labelled steps, not models |
| MCP or external tool execution | `mcp.call` (client round trip); `changelog` (`lookup_changes`) | none | $0 | call 103 / 461 ms; changelog **13.5 / 15.2 ms** (n = 15) | `lookup_changes` is 2.1 ms server side; `check_symbol` ran only for the 2 of 45 answers that contained code |
| final answer generation | `generate` (the one model call) | **gpt-oss-120b** | **$0.000323 mean** (this run), $0.000388 (45-question run) | **761 / 1,391 / 1,391 ms** | 84.7% of a request at p50 (range 62–88%) |
| verification and delivery | `verify_citations_and_version`, `finalize` | none | $0 | 0.32 / 0.71 ms; 0.09 / 0.38 ms | code validation runs inside `verify` when there is a code block |
| **whole request** | `request` | 1 | | **950 / 2,021 / 2,021 ms** | everything except generation: p50 **135 ms**, p95 631 ms |

Engine-only path (no model: the 26 requests degraded by the quota, n = 26; 11 single-search, 15 comparison): request p50 **294 ms**, p95 879 ms; single-search p50 245 ms, comparison p50 376 ms (two strictly scoped searches, then the changelog). That is the cost of everything the engine does that is not the model, under the interference noted above.

## 4. Costs reported separately, as the brief asks

* **Evaluation / judge.** The grader (`gpt-oss-20b`) used 19,117 tokens for the 45 golden answers: $0.0014 if all tokens were input, $0.0057 if all were output (only the total is recorded, so it is bounded, not computed). The 19 latency-run gradings used 6,667 tokens ($0.0005–$0.0020). Suite-level judge costs are in `phase8.md`. None of this is added to a user query.
* **Embedding and indexing.** Local CPU, **no API charge**. Embedding model load 18.6 s (probe) and MCP server start-up 23.7 s (`health --deep`) are one-off per process; per query, 80 ms p50 (n = 45). Index build time and upload ingest time: see `phase11.md` and `phase4.md` (CLI cold start ≈ 12–13 s per call).
* **Provider limits are part of the cost.** See `tenx.md`: the same key that yields $0.000388 per query also limits the day to about 109 of them.

## 5. What this does not tell you

Cost per query on *your* traffic (the golden set is 45 author-chosen questions: a third are comparisons, which cost 49% more); latency under real concurrency with a real model (the load test used a scripted instant model: `tenx.md`); latency of comparison questions with the model (none reached it); any figure at p99; any figure for a different model, prompt or index size.
