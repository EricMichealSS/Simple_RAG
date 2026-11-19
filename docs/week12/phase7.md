# Phase 7 — Unified observability and incident response

Date: 2026-11-04 (spans are UTC; the live runs happened on the evening of 2026-11-03 UTC) · **Status: COMPLETE for its scope, with the limits below.** One trace id now runs from the CLI entry through the graph, retriever, model call, MCP client and the MCP server's own process; a request can be found by trace id, time range, version, endpoint, tool, status, code finding, security flag, cost or latency; a complete real trace and its redaction were demonstrated and scanned; an incident drill was run twice, blind, on a **synthetic** log (see `incident_drill.md`: it did not single out one request, and says why).

## The brief's requirements
| Requirement | Status |
|---|---|
| One trace id across entry point, graph nodes, retriever, model calls, specialist agents, MCP client, MCP server | **Done** and tested across the real process boundary. *Specialist agents:* there are none as separate agents; the "specialists" are labelled roles inside the one graph and share its trace (a deliberate Phase 3 decision) |
| Per-span data: ids, parent, time, operation, duration, status; model/provider and **prompt version**; tokens and cost; chunk ids; SDK version and retrieval scope; selected tool and route; retries and error category; code-validation outcome; redaction and security flags | **Done.** Audit below; `provider` and `prompt_version` were missing and are added |
| Cost from real usage and documented pricing; missing or projected values marked; local work may have zero API charge but still costs compute; never fabricate | **Done.** `calculated` / `unavailable` / `none` on every model call; the stage table labels each row's basis; unpriced is never shown as 0 |
| Find a request by trace id, time range, version, tool, status, or cost outlier | **Done**: `python -m sdk_engine trace find` / `show` / `stats` |
| One complete trace from input through the MCP hop to the final answer, redacted before persistence | **Done**: live, below; every persisted file scanned |
| Incident drill with the actual time to locate and inspect a trace | **Done twice, blind, synthetic log**: 65.3 s and 37.1 s self-timed (`incident_drill.md`), with its caveats |

## Audit of the per-span data (37 real traces from my live runs, before any change)
| Field | Before | After |
|---|---|---|
| trace/span/parent ids, timestamps, duration, status, error category | ✓ | ✓ |
| model, tokens, cost + `cost_status` | ✓ on `generate` | ✓ |
| **provider, prompt version** | ✗ | ✓ on every `generate` span (`prompt_version` = `sdk-answer-v1`, the **first** label: earlier prompts were unversioned, so older logs cannot be attributed) |
| chunk ids, SDK version, retrieval scope | ✓ | ✓ |
| selected tool | ✓ `mcp.call` | ✓ |
| per-request retry totals, version, tools, cost, code findings | ✗ (only per-span) | ✓ **request summary on the root span** |
| code-validation outcome | ✓ (Phase 6 span) | ✓ + tiers, deprecated-warned and unchecked counts + reason codes in the summary |
| redaction and security flags | ✓ | ✓ |

## The request summary (the index) — and the privacy decision behind it
The root `request` span carries `summary_version: 1` and: `corpus, sdk_version, product, comparison_versions, scope_versions, endpoints, retrieval_via, tools_called, tool_failures, mcp_fallbacks, model, provider, prompt_version, llm_calls, prompt_tokens, completion_tokens, cost_usd, cost_status, retrieval_attempts, generation_attempts, code_blocks, code_tiers, code_hard, code_soft, code_deprecated, code_unchecked, code_unchecked_reasons, reason_category` (+ the existing `outcome`, `security_events`). Every value is an id, a count, a label or a number; `reason` is reduced to its category.

**Neither the question nor the answer is ever persisted** (tested: a distinctive question and answer string do not appear in the log). That is a privacy property worth keeping, and its cost is stated plainly: Week 11's way of finding an incident (grep the answers) is no longer possible, and requests that look identical in every field cannot be told apart from the logs. These structured fields are the index, and `endpoints` (a catalog id, not text) was added specifically so an incident about "creating an issue" can be narrowed.

## The query tool (`sdk_engine/tracequery.py`, CLI `python -m sdk_engine trace …`)
* `find` — filters: `--since/--until` (`7d`, `12h`, ISO; `--as-of` makes a relative query exactly re-runnable), `--version`, `--endpoint`, `--tool`, `--status`, `--corpus`, `--code-tier`, `--has-code`, `--unchecked`, `--fallback`, `--security`, `--min-cost`, `--min-ms`; `--costliest N`, `--slowest N`, `--outliers` (cost > 3× the median priced request, **refused below 10 priced requests** with that reason printed); `--json`. Scans only root spans (one substring test skips every other line before parsing).
* `show TRACE_ID|PREFIX` — merges the engine's log and the MCP server's log into one tree (a server span hangs under the client `mcp.call` that caused it), then a **per-stage table**: model calls = `calculated: real token usage × published rate`, an unpriced model = `unavailable`, the root = `not additive`, everything else = `no API charge (local compute)`; durations are inclusive (nested stages overlap).
* `stats [--bucket hour|day]` — counts, nearest-rank p50/p95 latency and cost (priced requests only; unpriced counted separately), tool/tier/version counts, and a per-bucket timeline in which an outage appears as a cluster.
* Output passes through the redactor again, the log is never rewritten, unreadable lines are counted, a root written before the summary existed is listed as `legacy` and matches no summary filter.

