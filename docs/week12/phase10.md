# Phase 10 — Cost, latency, load and 10× analysis

Date: 2026-11-04 · Status: **done, with the measurement problems stated** · Nothing committed, pushed or deployed; no tracked file modified.

## Objective

Per-question cost split by stage (routing, retrieval, tools, generation, with judge and embedding/indexing reported separately), latency p50/p95, a bounded local load test, and a 10× analysis that names a first bottleneck with evidence or labelled projections.

## What was done and what it found

| step | result | evidence |
|---|---|---|
| cost per question, all 45 golden questions | mean **$0.000388**, p95 $0.000695, max $0.000837; mean 1,836 tokens, 86% of them prompt | `evidence/phase9/golden_analysis.json` |
| cost by stage | only `generate` is billed; every other stage is local compute with no API charge | `cost_by_stage.md` |
| latency run: 45 questions, no pacing, one every 20 s | **broke at question 19**: the provider's *daily* token quota ran out (limit 200,000, used 198,646); **26 of 45 degraded** | `evidence/phase10/latency_run/`, `provider_limits/probe_4_…json` |
| latency with the model | n = 19 (single-version only): request p50 **950 ms**, p95 2,021 ms (= the slowest); generate p50 761 ms (85%); everything else p50 135 ms | `latency_analysis.json` |
| engine-only latency | n = 26 degraded: p50 294 ms, p95 879 ms | `latency_analysis.json` |
| the 26 degraded requests | all handled cleanly, 203–904 ms, no exception | `golden_results.json` (latency run) |
| bounded local load test (scripted instant model, real MCP server and embeddings) | 160 requests, 0 failures; 5.4 / 6.9 / 6.8 / **3.1 req/s** at concurrency 1/2/4/8; p95 288 / 420 / 993 / **5,063 ms** | `load_results.json`, `load_console.txt` |
| memory | engine ≈ 113 MB; MCP server ≈ **632 MB** | `memory_probe.json` |
| retrieval scaling (synthetic) | 3 / 26 / 327 / **1,208 ms** per query at 39 / 1,000 / 5,000 / 20,000 chunks; the whole-pool fetch is 98% of it at 20,000 | `scale_retrieval.json` |
| real query embedding | p50 80 ms (n = 45); model load 18.6 s | `scale_retrieval.json` |
| run-to-run stability | same outcome and judge verdict 19/19; identical answer text only 11/19 at temperature 0 | `latency_analysis.json` |
| 10× analysis | first bottleneck: provider token quota (≈109 queries/day on this key; ≈2.3 req/s on a paid plan *if* the published limits hold); engine second (≈7 req/s per process) | `tenx.md` |
| `max_tokens` hypothesis | I guessed the per-minute limiter charges the reserved `max_tokens`; **disproved** by two probes (a request with `max_tokens` 9,000 > the 8,000/min limit succeeded) | `provider_limits/` |

### Corrections made during the phase (so they are visible, not silent)

1. I first wrote the span volume as 21.4 KB per request: that divided both trace files (45 requests) by 19. Recomputed per trace id: **9.5 KB** (8.9 single-version, 10.7 comparison). `analyze_latency.py` was fixed; `tenx.md` and `tenx_numbers.txt` regenerated.
2. The taxonomy said Mode 1 (generation unavailable) was "0/45, not exercised live". The latency run exercised it by accident (26/45); `error_taxonomy.md` was updated, with the two denominators kept separate.
3. The earlier-quoted provider limits (8,000 tokens/minute, 1,000 requests/day) are real but are **not** the binding ones; the daily token limit is.

## Files inspected

`sdk_engine/pricing.py`, `llm.py`, `retrieval.py` (the pool fetch at line 266), `mcp_client.py` (no restart logic; calls run as concurrent tasks over one session), `tracequery.stage_costs`, `sdk_eval/golden.py` and `loadtest.py`, the OpenAI SDK's default connection limits, Groq's documentation pages (via search).

## Files created or modified

New: `docs/week12/cost_by_stage.md`, `tenx.md`, `phase10.md`, `evidence/phase10/` (latency run and traces, analyses, load results, scaling and memory probes, provider-limit probes with the organisation id redacted, scripts, `tenx_numbers.py`). Code changed in this phase window (from the README review, recorded in `phase11.md`): `toolbox.py` (top_k clamp), `indexing.py`, `cli.py`.

## Commands run and actual results

| command | result |
|---|---|
| `python -m sdk_engine eval golden --between 20 --out <dir>` | 45 rows, exit 0; 19 with a model call, 26 degraded (`RateLimitError`) |
| `python -m sdk_engine eval load --levels 1,2,4,8 --requests 40` | exit 0, 0 failures (table above) |
| `python scale_retrieval.py` / `memory_probe.py` | as tabled |
| `probe_limits.py`, `probe_reserve.py`, `probe_reserve2.py`, `probe_daily.py` | headers 8,000 tokens/min and 1,000 requests/day; `max_tokens` not reserved; the daily-quota 429 text |

## Tests

No test was added for measurement scripts. The suite was re-run at the end of Phase 11 (`phase11.md`).

## Security and privacy

The probes sent trivial and filler prompts only (no user data). The provider's error text contains an organisation identifier; it is redacted in the committed evidence (`org_<redacted>`), and no key is printed by any script. The latency run sent the 45 golden questions again (public documentation questions).

## Limitations (also in `tenx.md` §6)

The workload is **assumed** (the brief gives 9,000 integrators and no usage rate); the load test used an instant scripted model on one machine; with-model latency covers only 19 single-version questions, so p95 is the single slowest request and there is nothing for comparisons; the 26 degraded requests were timed while I ran other jobs on the machine; paid-plan limits are the provider's documentation, not tested; prices are published rates, not invoices; synthetic retrieval scaling says nothing about recall.

## Acceptance and next steps

Cost per query and per stage documented honestly: **met**. p50/p95 reported with n and without p99: **met, with a thin sample**. Bounded local load test with limitations: **met**. 10× analysis names a bottleneck with measured evidence (quota hit in this session) and labelled projections: **met**. Next: Phase 11 (packaging, README, CI, clean-checkout run, demo, final documents).
