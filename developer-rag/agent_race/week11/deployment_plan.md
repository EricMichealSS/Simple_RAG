# Deployment Plan — `agent-v2-mitigated-default`

## Prompt/behavior version bump

`agent_race/agent.py`: `PROMPT_VERSION` added, set to `"agent-v2-mitigated-default"`.
Effective behavior change: `run_agent()`'s three defaults flip from
`mitigated=False, parallel_hint=False, compact_history=False` to all `True` (see
`phase5.md` for why all three, not just one). Every `run_agent()` result now carries
`prompt_version` and `mitigated` in its returned dict so any future trace can be attributed
to exactly which behavior produced it.

## The two-line plan

1. **Canary:** Route the next real batch of questions (or 10% of traffic, whichever this
   deploys into) through `agent-v2-mitigated-default`; watch `w11_reg_01` (this case) keep
   passing and watch the `budget_exceeded` rate across that batch — promote to 100% only if
   `budget_exceeded` stays at or below the pre-fix baseline rate (2/10 = 20%, from
   `trajectory_results_optimized.json`, the real measured rate this exact configuration
   already produced on the full 10-question set) for at least 24h or the next 20 real
   questions, whichever is longer.
2. **Rollback:** If `budget_exceeded` rate rises above the 20% baseline, or any output is
   flagged containing a known-deprecated term without its replacement, revert `agent.py`'s
   three defaults to `False`/`False`/`False` and `PROMPT_VERSION` to
   `"agent-v1-unmitigated-default"` (a one-line config change, no data migration — every
   request is stateless) and redeploy immediately; re-open the incident with the new failing
   trace attached.

## Why these specific thresholds, not arbitrary ones

The 20% `budget_exceeded` ceiling is not invented for this plan — it is the real, already-
measured rate this exact fixed configuration produced across all 10 original race questions
(`trajectory_results_optimized.json`: `budget_exhausted_no_answer: 2` of 10). Canarying
against "does the rate stay at or below what we already measured it to be" is a concrete,
falsifiable check, not a guess at an acceptable SLA.

## Status

**Written, not executed.** No deployment target exists for `agent_race/` (it is an
experimentation harness, not a hosted service) and this session was explicitly told never to
send real production traffic or incur paid API costs without authorization — this plan is
the artifact required by the brief, proposed and ready, awaiting an actual deployment surface
and authorization to run it for real.