## One complete real trace (live; `evidence/phase7/sample_trace.txt` / `.json`)
The documented CLI entry point, the real MCP server, the real model. The question deliberately contained a **fake** token (`ghp_…`, assembled at run time) and an `example.com` address:
```
request [sdk_engine] 5038.8 ms  outcome=answered sdk_version=2022-11-28 corpus=official retrieval_via=mcp tools_called=[search_docs, lookup_changes] llm_calls=1 cost_usd=0.00056145 cost_status=calculated
  validate_request [sdk_engine] 0.3 ms  question_chars=187 redactions={"provider-key":1,"email":1}
  select_corpus [sdk_engine] 0.0 ms  corpus=official
  resolve_version [sdk_engine] 4.3 ms  status=resolved sdk_version=2022-11-28 source=question
  route [sdk_engine] 0.4 ms  intent=code_help specialists=[documentation_researcher, deprecation_migration, code_validation]
  retrieve [sdk_engine] 438.3 ms  attempt=1 via=mcp returned=5 scope_versions=[2022-11-28]
    mcp.call [sdk_engine] 433.6 ms  tool=search_docs ok=True latency_ms=433.06
      mcp.tool.search_docs [mcp_server] 422.0 ms  requested_version=2022-11-28 returned=5
  changelog [sdk_engine] 13.4 ms  via=mcp found=1 kept=1
    mcp.call [sdk_engine] 11.6 ms  tool=lookup_changes ok=True
      mcp.tool.lookup_changes [mcp_server] 2.4 ms  requested_version=2022-11-28 returned=1
  assess_sufficiency [sdk_engine] 0.4 ms  sufficient=True
  generate [sdk_engine] 4548.0 ms  attempt=1 model=openai/gpt-oss-120b provider=groq prompt_version=sdk-answer-v1 prompt_tokens=1639 completion_tokens=526 cost_usd=0.00056145 cost_status=calculated
  verify_citations_and_version [sdk_engine] 5.0 ms  ok=True security_failures=0 code_failures=0
    code_validation [sdk_engine] 3.1 ms  blocks=1 languages=[shell] hard=0 soft=0 tiers=[cited] symbol_source=none lookups=0
  finalize [sdk_engine] 0.2 ms  outcome=answered citations=1
API cost of this request: $0.000561   (trace 17a2fafb064d4d7b9cb9470c04867f7e; 16 spans, 2 services; wall 23.1 s including a cold process start)
```
**What was persisted** (scan of every file in that trace directory — `spans.jsonl` 8,394 B, `server_spans.jsonl` 1,310 B, `mcp_sdk-docs_stderr.log` 2,132 B): the real API key, the fake token, the email address and the question text are present in **none** of them, and neither in the saved evidence files; `find_secrets` finds 0 secret-shaped strings. What is recorded is the count: `redactions={"provider-key":1,"email":1}`.

