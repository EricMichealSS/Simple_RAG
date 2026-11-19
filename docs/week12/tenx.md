# Week 12 — What happens at ten times the load

Date: 2026-11-04 · Arithmetic: `evidence/phase10/scripts/tenx_numbers.py` → `evidence/phase10/tenx_numbers.txt` · Measurements: `evidence/phase10/` · Cost and stage detail: `cost_by_stage.md`.

**Answer first.** The first bottleneck is the **provider's token quota**, not anything in this code, and it was hit during this very evaluation. On the free-tier key used here the limit that binds is **200,000 tokens per day** for `gpt-oss-120b` (the provider's own error: "tokens per day (TPD): Limit 200000, Used 198646, Requested 1455"). At the measured 1,836 tokens per query that is **about 109 answered questions per day**. The engine itself saturates later, at about **7 requests/second** on one process with an instant model. On a paid plan, if Groq's published limits apply (250,000 tokens/minute, 1,000 requests/minute for these models: documented, **not measured on this key**), tokens per minute caps sustained throughput near **2.3 requests/second** — below the peak of the "3 questions per integrator per day" scenario at 10×.

## 1. What is measured and what is assumed

There is no production traffic in this repository, so **there is no measured workload baseline to multiply by ten**. The brief gives about 9,000 active integrators and says not to assume they all ask at once. I therefore state the workload as assumptions and show three usage rates; everything else is a measurement from this build.

