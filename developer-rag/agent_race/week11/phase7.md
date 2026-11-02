# Phase 7 — Safe Deployment, Final Integration, and Review

**Status: COMPLETED (deployment itself NOT executed — see 7.1)**

## 7.1 Deployment plan

`deployment_plan.md` — two-line canary/rollback plan, with thresholds taken from real
measured data (`trajectory_results_optimized.json`'s real 20% `budget_exceeded` rate),
not invented. **Status: written, not executed.** `agent_race/` has no deployment target (it
is an experimentation harness) and this session was explicitly instructed never to send real
production traffic or incur paid API cost without authorization — neither was requested nor
given, so no deployment occurred.

## 7.2 Final integration — verified

- [x] Request tracing connected to the real request path: `agent.py`'s own loop now emits
      the Phase 1 schema's fields directly, not via a side-channel wrapper (except `trace_id`
      itself — see `phase1.md`'s named gap).
- [x] Generation spans emitted (`llm_call` events, per-call `cost_usd`, `timestamp`).
- [x] Tool/retrieval spans emitted when tools are actually used (`tool_call` events,
      `result_full`); correctly ABSENT in the incident trace because the bug was exactly that
      no tool was called — no span was fabricated to make the trace look complete.
- [x] Errors/budget hits recorded (`budget_exceeded` events — unmodified, pre-existing, still
      functioning).
- [x] Cost accounting consistent: `trace.json`'s per-span costs sum to its own total; separately
      reconciled against `cost_by_stage.md`'s independent breakdown — see Phase 4's
      reconciliation note.
- [x] Prompt version visible: `PROMPT_VERSION`/`"prompt_version"` present in every
      `run_agent()` return dict from this point forward.
- [x] Context IDs preserved: `result_full` in tool_call log entries, consumed by
      `extract_trace.py`.
- [x] Regression case remains in the suite's own module (`questions.py`), not a disconnected
      file that could silently stop being run.
- [x] Existing functionality remains available: every pre-existing `run_agent(` call site was
      checked (`phase5.md`); `trajectory_eval.py` was patched to preserve its own documented
      isolation guarantee; no other caller required a change.
- [x] No optional external observability tool is required for local execution — nothing new
      was installed (`tool_selection.md`).
- [x] Secrets not committed or logged: confirmed by direct review of every new/modified file;
      the one placeholder credential in `test_regression_w11.py` is a literal non-secret
      string, documented as such.

## 7.3 Final validation — commands and real results

```
py -m py_compile agent.py questions.py deprecation_assertions.py trajectory_eval.py \
   week11/fakes.py week11/test_regression_w11.py week11/extract_trace.py
  -> exit 0 (all files compile)

cd week11 && py test_regression_w11.py
  -> SUITE RESULT: GREEN (1/1), exit code 0 (re-confirmed at the end of this pass, not just
     once during Phase 5 — see this phase's own working notes)

git status --short  (repo root)
  -> M developer-rag/agent_race/agent.py
     M developer-rag/agent_race/questions.py
     M developer-rag/agent_race/trajectory_eval.py
     ?? developer-rag/agent_race/deprecation_assertions.py
     ?? developer-rag/agent_race/week11/
     (plus prior-session untracked files, unchanged, untouched)
```

No linter or type checker is configured in this repo (confirmed by inspection — no
`pyproject.toml`/`.flake8`/`mypy.ini` found under `agent_race/`), so none was run; this is
stated rather than silently skipped.

**Not run:** pytest (not installed, and not required — `test_regression_w11.py` is a plain
script with its own pass/fail convention, run directly above), the live 10-question full
suite against the real Groq API (environment blocker, `phase0.md`), and any real deployment
or load test (not authorized, Section 7.1).

## Acceptance criteria — verified

- [x] All required Week 11 artifacts exist (see `final_review.md`'s file list).
- [x] Every validation check that COULD run in this environment was run and its real output
      recorded above; every one that could not is named with its exact blocker.
- [x] Repository left clean: only the three intentional source edits plus new, additive files
      exist; nothing was discarded or reset (confirmed `git status --short` above).
- [x] No deployment or publication occurred without permission.