## Defects found during this phase
1. **The live demo's first run was blocked by my own Phase 5 safety check.** The answer wrote `Authorization: Bearer <the token the user had pasted>`; `find_secrets` returns the whole match `Bearer ghp_…`, which is not a substring of "my token is ghp_…", so a correct answer was withheld as a secret "not in the sources". Reproduced offline, failing test first; the token part is now what must be supported (an *invented* bearer token is still refused; `safety_checks_off` still fails 47 tests). The blocked run is itself a trace (`341a7dba…`, `reason_category=blocked_by_safety_check`) and is a fair example of why the trace is worth having.
2. **A guard test could not fail** (`no_provider_or_prompt` passed all tests): it compared the span's prompt version with `answering.PROMPT_VERSION`, which the mutation also changed. It now asserts the label's shape as well. The mutation run found it.
3. **The answer footer was silent about unchecked identifiers** (found by reading the code against the drill's feedback): an identifier that could not be checked looked exactly like one that passed. The footer now says "N identifier(s) could NOT be checked (reason)" and the finding carries a machine-readable `reason_code`.
4. Smaller: my first e2e depth expectations were off by one level (`mcp.call` hangs under the `retrieve`/`changelog` node span); two tests compared 12-character prefixes of synthetic ids that differ only in the last character.

## Scale of the query (measured; **synthetic** spans, warm file cache, best of 3, this Windows machine; `evidence/phase7/measure_scale.py`)
| requests | client spans | log size | `find` (roots only) | parse every line | `show` one trace |
|---|---|---|---|---|---|
| 1,000 | 12,804 | 7.2 MB | 0.022 s | 0.057 s | 0.012 s |
| 10,000 | 128,884 | 73.0 MB | 0.229 s | 1.607 s | 0.219 s |
| 30,000 | 386,513 | 218.8 MB | 1.514 s | 5.971 s | 1.673 s |

The roots-only scan is 2.6× / 7× / 3.9× faster than parsing everything. A linear scan is comfortable to ~10⁴ requests and still about 1.5 s at 3×10⁴; beyond that it needs an index, a daily-rotated log or a database. The growth from 10⁴ to 3×10⁴ is steeper than linear (6.6× for 3× the data) and I have not investigated why (file-cache effects are a candidate). **There is no log rotation or retention policy**; today's real log is 37 traces, so this is a 10×-analysis input (Phase 10), not a present problem. Cold-cache reads are slower than these.

## Mutation checks (throwaway pytest plugins in `evidence/phase7/`)
| Mutation | Result |
|---|---|
| engine stops writing the request summary | 6 failed |
| summary forgets the tools called | 1 failed (thin) |
| `provider` / `prompt_version` not recorded | 2 failed (0 before defect 2 was fixed) |
| unpriced model shown as $0 | 1 failed (thin) |
| `show` prints the log without redacting | 1 failed (thin) |
| `show` reads only the client log (server spans not joined) | 2 failed |
| time-range filter ignored | 1 failed (thin) |
| outlier detection without its minimum-count guard | 3 failed |
| footer silent on unchecked identifiers | 2 failed |
| unchecked findings lose their reason code | 2 failed |
| `--fallback` filter ignored | 1 failed (thin) |
| `--as-of` ignored | 1 failed (thin) |
| timeline stops counting fallbacks/unchecked | 1 failed (thin) |

## Commands run and actual results
* `pytest -q` (no API key in the environment): **615 passed** in 93 s. Progression this session: 578 (end of Phase 6) → 615 (+37). Per file: `test_tracequery` 34 (new), `test_codecheck` 46, `test_output_safety` 36, `test_mcp_graph_e2e` 5. No tracked file is modified (`git diff --name-status` empty). The one-off `test_uploads` failure recorded in Phase 6 has not recurred in the five full runs since (509, 570, 577, 578, 615 passed); its cause is still unknown.
* Live: one blocked request and one answered request through the documented CLI ($0.000515 and $0.000561); evidence scanned: 0 key-shaped strings.
* GitNexus: the new/changed symbols (`request_summary`, `route`, `verify`, …) are in untracked files, so `risk: UNKNOWN` as before; callers confirmed by text search; every schema change is an additive optional field. `detect_changes` has **not** been run: nothing is being committed.

## Known weaknesses (open)
1. **The drill did not pin a single request** and cannot, by design (no answer text); n = 2, AI investigators, synthetic log, a constructed scenario, improvements tuned to run 1 (all in `incident_drill.md`).
2. **No log rotation or retention; linear-scan queries**; concurrent appends from several processes to one log file are untested (each writer holds a lock, but only within a process).
3. `trace show`'s stage table says "no API charge (local compute)" even on a tool call that timed out (run 2's investigator called it noise); and mixed-version logs show pre-Phase-7 requests as `legacy` with most filters unavailable.
4. The prompt label `sdk-answer-v1` is the first; nothing before it can be attributed, and nothing enforces bumping it when the prompt changes.
5. Only LLM cost is an API cost; embedding and the MCP subprocess are timed, not costed. Cost per stage for the 10× analysis is Phase 10's work.
6. **Pasted secrets and PII in a question are still sent to the model provider** (they are redacted in the logs, not in the prompt). Redacting secrets before the prompt would also remove the echo problem of defect 1. That changes what the model sees, so it is **your decision**, not something I did.
7. An unchecked-code answer is still `outcome=answered` (see `incident_drill.md`, Open).

## Reproduce
```
cd developer-rag
..\.venv\Scripts\python -m pytest -q                                   # no key needed
..\.venv\Scripts\python -m sdk_engine trace find --since 7d            # then:  trace show <id-prefix>   |   trace stats --bucket day
..\.venv\Scripts\python ..\docs\week12\evidence\phase7\live_trace.py   # needs GROQ_API_KEY in developer-rag\.env; ~$0.0006
..\.venv\Scripts\python ..\docs\week12\evidence\phase7\measure_scale.py <scratch_dir>
```
The evidence scripts contain machine-specific absolute paths. Mutation plugins: `MUTATION=<name> PYTHONPATH=<dir> pytest -p mutate10_trace …` (and `mutate11_drill`).
