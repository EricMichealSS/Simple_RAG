# 10x Traffic Analysis

## One-line answer

**API rate limits break first — and they already have, at roughly today's volume, not a
hypothetical 10x.** This is not a projection; it is a fact already recorded in this repo.

## The real evidence (not a projection)

From `agent_race/squad/protocol.md`, Amendment A1 (written 2026-10-05, **before** any valid
race result existed, so this is not a result-justifying excuse):

> Attempt 1 (gpt-oss-120b) was void: the Groq free tier caps that model at **200,000
> tokens/day and 8,000/min**, which it hit (**29 of 60 runs ended HTTP 429**).

60 runs = 10 questions × 3 repeats × 2 arms (single agent + squad) for ONE race session —
i.e., **48% of all calls failed on rate limits at the smallest possible experimental
volume this project has ever run.** Amendment A2, same document: the free tier also hard-
rejects any single request over 8,000 tokens with HTTP 413, which the squad's manager hit
on two separate questions in repeat 1 alone, purely from context growth, with no volume
increase at all.

Separately, `agent_race/taxonomy.md` (Week 5, 20 sampled real traces from the live RAG app,
a different system on the same Groq account) found:

> Mode 1 — Generation API error (4/20, 20%, HIGH): Groq API returns 413 or TPM error...

Two independent systems, two independent measurement efforts, both already hitting
provider rate/size limits at normal low-volume usage — not at projected scale.

## Current measured volume and cost (for comparison, so the conclusion isn't "it felt slow")

| | Single agent | Squad |
|---|---|---|
| Questions per race | 10 | 10 |
| Mean cost/question | $0.000938 | n/a (killed, see Week 10 verdict) |
| p50 latency | 25.92 s | n/a |
| Max observed latency | 75.84 s | n/a |
| Total race cost (10 questions) | $0.009378 | — |

Source: `race_results.json`, `race_summary.json` (real, recorded 2026-10 race run).

## Projected cost at 10x volume (a projection, labeled as such)

```
projected cost at 10x = 10 x Q x C
Q (current volume)     = 10 questions per race
C (mean cost/question) = $0.000938 (single agent, the arm actually in production per the Week 10 verdict)
projected 10x cost     = 10 x 10 x $0.000938 = $0.0938 per 10x race (100 questions)
```

**Cost at 10x is under 10 cents.** Cost is not a plausible first bottleneck at any volume
this project is positioned to reach before rate limits do — the gap between "$0.09" and
"the free tier's $0 budget running out via request/token caps instead of dollars" is the
entire point: **the free tier fails on request/token-rate ceilings long before it fails on
spend**, because there is no spend on the free tier to begin with.

## Latency at 10x (reasoned, not independently re-measured)

Per-call model inference latency (p50 25.92s, max 75.84s for the single agent) is a
function of the model and prompt length, not of how many OTHER requests are in flight — Groq
is a shared multi-tenant API, so this project's own traffic volume does not by itself make
*each individual call* slower. What 10x volume *does* do is increase how often any one
`max_wall_clock_s=60` budget is hit simply by chance (already observed: the max recorded
single-agent latency, 75.84s, individually exceeds the squad's `max_wall_clock_s=120s`
ceiling's half-point and this project's own 60s single-agent ceiling in `agent.py`'s
`_build_default_budgets()|max_wall_clock_s=60.0`) — but this is a tail-latency/timeout risk,
not a first-to-break resource. Rate limits (429/413) are a harder, earlier, deterministic
wall: at 10x volume, 8,000 tokens/minute is reached by roughly 10x faster than today, and
today's 1x volume already breached it 48% of the time.

## Assumptions and what is NOT measured here

- This analysis assumes continued use of the **free Groq tier**, which is what every
  historical trace in this repo was actually run against (`pricing.py`'s rate table and the
  protocol.md amendments both confirm this). A paid tier would raise the token/minute
  ceiling and could shift the bottleneck toward cost or latency instead — explicitly stated
  as an open variable, not measured here (protocol.md Amendment A2 makes the same caveat:
  "a paid tier might not fail those runs").
- No real p95/p99 latency distribution exists beyond the 10-sample race (`race_summary.json`
  states its own p99 is "effectively the max" over only 10 samples, too few for a real
  percentile). A true p95/p99 would need a larger, repeated sample — not available in this
  environment (no live API access during this Week 11 pass; see `phase0.md`).
- Local compute cost for retrieval (CPU-bound embedding + vector search) is not included in
  the cost projection and is not currently measured in dollars (see `cost_by_stage.md`'s
  final note) — at 10x volume this becomes a real, unmeasured cost center worth
  instrumenting next.

## Next engineering action

Given rate limits are the already-confirmed first bottleneck, the next action is **not**
cost optimization or latency tuning — it is request-level resilience against 429/413: bounded
retry with backoff on 429 (already partially done in `metering.py`'s `call_with_retry` for
the squad track, not present in plain `agent.py`), and a pre-flight token-count check before
sending any request over roughly 7,500 tokens so a 413 is predicted and trimmed for, not
just caught after the fact. Moving to a paid Groq tier (removing the 200,000 tokens/day /
8,000/min ceiling) is the other direct lever, and is the one that would most directly test
whether cost or latency becomes the *new* bottleneck once rate limits are no longer the
binding constraint — that follow-up experiment was not run here and would need its own
measurement pass.
