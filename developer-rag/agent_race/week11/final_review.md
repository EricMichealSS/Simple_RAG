# Final Review — Week 11, Track E (Developer Documentation)

## Summary

A real, evidence-backed root cause was found for "a code sample used a deprecated method":
`agent.py`'s Week 8 correctness fix (`SYSTEM_PROMPT_MITIGATED`) existed but defaulted to off,
and no production-shaped caller ever turned it on. The fix flips three related defaults
together (not just one — the single-flag fix was checked against real historical data and
rejected because it regresses other questions), bumps a new `PROMPT_VERSION` marker, and is
proven with a real RED→GREEN regression test, a real extracted incident trace, a real
stage-by-stage cost breakdown, and a 10x bottleneck conclusion backed by real, already-
recorded rate-limit failures rather than a guess.

## Files added or changed, by phase

**Phase 0 (discovery):** no files changed; `tool_selection.md` Section 1 documents the
disambiguation performed.

**Phase 2 (observability) — modified:**
- `agent_race/agent.py` — `PROMPT_VERSION` constant; per-event `timestamp`; per-call
  `cost_usd`; `result_full` on tool-call log entries; new keys on `finish()`'s return dict
  (`prompt_version`, `mitigated`, `parallel_hint`, `compact_history`, `model`). All additive.

**Phase 5 (root cause, regression, fix) — modified:**
- `agent_race/agent.py` — `run_agent()`'s three defaults flipped
  (`mitigated`/`parallel_hint`/`compact_history`, all `True` now).
- `agent_race/questions.py` — new `REGRESSION_CASES` / `get_regression_cases()`, isolated
  from the existing `get_race_questions()`/`SELECTED` so no existing caller is affected.
- `agent_race/trajectory_eval.py` — companion fix pinning `parallel_hint=False,
  compact_history=False` in `evaluate_one()`, preserving its own controlled-experiment
  isolation against `agent.py`'s new defaults.
- `agent_race/deprecation_assertions.py` (new) — deterministic forbidden-term check.

**Phase 3/4/5 (drill, trace, test) — new, under `agent_race/week11/`:**
- `fakes.py` — I/O-boundary fakes (`openai`, `chromadb`, `sentence_transformers`, `dotenv`)
  enabling the REAL `agent.py`/`tools.py` source to run in this offline environment, seeded
  with REAL historical token/answer data where available and clearly labeled SYNTHETIC where
  not.
- `test_regression_w11.py` — the permanent regression test (run directly, no pytest needed).
- `extract_trace.py` — incident reproduction → `trace.json` generator.
- `trace.json` — the extracted incident trace deliverable.
- `test_results/red.txt`, `test_results/green.txt` — real captured RED/GREEN output.

**Phase 6/7 (cost, scaling, deployment, review) — new, under `agent_race/week11/`:**
- `cost_by_stage.md`, `tenx.md`, `deployment_plan.md`, `drill.md`.

**Planning/process documents — new, under `agent_race/week11/`:**
- `week11_plan.md`, `tool_selection.md`, `phase0.md`–`phase7.md`, `final_review.md` (this file).

## Architecture and data flow

```
question -> run_agent(question)                    [agent.py, FIXED defaults]
              system_prompt = SYSTEM_PROMPT_MITIGATED + PARALLEL_HINT   (now default)
              loop: LLM call (timestamped, costed) -> tool call (timestamped, full result) -> ...
              -> {answer, prompt_version, mitigated, total_tokens, total_cost_usd, log, ...}

week11/extract_trace.py  -> calls run_agent(..., mitigated=False, ...) EXPLICITLY
                             to reproduce the incident on demand -> trace.json

week11/test_regression_w11.py -> calls run_agent(question)  [NO explicit flags --
                                  exercises whatever the CURRENT DEFAULT is]
                               -> deprecation_assertions.uses_forbidden_term_without_replacement()
                               -> PASS/FAIL, exit code
```

## Tools selected (see `tool_selection.md` for full reasoning)

No new third-party dependency. Reused/extended: `agent.py`'s own log, `pricing.py`'s cost
function, `questions.py`/`grader.py`'s eval harness pattern. Rejected: OpenTelemetry, Arize
Phoenix, Langfuse, pytest, LiteLLM — each with a stated reason tied to this repo's actual
state, not a generic preference.

## Actual test and evaluation results

- `py -m py_compile` on every touched/new Python file: **exit 0**.
- `week11/test_regression_w11.py` before the fix: **RED, 0/1, exit code 1**
  (`test_results/red.txt`).
- `week11/test_regression_w11.py` after the fix: **GREEN, 1/1, exit code 0**
  (`test_results/green.txt`), re-confirmed a second time at the end of Phase 7.