| | value | status |
|---|---|---|
| active integrators | 9,000 | from the brief |
| questions per integrator per day | 1, 3, 10 | **assumed** (three scenarios; the brief gives none) |
| share of a day's requests in the busiest hour | 10% | **assumed** |
| peak second vs busy-hour average | ×3 | **assumed** |
| tokens per query | mean **1,836** (p50 1,765, p95 2,437, max 3,530); prompt 86% | measured, 45 golden questions (a third are comparisons) |
| cost per query | mean **$0.000388** (p95 $0.000695, max $0.000837) | calculated from tokens and published rates |
| model-call latency | generate p50 **761 ms**, p95 1,391 ms (n = 19, single-version only) | measured; no comparison question reached the model |
| request latency with model | p50 **950 ms**, p95 2,021 ms (n = 19: the slowest request) | measured |
| engine time excluding the model | p50 **135 ms**, p95 631 ms (n = 19); engine-only path p50 294 ms (n = 26, comparisons included) | measured, with interference noted in `cost_by_stage.md` |
| local capacity, one process, **instant scripted model**, real MCP server and real embeddings | **≈ 7 req/s** at concurrency 2–4; 3.1 req/s at 8; 0 failures in 160 requests | measured, one machine (below) |
| memory | engine process ≈ 113 MB; MCP server process ≈ **632 MB** (embedding model + index) | measured, Windows working set |
| provider limits, this key | 8,000 tokens/minute, 1,000 requests/day (response headers) and **200,000 tokens/day** (the 429 message) | measured |
| provider limits, paid plan | 250,000 tokens/minute, 1,000 requests/minute | **documented by the provider, not measured here** (a search of Groq's docs on 2026-11-04; some pages carried old dates) |

## 2. The workload scenarios (arithmetic, not measurement)

| questions per integrator per day | requests/day | busy-hour average req/s | peak req/s | tokens/day | USD/day | USD/30 days | trace GB/day |
|---|---|---|---|---|---|---|---|
| 1 | 9,000 | 0.25 | 0.75 | 16.5M | 3 | 105 | 0.09 |
| 3 | 27,000 | 0.75 | 2.25 | 49.6M | 10 | 314 | 0.26 |
| 10 | 90,000 | 2.50 | 7.50 | 165.2M | 35 | 1,048 | 0.86 |
| **10× of 1** (= baseline at 10) | 90,000 | 2.50 | 7.50 | 165.2M | 35 | 1,048 | 0.86 |
| **10× of 3** | 270,000 | 7.50 | 22.50 | 495.7M | 105 | 3,143 | 2.57 |
| **10× of 10** | 900,000 | 25.00 | 75.00 | 1,652M | 349 | 10,476 | 8.57 |

Concurrency by Little's law (rate × the 0.95 s median request): 10× of 3 needs about 7 requests in flight on average, 21 at peak; 10× of 10, 24 and 71.

## 3. Which limit binds first

| capacity (sustained, at 1,836 tokens per query) | req/s | per day | status |
|---|---|---|---|
| **free key: tokens/day** | 0.0013 | **109** | measured |
| free key: tokens/minute | 0.073 | 6,275 | measured |
| paid plan: tokens/minute | **2.27** | 196,000 | documented, projected |
| paid plan: requests/minute | 16.7 | 1.4M | documented, projected |
| local engine, one process | **≈ 6.9** | 596,000 | measured with a scripted model |

Peak demand against those, for the "3 per integrator per day" scenario: baseline peak 2.25 req/s is **0.99×** the paid tokens-per-minute ceiling (already at the edge) and 0.33× the local ceiling; **10× peak 22.5 req/s is 9.9× the paid token ceiling and 3.3× the local ceiling**. On the free key even the *smallest* scenario (9,000 requests/day) is **83×** the daily quota. So the order is: **provider tokens first** (per day on this key; per minute on a paid plan), **the single-process engine second**, everything else later.

**The quota is real, and it was hit.** In the latency run, the first 18 questions were answered; from the 19th (`medium_04`) the provider returned 429s and the engine answered 26 of the remaining 27 with cited excerpts and no model (the one success, `medium_10`, came 2 minutes after the first failure, consistent with older usage ageing out of a rolling window; I did not verify the window's rule). Token use in any 60-second window never exceeded about 5,000 of the 8,000 per minute (`evidence/phase10/latency_run/token_timeline.txt`), so it was the *daily* bucket. I first guessed the per-minute limiter charged the reserved `max_tokens`; two probes disproved that (a request with `max_tokens` 9,000, above the whole minute limit, succeeded; `evidence/phase10/provider_limits/`). Failure mode observed: **graceful**: 26 degraded responses in 203–904 ms, no exception, no crash, no retry storm (the client is built with `max_retries=0` and the engine does not retry a provider failure). User-visible effect: 58% of the requests in that window got excerpts instead of an answer.

## 4. The other items the brief lists

| topic | finding | evidence |
|---|---|---|
| provider quotas and rate limits | see §3. The key's real ceiling is tokens/day; the earlier-quoted 8,000/min and 1,000/day are not the binding ones | probes, 429 text |
| concurrent requests and connection pools | the model client is the OpenAI SDK with its default pool (1,000 connections, 100 keep-alive): not a limit at these rates. The tool path is **one MCP subprocess over one stdio session**; its `search_docs` is synchronous, so server work is effectively serial | `openai/_constants.py`; `mcp_client.py`; load test |
| local scaling under concurrency | throughput 5.4 (c=1) → 6.9 (c=2) → 6.8 (c=4) → **3.1 req/s (c=8)**; p95 288 → 420 → 993 → **5,063 ms**; 0 failures. Saturation at 2–4 workers, then contention makes it *worse*. Measured with an instant model on one machine, threads in one process, 40 requests per level, one fixed set of 36 questions: a rough ceiling, not a capacity plan | `load_results.json` |
| model-call latency | p50 761 ms, 85% of a request | `latency_analysis.json` |
| repeated routing calls | none exist: routing is rules, 0 model calls, 0.34 ms | `route` span |
| retrieval and reranker CPU/memory | today the query embedding (80 ms p50) dominates retrieval. **But `search()` fetches the whole filtered pool with documents on every query** (`retrieval.py:266`), even with hybrid off. Synthetic corpora, one version scope, hash embeddings, in-memory Chroma: **3 ms at 39 chunks, 26 ms at 1,000, 327 ms at 5,000, 1,208 ms at 20,000**, of which the pool fetch is 98% at 20,000. Invisible at 39 chunks; a hard wall for a real SDK corpus. The reranker is off (≈1.1 s p50 if on) | `scale_retrieval.json` |
| indexing and upload workloads | indexing is a local, offline job (`index rebuild`, minutes, no API cost); uploads are embedded locally, stored in SQLite, searched by brute-force numpy cosine over the owner's rows (≤ 25 × 500 chunks): milliseconds. CLI cold start ≈ 12–13 s per call (`phase4.md`) is the real upload cost. Ingestion shares CPU with queries (no separate worker) | `phase4.md`, `sqlite_store.py` |
| cache behaviour and invalidation | **there is no answer cache** (only the in-process embedding-model cache). Every repeated question pays a full model call. I have no traffic log, so the duplicate rate and the saving cannot be estimated | code |
| MCP availability and timeouts | one subprocess, 18–24 s cold start; per-call timeout 20 s; on any tool failure the graph serves the request from the in-process retriever (tested, `m01`–`m03`); **no automatic restart of a dead server was found in the client code**, so after a crash every request would take the fallback path until the process is restarted | `mcp_client.py` (grep for restart/reconnect: none) |
| queueing, backpressure, retry storms | **no queue and no rate limiter** in the engine: every request goes straight to the provider. A 429 degrades the request immediately and does not retry, so the engine cannot amplify an outage; a *caller* that retries can | latency run; `llm.py` |
| storage growth and log retention | **9.5 KB of spans per request** on average (45 golden requests, both span files summed per trace id: 8.9 KB for a single-version question, 10.7 KB for a comparison; the latency run's 45 requests, 26 of them quota-degraded, averaged 9.0 KB). **No rotation or retention** (G-OBS-5): 0.26 GB/day at 3 questions/integrator/day, 2.6 GB/day at 10× of that. `trace find` is a linear scan, 1.5 s per 30,000 requests (Phase 7, synthetic): about 14 s per day of logs at 10× of 3 | `latency_analysis.json`, `phase7.md` |

## 5. Mitigations, ordered by what the evidence supports

| # | mitigation | what the evidence says | cost / effort |
|---|---|---|---|
| 1 | **Move off the free key** (paid plan, or a second provider/key behind the existing `LLMClient` interface) | the only change that moves the first bottleneck. At $0.000388 per query: **$314/month at 27,000 requests/day, $3,143/month at 10×** (30 days, no cached-input discount, rates as published 2026-11-04) | money; the adapter boundary already exists |
| 2 | **A client-side token bucket with a queue and `Retry-After`** (the provider returned `retry-after: 44` s) | would turn 429-degradations into waits and give real backpressure; not built, not measured | small to moderate engineering |
| 3 | **Fewer prompt tokens** (prompt = 86% of tokens): fewer or shorter passages | direction is clear, **size is not measured**: the golden set is the harness to measure tokens vs correctness (`eval golden`); do not assume a percentage | measurement run, ≈ $0.02 |
| 4 | **Fix the whole-pool fetch** in `ChromaRetriever.search` when hybrid is off | measured: 98% of retrieval time at 20,000 chunks (1.2 s/query) | small code change with the version-filter tests as the safety net; **not done** (it is on the safety-critical path) |
| 5 | **More local workers** (one process saturates near 7 req/s; contention worsens it by 8 concurrent) | each process pair costs ≈ 745 MB (113 + 632); throughput of N processes and of the in-process (`--no-mcp`) path is **not measured** | memory |
| 6 | **Log rotation / retention** | storage grows at about 9.5 KB/request with no bound | small |
| 7 | **Answer cache** keyed on (normalised question, version, index version, prompt version), invalidated when the index or prompt changes | saving unknown without real traffic; correctness risk if the key omits any of those four | moderate |
| 8 | Groq's batch endpoint for *offline* evaluation (documented 50% discount) | applies to judge/eval runs only, not to user queries | none to adopt |

## 6. Limits of this analysis

* The workload is assumed; change `tenx_numbers.py` inputs and re-run.
* The load test used an **instant scripted model**: it measures the engine, not provider latency, quotas or a real model's variance. One machine, one MCP subprocess shared by all threads, 40 requests per level, p99 not reported.
* No comparison question reached the real model in the latency run, and comparisons are a third of the golden set and cost 49% more; with-model latency for them is unmeasured.
* Paid-plan figures are the provider's documentation, found by search, not tested on a paid key.
* Prices are published rates; no invoice was checked.
* Synthetic retrieval scaling used random text and hash embeddings: it shows how cost grows with pool size, not how recall behaves.
