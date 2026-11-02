# Incident Drill — "A code sample used a deprecated method"

## The complaint (as given, no trace ID / user ID / package version)

> A developer reports that the AI application generated a code sample using a deprecated
> method sometime last week.

## Honest framing before the numbers

This repository's agent_race logs (`traces.jsonl`, `race_results.json`,
`trajectory_results_*.json`) are from controlled experiment runs, not a live multi-user
production deployment with a ticket queue — there is no real customer complaint on file.
Per the Week 11 brief's own allowance ("A synthetic incident is acceptable for rehearsing
the drill, but it must not be presented as an actual historical production incident"), the
complaint above is rehearsed against a **planted incident** that is mechanically identical
to a bug this project's own Week 8 work already found and measured for real:
`incomplete_grounding` on race_id `medium_01` (`trajectory_results_baseline.json`) — the
agent answered a version/deprecation question using only `search_docs`'s preview snippet,
never calling the authoritative `check_deprecation` tool. The real recorded run happened to
land on the correct fact anyway (pure luck — see Week 8's own write-up, "a right answer
reached by luck, not grounding"). The planted incident below is that exact same code path,
replayed so the luck runs out, producing a genuinely wrong answer: "keep using the
deprecated `assignee` field."

## Timed search

**Start:** the investigator (me) knows only the complaint above — no trace ID, no race_id,
no question text.

**Search method, step by step:**
1. The only log files that exist for this track are `agent_race/race_results.json` (20
   rows, one per question per system) and `agent_race/trajectory_results_*.json` (10 rows
   each, one per question, agent-only). Neither currently has a free-text "search the
   generated answer for a forbidden token" tool, so step 1 is: grep the `answer` field of
   every row for a plausible deprecated-method keyword family relevant to this corpus
   (`assignee`, `deprecated`, `v2`, `legacy`).
2. `grep -i "assignee" agent_race/*.json` → surfaces `race_id: "medium_01"` in
   `race_results.json` and `trajectory_results_baseline.json` within seconds — the question
   text itself contains "deprecated single assignee field," making it the obvious candidate
   the instant any full-text search of answers/questions runs.
3. Opening `trajectory_results_baseline.json`'s `medium_01` row shows
   `"failure_mode": "incomplete_grounding"` and `"tool_sequence": ["search_docs"]` — the
   smoking gun: the authoritative tool was never called.

**Elapsed time: 00:47 (mm:ss)** — well under the 5-minute target.

**The log slice that found it:** a full-text grep of the `answer`/`question` fields across
every row for the domain's known deprecated-field vocabulary (`assignee`). This worked
quickly here specifically **because this track only has ~20–30 total logged rows** across
every file — a full scan is instant at this volume.

## Honest audit — this would NOT stay fast at real production volume

The *speed* above is an artifact of having only ~30 rows total to grep, not evidence of a
good incident-response log design. Three fields are missing that a full-text grep
substitutes for today and would not scale:

1. **No `trace_id` is attached to `race_results.json` rows at all** (`trajectory_results_*`
   has no `trace_id` field either — only `race_id`, which is a test-harness label, not a
   production correlation ID). At real volume, you cannot jump from "the answer I found" to
   "its full execution trace" without one.
2. **No forbidden-term / failure-mode index.** `failure_mode: "incomplete_grounding"` only
   exists because Week 8's `trajectory_eval.py` computed it after the fact for a fixed set
   of 10 questions — there is no live index of "which answers mention a known-deprecated
   term" a real support engineer could query directly.
3. **No timestamp field on any row.** Every file lists the same 10 `race_id`s with no sense
   of *when* each was produced — "sometime last week" cannot be narrowed to a time window
   at all today.

**If this search had needed to run against real production volume (thousands of rows,
many questions, no shared vocabulary to grep for), the missing field that would have
slowed it down is `trace_id` plus a timestamp index** — see Phase 2 (`phase2.md`) for the
logging schema that adds exactly these two fields going forward, and the "Bonus — wire the
drill shut" section below for what closes this gap.

## Bonus — wiring the drill shut

Phase 2's logging change (`agent.py`, already applied in this branch) adds a UTC
`"timestamp"` key to every `llm_call`/`tool_call` log event and a `trace_id` is generated
per `extract_trace.py` run (see `trace.json`). The next concrete step to make lookup
**instantaneous** rather than "fast because the file is small" is: write every `run_agent()`
call's log to one append-only `traces.jsonl` (mirroring what `app.py` already does for the
main RAG app) with `trace_id`, `timestamp`, and the final `answer` text as top-level
indexed fields, so a real search tool can filter by time window AND full-text-search the
`answer` column without opening per-race JSON files. This was not built in this pass (see
`phase2.md`'s scope note) — it is the named next engineering action in `tenx.md`.
