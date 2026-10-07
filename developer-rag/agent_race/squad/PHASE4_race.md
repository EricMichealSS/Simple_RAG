# Phase 4: The race

**Done:** 2026-10-05. Code: `run_squad_race.py`. Output: `race_table.md`, `race_runs.json`, `traces.jsonl`.

## What was run
Both arms, same questions, same ceilings, interleaved per question. Result: ONE complete repeat on gpt-oss-20b (all roles, both arms), judge gpt-oss-120b. The planned 3 repeats were not possible.

## What went wrong on the way (reported, not hidden)
1. **Attempt 1 void** (`void_attempt1_quota/`): the free tier caps gpt-oss-120b at 200k tokens/day (+8k/min). 29 of 60 runs got HTTP 429. Separately, a bug in MY squad tool schema (optional enum, model sent `endpoint: null`) caused 2 HTTP 400s. Fixed, archived, no conclusion drawn.
2. **Re-run on gpt-oss-20b** (amendment A1 in protocol.md): same model for every role and arm; judge on 120b.
3. **My wait-time parser bug:** "75ms" was read as 75 minutes and stopped the runner. Fixed; resumable.
4. **413 request-too-large** (amendment A2): on the negative cases the squad manager's context grew to ~7.4k tokens and crossed the provider's 8,000-token request cap. Deterministic, so recorded as an outcome, not retried.
5. **Daily quota ran out again** on 20b. The squad's `easy_13` and `medium_13` runs never finished (partial traces: 9 calls, ~28k tokens each, max prompt ~7.4k). They are NOT counted as pass or fail.
6. **Runner artifact:** the unmodified agent.py measures its 120 s ceiling on raw time, including my rate-limit waits. Single `hard_11` was cut off after 2.4 s of work. Excluded by an objective rule (wall-clock budget hit but active time < 120 s). As run it would be single 7/8 vs squad 8/8; excluded 7/7 vs 7/7.
7. **Latency confound:** the single arm's SDK still had hidden retry sleeps inside its measured latency; the squad client had retries off. Latency is not used in the verdict.

## Result (7 paired cases, 1 repeat)
Pass 7/7 vs 7/7. Tokens 21,118 (single) vs 25,129 (squad). Cost/question $0.00030 vs $0.00040. Full table in `race_table.md`.

## Edge cases checked
Token reconciliation asserted (hand-off tokens = arm totals); traces from failed attempts excluded by trace_id; runs resume without repeating finished ones.
