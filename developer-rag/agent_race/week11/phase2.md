# Phase 2 — Implement Logging, Tracing, and Error Handling

**Status: COMPLETED (scoped — see "What was not done" below)**

## Objective

Implement the Phase 1 schema in `agent.py` itself, additively, with zero risk to existing
callers.

## Starting state

`agent.py`'s `run_agent()` loop (`agent_race/agent.py`, pre-Phase-2) returned a dict with
`status, budget_hit, answer, iterations, total_tokens, total_cost_usd, wall_clock_s, log` and
appended `llm_call`/`tool_call`/`budget_exceeded` events to `log` with the field set
described in `phase1.md`.

## Files modified

**`agent_race/agent.py`** (no files created in this phase; Phase 1's schema is implemented
as additive edits to the one existing file that owns this data):
1. Added `from datetime import datetime, timezone` import.
2. Added `PROMPT_VERSION = "agent-v2-mitigated-default"` module-level constant, with a
   docstring explaining why it exists and when to bump it again.
3. `llm_call` log entries: added `"timestamp"` (UTC ISO-8601) and `"cost_usd"` (per-call,
   via the already-imported `cost_usd()` — previously only `cumulative_cost_usd` existed).
4. `tool_call` log entries: added `"timestamp"` and `"result_full"` (the real, untruncated
   tool return value) alongside the existing `"result_preview"` — nothing removed.
5. `finish()`'s returned dict: added `"prompt_version"`, `"mitigated"`, `"parallel_hint"`,
   `"compact_history"`, `"model"` — all new keys, none of the original keys renamed or
   removed.

Every change above is **additive**: new dict keys, a new module constant, nothing deleted or
renamed. (The one NON-additive change in this file — flipping `run_agent()`'s three default
argument values — is Phase 5's bug fix, not Phase 2's observability work, and is documented
separately there.)

## How this connects to other files

- `week11/extract_trace.py` reads the new `timestamp`, `cost_usd`, and `result_full` keys
  directly to build `trace.json` (Phase 4).
- `week11/test_regression_w11.py` reads the new `prompt_version`/`mitigated` keys to prove
  which behavior actually produced a given answer (Phase 5).
- `trajectory_eval.py`'s own log-parsing (`_extract_trajectory()`) only ever reads
  `entry["tool"]`/`entry["args"]` from `tool_call` events — confirmed by direct inspection —
  so it is unaffected by the two new keys added to those same events.

## Request/data flow

```
run_agent(question)
  -> messages = [system_prompt, user_question]
  -> loop:
       _client.chat.completions.create(...)          [generation]
       log.append({event: llm_call, timestamp (NEW), cost_usd (NEW), ...})
       if tool_calls:
         TOOL_IMPLS[name](**args)                     [retrieval / tool]
         log.append({event: tool_call, timestamp (NEW), result_full (NEW), ...})
  -> finish() returns {..., prompt_version (NEW), mitigated (NEW), model (NEW), log}
```

## Tests executed and actual results

`week11/test_regression_w11.py` exercises this exact code path end-to-end (see `phase5.md`
for the full RED/GREEN record) — the new `timestamp`/`cost_usd`/`result_full`/
`prompt_version` keys are all observed populated with real values in both the RED and GREEN
runs (`test_results/red.txt`, `green.txt`), and `week11/extract_trace.py`'s successful run
(Phase 4) is itself a test that every new field is actually reachable from a real
`run_agent()` call, not just declared in a schema document.

## Failures encountered and how they were handled

1. `ModuleNotFoundError: No module named 'openai'` on first import attempt — resolved by
   building `week11/fakes.py` (Phase 0's identified blocker, addressed here because Phase 2's
   own verification required a successful import).
2. `ModuleNotFoundError: No module named 'dotenv'` on the next attempt — same fix, extended
   `fakes.py` with a no-op `load_dotenv`.
3. No other failures — `py_compile` on `agent.py` passed on the first attempt after each edit.

## Security and privacy implications

No new secret-bearing field was added. `result_full` (the new, untruncated tool-call capture)
could in a real deployment contain more retrieved document text than the old 200-char
preview — this is documentation/API-reference content in this track, not personal data, but
is flagged here as the kind of field a real production redaction policy would need to review
before this pattern is reused against a corpus containing user data.

## Acceptance criteria — verified

- [x] A real `run_agent()` call produces a complete, schema-matching log (proven in Phase 4).
- [x] Errors (`budget_exceeded`) are represented honestly — untouched, still present.
- [x] Context IDs are recoverable from the log (proven in Phase 4's `trace.json`).
- [x] All changes are additive; no existing caller's code needed to change because of this
      phase (only Phase 5's default-value fix required one companion edit, to
      `trajectory_eval.py`, documented separately there).

## What was NOT done in this phase (scope note)

A `trace_id` is not generated inside `run_agent()` itself for every ordinary call — only
`week11/extract_trace.py`'s wrapper generates one today. Wiring `uuid.uuid4()` into
`finish()` directly is the natural next step (named in `final_review.md`) and was deferred
here specifically to keep this phase's `agent.py` edit as small and reviewable as possible.
