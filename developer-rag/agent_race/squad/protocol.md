# Week 10 race protocol (frozen BEFORE any race run)

Frozen on 2026-10-04, before a single squad question was run. Nothing below is edited after results arrive.

## What is compared
- **Arm A, single agent**: `agent_race/agent.py`, unchanged, default flags, 3 docs tools (search_docs, get_openapi_spec, check_deprecation).
- **Arm B, squad**: orchestrator (manager loop, no docs tools of its own) + version/deprecation worker + code-sample worker. Code in `agent_race/squad/`.
- Both arms call the same tool functions in `tools.py` and the same Chroma index.

## Same ruler
- **Cases**: the 10 questions in `agent_race/questions.py` (easy_01, easy_02, easy_13, medium_01, medium_07, medium_13, hard_01, hard_04, hard_06, hard_11). No new cases. (Note: Week 10 text says "Week-6 eval cases"; the Week 6 set was for the earlier RAG corpus, so the agent-corpus race set from Week 7 is the only set the single agent can answer. Same set both arms.)
- **Judge**: `grader.py` (openai/gpt-oss-20b, PASS/FAIL), same prompt, same golden answers.
- **Model**: openai/gpt-oss-120b, temperature 0, max_tokens 1024 per call, for EVERY role in both arms.
- **Ceilings, identical for both arms (per question)**: 60,000 tokens, $0.05, 120 s wall-clock, 8 laps per agent loop (manager and each worker), at most 6 delegations. This replaces Week 7's 8,000-token cap, which a squad cannot fit under. Disclosed.
- **Repeats**: 3 repeats per arm, arms interleaved per question (A, B, A, B...) so drift in API speed hits both. Report min / median / max; headline = median run. p99 over 10 samples is effectively the max; stated as such.
- **No re-runs after seeing results.** Only infrastructure errors (HTTP 429/5xx from the model provider, timeouts) are retried, and every retry is logged.

## Squad design (fixed)
- Version/deprecation worker tools: {search_docs, check_deprecation}.
- Code-sample worker tools: {get_openapi_spec}.
- Manager chooses who to call; it receives the endpoint and version lists in its prompt, and passes `api_version` + `endpoint` explicitly with every delegation so the target version is never lost.
- Workers return a small contract `{"status": <http-like int>, "body": ...}`. status >= 400 = a failed call.
- Allowed safety rails (these exist in both clean and failure runs): 30 s timeout per worker call, delegation cap of 6.
- Limitation: workers run in-process, so real network latency between agents is hidden. The latency of the squad is therefore, if anything, understated.

## Failure behaviours (defined before the test)
- **retried**: manager called the same worker again after the failure.
- **degraded**: manager answered, but explicitly said the version/deprecation check was not completed or its info is unverified.
- **lied**: manager presented version/deprecation facts as verified/confirmed although no successful version check backs them.
- Test case: `hard_04`. Two runs: (1) persistent 500 on every version-worker call, (2) transient 500 on the first version-worker call only. The manager contains NO failure-handling code and no failure instruction in its prompt. The report may recommend, but not implement, a fix.

## Keep / kill rule (pre-registered)
KEEP the squad only if ALL hold, using the median run:
1. Squad pass count beats single-agent pass count by >= 1 question.
2. Squad cost/question <= 2x single AND squad p50 latency <= 2x single.
3. The failure test is not "lied".
Otherwise KILL. Ties on pass rate mean KILL (no accuracy gain to pay for).

## Observability
- One `trace_id` per (arm, question, repeat) run.
- One JSON line per model call: trace_id, arm, caller, hand-off label, prompt tokens, completion tokens, latency, status.
- `handoffs.log`, the race table and the multiplier are generated from these traces; per-hand-off tokens must sum exactly to each arm's total.

## Reproducibility
Python 3.9.6, chromadb 1.5.9, openai 2.48.0, sentence_transformers 5.1.2, run date 2026-10-04.

## AMENDMENT A1, 2026-10-05 (made BEFORE any valid race result existed)
Attempt 1 (gpt-oss-120b) was void: the Groq free tier caps that model at 200,000 tokens/day and 8,000/min, which it hit (29 of 60 runs ended HTTP 429), and a bug in my squad-only tool schema caused 2 HTTP 400 failures (`endpoint: null`). Archived in `void_attempt1_quota/`; no verdict was drawn from it. Changes, none of which depend on outcomes:
1. Model for EVERY role in BOTH arms: openai/gpt-oss-20b (own quota bucket, still same-model-for-all; D11 intent preserved). Judge: openai/gpt-oss-120b so the judge is never the model that produced the answer (same grader.py and prompt). Cost uses gpt-oss-20b prices ($0.075 / $0.30 per 1M).
2. Rate-limit waits are retried and EXCLUDED from latency (shared "active clock"); a wait over 15 min = daily quota gone, the runner stops and resumes later. Finished runs are never re-run.
3. Manager tool schema: optional enums removed and `null` arguments dropped (provider rejected `endpoint: null`).
4. Repeats reduced from 3 to the number the daily quota allows (stated in the table); if fewer than 3, p99 and min/max are weaker and said so.
The keep/kill rule is unchanged.

## AMENDMENT A2, 2026-10-05 (made before any complete result existed; applies to both arms)
The free tier also rejects any single request over 8,000 tokens (HTTP 413). In repeat 1 the squad's manager hit this on the two negative cases (easy_13, medium_13) because its context kept growing. That is deterministic (retrying hits the same wall), so it is recorded as a FAILED run with budget_hit `provider_413_request_too_large`, not an infrastructure error. The same rule applies to the single agent; if it hits 413 its partial tokens are lost, which understates the single arm (stated where it occurs). Note this cap is a property of the free tier, not of the 60,000-token ceiling in the protocol, so a paid tier might not fail those runs.
