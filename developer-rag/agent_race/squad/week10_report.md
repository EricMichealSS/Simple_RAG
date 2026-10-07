# Week 10: Docs squad vs single agent
**Track E: Developer Documentation. Date: 2026-10-05.**

## Result in one paragraph
I built a manager + 2 workers (version/deprecation, code-sample) and raced it against the existing single agent on the same cases with the same judge. On the 7 cases both arms finished, accuracy tied (7/7 vs 7/7), the squad used 1.2x the tokens (25,129 vs 21,118) and 1.3x the cost per question ($0.00040 vs $0.00030), and when the version worker was made to return a 500 the squad retried and then shipped a confident wrong answer without a verified version check. **Verdict: KILL**, by a rule written before the race. Evidence is weak (1 repeat, 7 cases); it all points the same way.

## The five required deliverables
| Deliverable | File |
|---|---|
| 4 metrics x 2 arms | `race_table.md` |
| Hand-off tokens | `handoffs.log` |
| Multiplier + dominant hand-off | `multiplier.txt`: **1.2x**; "version_worker tool-loop (lap 2+)", 31% of squad tokens |
| Injected 500 | `failure_case.md`: retried, then **lied** (persistent); retried and recovered (transient) |
| Verdict (7 lines) | `verdict.md` |
Bonus: `agent_card.json`, `a2a_notes.md`. Rules and prediction frozen first: `protocol.md`, `prediction.txt`. Phase notes: `PHASE0` to `PHASE8`.

## What did NOT go to plan (tell the mentor this first)
- **Not 10 cases x 3 repeats.** I got 1 repeat, 7 paired cases. Free-tier Groq limits (200k tokens/day, 8k/min, 8k per request) stopped the rest. The first attempt on gpt-oss-120b was void; the final race ran on gpt-oss-20b for both arms (judge 120b). Details: protocol amendments A1, A2 and `void_attempt1_quota/`.
- **Three cases excluded, all disclosed:** `easy_13` and `medium_13` (squad never finished: its manager kept delegating, ~28k tokens, context near the provider's 8,000-token request cap; single also spent 22-26k and timed out) and `hard_11` (single run cut off by my own runner's clock after 2.4 s of work; an objective rule excludes it; counting it, the verdict is unchanged).
- **Latency is not usable.** The single arm's latency included hidden SDK retry sleeps; the squad's did not. The squad looked 5x faster, but I make no claim from it.
- **Bugs of mine found and fixed during the work:** squad tool schema rejecting `null`, wait-time parser reading "75ms" as minutes, wall-clock check counting rate-limit sleeps.
- The failure test ran on gpt-oss-120b (20b quota gone), one run per variant.
- Task-text mismatches: no orchestrator existed from Week 9 (built now); "Week-6 cases" are a different corpus, so the 10 Week-7 race questions were used for both arms.
- The only change to an existing file: `pricing.py` now reads an optional `RACE_MODEL` env var (default unchanged). `agent.py` was not modified.

## What I learned
1. Multi-agent has a real, countable cost, and on look-up questions it bought nothing measurable.
2. The biggest token share was a worker re-reading what it retrieved, not the manager re-reading worker output (my prediction was wrong).
3. A manager with no failure handling does not fail safe: it retries blindly, then writes fluent, unverified answers. That, not tokens, is the strongest reason to kill this version.
4. The fair-race controls mattered, and my own measurement code was the most likely source of unfairness. It is worth auditing the harness as hard as the system.

## What would change the verdict
Worker contract with an explicit "unverified" flag, bounded retry, a paid-tier rerun with 3 repeats and the 2 negative cases, and a task with independent sub-questions to parallelise.

## Git
Do not push: `.env`, `data/`, venvs, `__pycache__`. Suggested to push: everything in `agent_race/squad/` (incl. `void_attempt1_quota/`), `agent_race/week10_plan.md`, and the one-line `pricing.py` change. I did not run any git command.
