# Phase 6 — Cost Optimization and Ten-x Analysis

**Status: COMPLETED**

## Objective

Attribute the incident's cost by stage, measure the real cost of the fix, and determine
what breaks first at 10x traffic with numeric backing.

## 6.1 Cost by stage

Full table and reconciliation: `cost_by_stage.md`. Summary: the incident's pre-fix request
cost $0.000511 (100% generation, $0 retrieval API cost, $0 tool cost because the tools were
never called — their absence is the bug). The fixed request costs $0.000627 (+23.4% tokens,
+22.7% cost) because grounding the answer requires one more authoritative tool call and the
LLM round-trip to read its result. This is reported, not hidden, per the brief's explicit
instruction not to optimize cost at correctness's expense without measuring the trade-off.

## 6.2 Baseline measurement

Real, already-recorded: `race_summary.json` — single agent mean cost/question $0.000938,
p50 latency 25.92s, across the real 10-question race. Used as the volume/cost baseline for
Section 6.4 instead of a freshly invented number.

## 6.3 Optimization considered and NOT implemented in this pass

The brief allows "no safe, measurable optimization is possible with the available
environment; document the bottleneck and the experiment needed rather than claiming an
improvement that did not occur." That is the honest state here: the one real lever this
pass touched (`mitigated`/`parallel_hint`/`compact_history`) was applied as a **correctness**
fix, not a cost optimization — and it measurably INCREASES cost per the table above. No
separate cost-reduction experiment (caching, cheaper model routing, reduced context) was run
in this pass, because Section 6.4 found a different, already-measured constraint
(rate limits) that would make a cost optimization not the highest-value next engineering
action right now. This is stated plainly rather than performing a token-trimming exercise
for its own sake just to have an "optimization" to report.

## 6.4 Ten-times traffic analysis

Full analysis: `tenx.md`. One-line conclusion: **API rate limits break first — and already
have, at today's volume**, evidenced by `squad/protocol.md`'s real, dated record of 29 of 60
calls failing on HTTP 429 at the smallest experimental volume this project has run, and
`taxonomy.md`'s independent real finding of 20% Groq 413/TPM failures in just 20 sampled
live-app traces. Projected cost at 10x volume is under $0.10 for a full 100-question race —
not a plausible competing bottleneck at any volume measured or projected here.

## Tests executed

None new in this phase — Section 6.4 reuses `trace.json` (Phase 4), `race_results.json`,
`race_summary.json`, and `squad/protocol.md`/`taxonomy.md` (both pre-existing, real files)
rather than generating new test output.

## Acceptance criteria — verified

- [x] Cost attributed by stage (`cost_by_stage.md`).
- [x] Baseline recorded from real historical data, not invented.
- [x] No optimization is falsely claimed to have been measured when it was not attempted —
      Section 6.3 states this directly.
- [x] The 10x bottleneck conclusion is backed by specific real numbers (29/60 real rate-limit
      failures, 20% real 413/TPM rate), not a guess.
