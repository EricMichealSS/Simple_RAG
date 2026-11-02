# Cost by Stage — the Incident Request

All numbers below are either **REAL** (copied from an actual historical trace already on
disk, or computed by re-running the real `agent.py` source against that real token data) or
**CALCULATED** (pricing arithmetic applied to real token counts, using the published rate
table in `agent_race/pricing.py`: `openai/gpt-oss-120b`, $0.15 / 1M input tokens, $0.60 / 1M
output tokens). Nothing below is invented. Source: `trace.json` (incident, pre-fix) and
`trajectory_results_after.json` race_id `medium_01` (fixed path, for comparison).

## Incident trace (pre-fix, `mitigated=False`) — what actually shipped the bad answer

| Stage | Calls | Input tokens | Output tokens | Cost (USD) | % of total |
|---|---|---|---|---|---|
| **Retrieval** (`search_docs`) | 1 | — (tool execution, no LLM call) | — | $0.00 | 0% |
| **Generation** (LLM calls) | 2 | 2,454 | 239 | $0.00051150 | 100% |
| **Tool executions** (`get_openapi_spec` / `check_deprecation`) | 0 | — | — | $0.00 | 0% |
| **Total** | 3 ops | 2,454 | 239 | **$0.000511** | 100% |

**Why Tool executions is $0.00 with 0 calls — and that absence is the bug.** The
authoritative tools (`check_deprecation`, `get_openapi_spec`) were never invoked. That is
not a cost optimization; it is the root cause (`phase5.md`). The request was *cheaper*
specifically because the step that would have caught the deprecated field was skipped.

## Fixed trace (post-fix, the new default) — for comparison

| Stage | Calls | Input tokens | Output tokens | Cost (USD) | % of total |
|---|---|---|---|---|---|
| **Retrieval** (`search_docs`) | 0 | — | — | $0.00 | 0% |
| **Generation** (LLM calls) | 3 | 3,038 | 286 | $0.00062730 | 100% |
| **Tool executions** (`check_deprecation`, `get_openapi_spec`) | 2 | — (tool execution, no LLM call) | — | $0.00 | 0% |
| **Total** | 5 ops | 3,038 | 286 | **$0.000627** | 100% |

Reconciliation: $0.00062730 calculated vs. $0.000627 recorded in
`trajectory_results_after.json` — the 0.00000030 difference is rounding in the historical
file's own `round(total_cost, 6)` step; both values round to the same $0.000627 at 6 decimal
places, so there is no double-counting between the two accounting methods.

## The real cost of being correct

| | Pre-fix (incident) | Post-fix | Delta |
|---|---|---|---|
| Total tokens | 2,693 | 3,324 | **+631 tokens (+23.4%)** |
| Total cost | $0.000511 | $0.000627 | **+$0.000116 (+22.7%)** |
| Tool calls made | 1 (`search_docs` only) | 2 (`check_deprecation` + `get_openapi_spec`) | +1 authoritative lookup |

This is the honest price of grounding the answer: calling the authoritative tool costs
roughly 23% more tokens on this question, because it adds one more full LLM round-trip to
read the tool's result and decide the final answer. At $0.000116 per incident-class
question, this is not a cost concern at any volume this project has actually measured (see
`tenx.md`) — but it is reported here, not hidden, per the brief's own instruction not to
optimize cost at the expense of correctness without measuring the trade-off.

## Why "Retrieval" shows $0.00 either way

`search_docs`, `get_openapi_spec`, and `check_deprecation` are all local Chroma
vector-store lookups plus a local `sentence-transformers` embedding call (`BAAI/bge-small-
en-v1.5`, run on CPU, no API key) — there is no per-query provider charge for retrieval in
this architecture. **The API cost is genuinely $0 for retrieval, but the compute cost is
not zero**: the embedding model and the vector index both consume local CPU/RAM per call.
This project has not instrumented or measured that compute cost in dollars (no cloud
hosting bill exists yet — this runs on a developer's own machine), so it is reported here as
a known gap rather than invented as zero cost that would also hold true in a hosted
deployment.
