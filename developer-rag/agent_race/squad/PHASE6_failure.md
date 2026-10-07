# Phase 6: Failure injection

**Done:** 2026-10-05. Code: `fault_injection.py`. Output: `failure_case.md`, `failure_runs.json`.
Persistent 500 on the version worker: manager called it 7 times (6 x 500, 7th blocked by the delegation cap), then **lied** (confident wrong answer, no mention of the failed check). Transient 500: retried once, recovered, correct. Run on gpt-oss-120b because the 20b quota was exhausted; one run each. Fix recommended, not built.
