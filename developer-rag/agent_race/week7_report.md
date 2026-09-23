# Week 7 — Agent vs. Workflow Race
**Track E: Developer Documentation (GitHub API + Swagger Codegen migration)**
**Date: 2026-09-10**

---

## 1. What this week is, and the Week 6 → 7 delta

Week 6 measured a single-shot RAG answer. Week 7 asks a different question:
does a *multi-step* migration task (find the current spec, check what changed
between versions, answer) actually need an autonomous agent loop, or does a
hard-coded 3-step pipeline do the same job faster, cheaper, and just as
reliably? Both a hand-built ReAct agent and a fixed workflow were built,
sharing the same 3 tools, the same model (`openai/gpt-oss-120b`, same
provider/pricing as Week 5–6's Groq setup), and the same output contract
(`ANSWER: ...`), then raced head-to-head on 10 real questions.

## 2. New data this week

Three new reference PDFs (`github_combined_reference.pdf` — GitHub REST API,
versions 2022-11-28 and 2025-06-01 side by side; `swagger_v2_reference.pdf`;
`swagger_v3_reference.pdf`), ingested into a new, separate ChromaDB
collection (`race_docs`) — untouched: the Week 5/6 `developer_docs`
collection. Plus a 45-question golden set (15 easy/15 medium/15 hard,
pre-graded), from which the 10 race questions were drawn.

## 3. The third tool (Requirement 1)

`check_deprecation(api_version, endpoint)` — returns ONLY what changed/was
deprecated for one (version, endpoint) pair, never the current parameter
table (that's `get_openapi_spec`'s job). Both parameters are closed enums
(4 known API versions, 10 known endpoints/generators), enforced both in the
tool schema and again in the Python implementation. Full before/after
description diff, including the rejected v1 draft that *did* overlap with
`get_openapi_spec`, is in `tool_diff.md`.

To make the two tools actually return different *content*, not just carry
different descriptions, ingestion (`ingest.py`) splits each doc page at its
"Previous version (...)" / "Compared to v2" heading into a `spec` chunk and
a `diff` chunk with separate metadata — otherwise both tools would have
handed back the same whole-page text (caught and fixed before wiring the
tools up; see `ingest.py`'s `_split_spec_and_diff`).

## 4. The agent and the workflow (Requirements 2 & 4)

`agent.py` — hand-built ReAct loop, no framework: plan (LLM call with tool
schemas) → act (run any tool calls) → look at results → repeat until a plain
`ANSWER: ...` with no tool calls, or a budget fires. All 4 budgets are
checked every iteration:

| Budget | Enforcement |
|---|---|
| `max_iterations` | loop cap, checked via the `for` bound itself |
| `max_tokens` | **cumulative across every lap** (the full message history resends every lap — summed, not just the last call's tokens, per the brief's explicit warning) |
| `max_cost_usd` | cumulative `$` from published Groq per-token rates (`pricing.py`) |
| `max_wall_clock_s` | real elapsed time, checked before each new LLM call |

`workflow.py` — identical 3 tools, identical model, identical output
contract, but the step order is hard-coded in Python: search_docs → (always)
get_openapi_spec → (always) check_deprecation → one synthesis call. No LLM
ever decides which tool to call next; the only thing later steps read from
step 1 is its top hit's metadata, read directly in Python.

## 5. Budget termination (Requirement 4 deliverable)

Two independent, real terminations, both clean (no crash, no hang):

- **`logs/budget_termination_max_tokens.json`** — question: *"What is the
  rate limit for unauthenticated GraphQL queries in GitHub API version
  2025-06-01?"* (a negative case — the docs only cover REST). The agent
  retried `search_docs` three times with reworded queries hunting for
  content that doesn't exist, growing cumulative tokens 789 → 2,517 → 5,193
  → 9,036 across 4 laps, and stopped cleanly the moment 9,036 > the 8,000
  `max_tokens` budget — `status: budget_exceeded`, `budget_hit: max_tokens`,
  `answer: null`. This occurred naturally during the real race run, not as
  a staged demo.
- **`logs/budget_termination_max_iterations.json`** (`budget_demo.py`) — the
  same hard question capped at `max_iterations=1`: one tool call
  (`check_deprecation`), then a clean stop with `budget_hit: max_iterations`,
  confirming that budget independently fires too.

## 6. The race (Requirement 3) — race.csv, 20 rows (10 questions × 2 systems)

10 questions: 3 easy, 3 medium, 4 hard — **4 dependency cases** (exceeds the
"at least 3" requirement), where a correct answer genuinely requires reading
the current spec *and* what changed relative to the other named version
(e.g. "Compare how the `context` parameter format changed between
2022-11-28 and 2025-06-01"). 2 of the 10 are negative cases (the correct
answer is a refusal — the info isn't in the docs at all), to test whether
either system hallucinates under pressure.

```
metric                            agent          workflow
----------------------------------------------------------------------
pass rate                          0.8               1.0
p50 latency (s)                  25.92             6.643
total tokens                     49477             10690
cost/question ($)             0.000938          0.000249
```

**The workflow wins on all four numbers.** Its pass rate is even *higher*
than the agent's — both agent failures were the negative cases, where the
loop's freedom to keep retrying `search_docs` became a liability (it burned
budget instead of concluding "not found"), while the workflow's bounded 4
steps naturally terminate in a refusal when `get_openapi_spec` /
`check_deprecation` come back empty. Full per-question detail, including
the grader's one-sentence reasoning for every verdict, is in
`race_results.json`.

## 7. Verdict (Requirement 5)

See `verdict.md` (144 words). Short version: applying the decision rule (does
the path vary by input?) to all 10 questions — including the 4 cross-version
dependency cases — the answer is no. The identical fixed 4-step sequence
answers every one of them correctly; `check_deprecation`'s diff text already
contains the prior version's behavior inline, so nothing required branching
on what an earlier step found. None of these 10 needs an agent; ship the
workflow.

## 8. Files

| file | what it is |
|---|---|
| `page_metadata.json` | hand-authored per-page version/endpoint/diff ground truth for all 24 PDF pages |
| `ingest.py` | ingests the 3 PDFs into the `race_docs` collection, splitting spec text from diff text per page |
| `tools.py` | the 3 tools + their JSON schemas (`TOOL_SCHEMAS`) |
| `pricing.py` | published Groq per-token rates, used for the cost metric |
| `agent.py` | the hand-built ReAct loop, all 4 budgets enforced |
| `workflow.py` | the fixed 3-step pipeline, same tools/model/contract |
| `questions.py` | the 10 selected race questions (4 dependency, 2 negative) |
| `grader.py` | binary PASS/FAIL grading vs. the golden-set answer, separate small model |
| `race.py` | runs both systems over the 10 questions, writes race.csv |
| `race.csv` / `race_results.json` / `race_summary.json` | the race output at 3 levels of detail |
| `budget_demo.py` | deliberately forces a `max_iterations` termination |
| `logs/budget_termination_max_tokens.json` | natural budget termination captured during the real race |
| `logs/budget_termination_max_iterations.json` | deliberate budget termination |
| `tool_diff.md` | the third tool's description diff (rejected overlapping draft → shipped version) |
| `verdict.md` | the verdict paragraph |
| `week7_report.md` | this file |
