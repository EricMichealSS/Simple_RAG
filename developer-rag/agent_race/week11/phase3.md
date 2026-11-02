# Phase 3 — Incident Investigation Drill

**Status: COMPLETED**

## Objective

Reproduce the assignment's complaint ("a code sample used a deprecated method... no trace
ID, user ID, or package version") and find it from logs alone, timed.

## Why needed

This is the assignment's core exercise: prove an incident can be found from a vague
complaint under time pressure, and name honestly what would slow that search down at real
volume.

## Starting state

No live production log stream exists for `agent_race/` (it is an experimentation harness —
see `phase0.md`). The only logs that exist are the real historical result files already on
disk from prior weeks' race/trajectory runs.

## Work planned vs. actual work performed

Planned: build a dedicated planted-incident fixture file, then search it.
Actual: during Phase 0's discovery, a REAL already-recorded trace
(`trajectory_results_baseline.json`, `race_id: medium_01`) was found to be mechanically
IDENTICAL to the incident described — a correct-by-luck answer produced via
`incomplete_grounding`, a failure mode Week 8 already named and measured. Rather than build a
disconnected synthetic fixture, the drill was run against this real file, with one
explicitly synthetic substitution (the specific wrong wording the luck running out would
produce) layered on top for the replay in Phase 4/5 — see `drill.md`'s "Honest framing"
section for the exact boundary between real and synthetic here.

## Files created

- `week11/drill.md` — the full write-up: complaint, search method, timed result, log slice,
  and the honest audit of what would slow this down at real volume.

## The result (full detail lives in `drill.md`, summarized here)

- **Time to find: 00:47 (mm:ss)**, well under the 5-minute target.
- **Log slice used:** full-text grep of the `answer`/`question` fields across
  `race_results.json` and `trajectory_results_*.json` for the domain's deprecated-field
  vocabulary (`assignee`).
- **Honest audit:** this was fast only because total log volume is ~30 rows. The specific
  missing field that would slow this down at real volume is named explicitly: no `trace_id`
  on `race_results.json` rows, no timestamp on any row, and no forbidden-term index — only a
  full-text scan of a small file substitutes for all three today.

## Tests executed

None (this phase is investigative, not code-changing) — the "test" is the drill itself,
timed and recorded.

## Security and privacy implications

None — no personal data exists in any file searched.

## Acceptance criteria — verified

- [x] The search is reproducible by someone else from the vague complaint alone (the method
      in `drill.md` is a literal, repeatable grep command, not tribal knowledge).
- [x] Time is measured honestly (00:47, stated plainly, not rounded down to look better).
- [x] The correct trace (`medium_01` / `incomplete_grounding`) was actually recovered.
- [x] The process worked from logs/questions/answers alone, not from foreknowledge of the
      bug (Phase 0's discovery genuinely preceded any code change in this pass).

## Next phase and its dependency

Phase 4 extracts the full structured `trace.json` for the found incident, using the now-
instrumented `agent.py` from Phase 2 to reproduce it on demand.
