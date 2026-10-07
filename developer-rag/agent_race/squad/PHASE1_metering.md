# Phase 1: Metering harness

**Done:** 2026-10-04. Code: `metering.py`.

## What was built
- `Tracer`: one per (arm, question, repeat) run, with a `trace_id`. `metered_chat()` is the only way the squad calls the model, and it writes one JSON line per call to `traces.jsonl`: trace_id, arm, caller, hand-off label, prompt tokens, completion tokens, cost, latency, status, provider retries.
- Same sampling in every role: gpt-oss-120b, temperature 0, max_tokens 1024.
- The single agent is NOT modified. Its metering is rebuilt from the `llm_call` entries `run_agent` already returns (`trace_from_single`); per-call latency is unknown there (stored as null), tokens are exact. The race runner asserts that those trace tokens equal the agent's own `total_tokens`.
- Provider hiccups (429 / connection / 5xx from the model host) are retried up to 4 times inside `metered_chat` and counted in `provider_retries`. They are infrastructure noise, kept apart from pattern failures.

## Edge cases
- Failed provider call after all retries: a row with status `provider_error:*` is recorded, then the error is raised; the race runner retries the whole run once and logs it.
- Traces of failed attempts are excluded from analysis by trace_id.
- Hand-off tokens must sum exactly to each arm's total; `analyze.py` asserts this.