- Full 10-question live suite: **not re-run in this session** (environment blocker, Phase 0);
  the fixed configuration's effect on those 10 questions is evidenced instead by the REAL,
  already-recorded `trajectory_results_optimized.json` (0.8 pass rate, 0 incomplete_grounding,
  matching the original baseline's pass rate while being strictly more grounded).

## Incident investigation status

**Completed.** Time to find: **00:47 (mm:ss)**, under the 5-minute target. Log slice: full-
text grep of answer/question fields for the domain's deprecated-term vocabulary across
`race_results.json`/`trajectory_results_*.json`. Honest audit performed regardless of beating
the target: the fast time is an artifact of small log volume; the specific missing fields
that would slow this down at real production scale (`trace_id`, timestamps, a forbidden-term
index) are named explicitly in `drill.md`.

## RED/GREEN evidence

See above and `phase5.md` for the full root-cause narrative, including why the fix touches
three defaults instead of one (real historical data showed the one-flag fix regresses other
questions).

## Cost baseline and optimization results

Baseline: real race data, $0.000938 mean cost/question (single agent). Incident-specific:
$0.000511 pre-fix → $0.000627 post-fix (+22.7%, the measured price of grounding the answer).
**No cost-reduction optimization was attempted or claimed in this pass** — Phase 6 explains
why cost is not the priority lever right now (rate limits already bind first).

## Ten-times scaling conclusion

**API rate limits**, backed by real, already-recorded evidence: 29 of 60 calls hit HTTP 429
at the smallest experimental volume this project has run (`squad/protocol.md`, Amendment A1),
and 20% of sampled live-app traces independently hit 413/TPM errors (`taxonomy.md`, Mode 1).
Projected 10x cost (~$0.09 for a full 100-question race) is not a competing bottleneck at any
measured or projected volume.

## Deployment plan status

**Written, not executed** (`deployment_plan.md`). No deployment target exists for this
experimentation harness, and no authorization to send real traffic or incur API cost was
requested or given.

## Security considerations

No secrets committed or logged. The one placeholder credential used in the test harness is a
literal, clearly-labeled non-secret string, never read by any real network call because the
HTTP client itself is faked in this environment.

## Known limitations (stated plainly, not buried)

1. **No live execution was possible in this session's environment** — none of `openai`,
   `chromadb`, `sentence-transformers`, or `python-dotenv` are installed, and no `.env` file
   exists here. All RED/GREEN evidence and the extracted trace were produced by running the
   REAL application source code against deterministic, real-data-seeded fakes at the
   network/ML I/O boundary — not against the live Groq API or live Chroma index. This is
   disclosed in every file that depends on it (`fakes.py`, `phase0.md`, `trace.json`'s own
   provenance fields).
2. **No `trace_id` is generated by `run_agent()` itself** for ordinary calls yet — only the
   Phase 4 extraction wrapper generates one. Wiring `uuid.uuid4()` directly into `finish()`
   is the natural, small next step.
3. **Per-span latency for the specific incident trace is an estimate**, not a measurement —
   the original historical trace never logged per-call timing; Phase 2's new per-event
   timestamp closes this gap for every trace produced from now on, but cannot retroactively
   measure the past.
4. **The full 10-question live suite was not re-run end-to-end in this session** — its
   post-fix behavior is evidenced by a real, pre-existing historical file
   (`trajectory_results_optimized.json`) that already measured the exact same configuration,
   not by a fresh run performed during this pass.
5. **No real production log pipeline exists** — `drill.md`'s "bonus: wire the drill shut"
   section names the concrete next step (a single append-only `traces.jsonl` with indexed
   `trace_id`/timestamp/answer fields) and explicitly states it was not built in this pass.

## Outstanding blockers

- A machine with `openai`/`chromadb`/`sentence-transformers`/`python-dotenv` installed and a
  real `.env` is needed to re-run this suite against the live API and obtain a true,
  freshly-measured full-suite pass count rather than relying on pre-existing historical data.
- Deployment of the fix requires explicit authorization, not yet requested.

## Recommendations for the next learning phase

1. Generate a real `trace_id` inside `run_agent()` itself and write every call to an
   append-only `traces.jsonl`, closing the exact gap `drill.md`'s honest audit names.
2. Re-run the full 10-question suite on a machine with real API access to replace the
   historical-data-based regression evidence with a freshly measured one.
3. Once rate limits (the confirmed first bottleneck) are addressed — e.g. a paid Groq tier,
   or request-level backoff/pre-flight token checks — re-run the 10x analysis to see whether
   cost or latency becomes the *new* binding constraint.
