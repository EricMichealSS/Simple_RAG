# Race table: single agent vs docs squad

Planned set: the same 10 cases (easy_01, easy_02, easy_13, medium_01, medium_07, medium_13, hard_01, hard_04, hard_06, hard_11). Headline numbers below use the 7 cases BOTH arms completed: ['easy_01', 'easy_02', 'hard_01', 'hard_04', 'hard_06', 'medium_01', 'medium_07']. same judge (gpt-oss-120b), same model for every role in both arms (gpt-oss-20b, temp 0, see protocol amendment A1), same ceilings, 1 repeat(s) (daily token quota stopped the rest). Headline = median of repeats; with 1 repeat there is no spread.

| Metric | Single agent (median) | Squad (median) | Single min-max | Squad min-max |
|---|---|---|---|---|
| Pass count (of 7 paired cases) | 7/7 | 7/7 | 7/7 - 7/7 | 7/7 - 7/7 |
| p50 latency (s) | 14.5 | 2.5 | 14.5 - 14.5 | 2.5 - 2.5 |
| p99 latency (s) | 35.8 | 3.2 | 35.8 - 35.8 | 3.2 - 3.2 |
| Total tokens (7 questions) | 21,118 | 25,129 | 21,118 - 21,118 | 25,129 - 25,129 |
| Cost per question | $0.00030 | $0.00040 | $0.00030 - $0.00030 | $0.00040 - $0.00040 |

**Excluded as invalid (runner artifact):** ['hard_11']. The single agent's own wall-clock check counted rate-limit waiting, so the run was cut off after <120 s of real work (see row in race_runs.json). As run, with it counted: single 7 vs squad 8 passes on the 8 paired cases; after exclusion see the table. Either way the keep/kill verdict is the same.

**Cases NOT in the headline (squad did not finish):** easy_13 (repeat 1, arms finished: ['single']), medium_13 (repeat 1, arms finished: ['single']). See PHASE4 doc: the squad's manager kept delegating on these negative cases, its context grew toward the provider's 8,000-token request cap, and the daily token quota ran out before they could finish. They are NOT counted as pass or fail.

p99 over 10 samples is the nearest-rank value, i.e. effectively the slowest single question. **Latency caveat, do not read as a squad win:** the single arm still had the SDK's own hidden retry sleeps inside its latency (not excluded by my clock), while the squad client had retries off; and workers run in-process (no network hop). Single latencies on short runs (e.g. 2,894 tokens in 19 s) show this. Latency is NOT used in the verdict.

## Per repeat

| Repeat | Arm | Pass | p50 s | p99 s | Tokens | $/question |
|---|---|---|---|---|---|---|
| 1 | single | 7/7 | 14.5 | 35.8 | 21,118 | $0.00030 |
| 1 | squad | 7/7 | 2.5 | 3.2 | 25,129 | $0.00040 |

## Per question (PASS count over repeats, tokens and latency are means)

| Case | Level | Single pass | Squad pass | Single tokens | Squad tokens | Single s | Squad s | Squad delegations |
|---|---|---|---|---|---|---|---|---|
| easy_01 | easy | 1/1 | 1/1 | 2,009 | 2,952 | 1.4 | 1.9 | 1.0 |
| easy_02 | easy | 1/1 | 1/1 | 3,039 | 4,353 | 1.8 | 2.8 | 1.0 |
| medium_01 | medium | 1/1 | 1/1 | 1,731 | 3,066 | 7.2 | 2.1 | 1.0 |
| medium_07 | medium | 1/1 | 1/1 | 2,894 | 4,185 | 19.3 | 2.7 | 1.0 |
| hard_01 | hard | 1/1 | 1/1 | 2,990 | 3,397 | 14.5 | 2.5 | 1.0 |
| hard_04 | hard | 1/1 | 1/1 | 3,413 | 3,556 | 21.0 | 2.5 | 1.0 |
| hard_06 | hard | 1/1 | 1/1 | 5,042 | 3,620 | 35.8 | 3.2 | 1.0 |

## Run statuses

- single: status=pass, budget_hit=None: 7 runs
- squad: status=pass, budget_hit=None: 7 runs

## Infrastructure errors (retried, logged)

- {'arm': 'squad', 'race_id': 'easy_13', 'repeat': 1, 'attempt': 1, 'error': "APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-20b` in organization `org_01m0f00hvbewfswqnwd39a7m42` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 8387, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"}
- {'arm': 'squad', 'race_id': 'easy_13', 'repeat': 1, 'attempt': 2, 'error': "APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-20b` in organization `org_01m0f00hvbewfswqnwd39a7m42` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 8482, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"}
- {'arm': 'squad', 'race_id': 'medium_13', 'repeat': 1, 'attempt': 1, 'error': "APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-20b` in organization `org_01m0f00hvbewfswqnwd39a7m42` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 8887, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"}
- {'arm': 'squad', 'race_id': 'medium_13', 'repeat': 1, 'attempt': 2, 'error': "APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-20b` in organization `org_01m0f00hvbewfswqnwd39a7m42` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 8878, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"}
