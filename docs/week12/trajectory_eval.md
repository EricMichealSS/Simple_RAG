# Week 12 — Trajectory evaluation of the integrated engine (Week 8 method)

Date: 2026-11-04 · Code: `developer-rag/sdk_eval/trajectory.py` (specs, extraction, scoring) · Run: `sdk_eval/golden.py` ·
Re-score without a model: `python -m sdk_engine eval trajectory --results <golden_results.json>` · Evidence: `evidence/phase9/`
(`golden_results.json`, `traces/spans.jsonl` + `server_spans.jsonl`, `trajectory_summary.json`, `trajectory_rescore_console.txt`).

## 1. What is being tested, and the caveat that governs everything below

Week 8 asked: is the *path* to a correct answer sound, not just the answer? Here the same question is asked of the integrated engine, using the
10 Week 7/8 race questions (`easy_01, easy_02, easy_13, medium_01, medium_07, medium_13, hard_01, hard_04, hard_06, hard_11`).

**In this engine the model does not choose tools.** The graph decides, by rules (`route` spans say `made_by: "rule"`; the rule is
`needs_changelog = an endpoint was detected or the question has change intent`, `graph.py:257`), and the MCP client enforces discovery, an allowlist and schema validation.
So this evaluation measures *the rules and the plumbing*: whether the rules ask for the right tools, in the right version scope, and whether the calls succeed.
It does **not** measure an LLM's tool judgement (Week 8 did). A tool-choice score of 100% would have been unsurprising; the evaluation is useful for the cases where it is not.

Also: the specs and the rules were written by the same author (me), which is a circularity risk. What limits it: the spec file was last modified at 01:39:52 on 2026-11-04,
before the golden run started (about 01:42:32), and I did **not** edit it after seeing the results (see §4: that is why two paths are scored "not ok").

## 2. Ground truth (written before the run)

`sdk_eval/trajectory.py::SPECS`. For each question: the version scope a correct run uses, the minimum calls per tool (`required`), the justified maximum per tool (`allowed_max`; a tool not listed is a wrong tool), whether the correct outcome is a refusal, and a one-line reason.

| question | scope | required | allowed max | expected outcome |
|---|---|---|---|---|
| easy_01 (default `mode`, render_markdown) | 2025-06-01 | search ×1 | search ×1, changelog ×1 (an endpoint is named) | answer |
| easy_02 (Swagger CLI Maven group id) | 3.x | search ×1 | search ×1 | answer |
| easy_13 (GraphQL rate limit: not in corpus) | 2025-06-01 | search ×1 | search ×1 | refuse |
| medium_01 (what replaced `assignee`) | 2025-06-01 | search ×1, changelog ×1 | same | answer |
| medium_07 (new `create_repository` enum values) | 2025-06-01 | search ×1 | search ×1, changelog ×1 | answer |
| medium_13 (fabricated formatter flag) | 3.x | search ×1 | search ×1 | refuse |
| hard_01, hard_04, hard_06, hard_11 (version comparisons) | 2022-11-28 and 2025-06-01 | search ×2 (one per version), changelog ×1 | same | answer |

Scoring (`score()`): `path_ok` = nothing required is missing, no call beyond the allowed maximum, no tool outside the allowed set, no failed call, every successful call ran in a version of the scope, and (where a search is required) each scope version was searched.
The version a call ran in is taken from the **server's** span (`requested_version`), joined to the client's `mcp.call` span, not from what the client says it sent.

## 3. Results (n = 10 questions, 23 tool calls)

| question | tools called (in order) | versions the server saw | path | outcome | judge | gap |
|---|---|---|---|---|---|---|
| easy_01 | search_docs, lookup_changes | 2025-06-01 ×2 | ok | answered | PASS | agree |
| easy_02 | search_docs, lookup_changes | 3.x ×2 | **not ok** (1 unnecessary) | answered | PASS | correct answer, unreliable path |
| easy_13 | search_docs | 2025-06-01 | ok | abstain (as expected) | PASS | agree |
| medium_01 | search_docs, lookup_changes | 2025-06-01 ×2 | ok | answered | PASS | agree |
| medium_07 | search_docs, lookup_changes | 2025-06-01 ×2 | ok | answered | PASS | agree |
| medium_13 | search_docs, lookup_changes | 3.x ×2 | **not ok** (1 unnecessary) | abstain (as expected) | PASS | correct answer, unreliable path |
| hard_01 / hard_04 / hard_06 / hard_11 | search_docs ×2, lookup_changes | 2022-11-28, 2025-06-01, 2022-11-28 | ok | answered | PASS | agree |

| metric | value | denominator |
|---|---|---|
| tool-choice accuracy (calls inside the spec's allowed set) | **0.913** (21) | 23 calls |
| path ok | **8** | 10 questions |
| outcome as expected (answer vs refusal) | 10 | 10 |
| outcome correct by the judge (gpt-oss-20b vs gold) | 10 | 10 |
| version arguments correct (every call, scope fully searched) | 10 | 10 |
| missing a required call | 0 | 10 |
| failed calls (timeout, error, schema rejection) | 0 | 23 |
| unnecessary calls / wrong-tool calls | 2 / 2 (the same two `lookup_changes` calls, counted by both rules: do not add them) | 23 |
| outcome × path | pass & path ok 8 · pass & path not ok 2 · fail & path ok 0 · fail & path not ok 0 | 10 |
| mean calls per question | 2.3 (2.2 over all 45) | |
| cost per question (10) | p50 $0.000360, max $0.000516 | 10 |

For all 45 questions the tool sequences were: `search_docs > lookup_changes` ×25, `search_docs > search_docs > lookup_changes` ×15 (every comparison), `search_docs` alone ×5 (the three refusals `easy_13`–`easy_15` and the two lookups `medium_06`, `medium_10`, where no endpoint and no change intent was detected).
`lookup_changes` ran in 40 of 45 questions.

## 4. The two paths that are "not ok", and what they do and do not show

`easy_02` and `medium_13` are Swagger questions. The router detected endpoints (`cli_general` + `java_generator` for easy_02; `cli_general` + `python_generator` for medium_13), set `needs_changelog`, and called `lookup_changes` — which my specs said was unnecessary because I assumed "no endpoint" for a CLI question.
Both readings are available and I have not chosen between them to make the score look better: either the **spec is wrong** (a named generator is a legitimate trigger for a proactive changelog check, exactly as for `easy_01`) or the **detector over-reaches** on generic CLI words.
What the data says: the extra call is cheap (client round trip p50 10.6 ms, max 164.8 ms over the 40 `lookup_changes` calls in the run; server side p50 1.9 ms; no tokens, the call is local; measured while other jobs shared the machine, so indicative), the answers were correct, and `lookup_changes` attached "deprecation / migration notes" to 40 of 45 results (relevance of those notes was **not graded**).
So: an unnecessary-call finding of low severity, an over-informing finding of unknown severity.

## 5. Week 8 baseline vs the integrated engine (not a controlled comparison)

| | Week 8 baseline (`agent_race/week8_report.md`, LLM-chosen tools, 10 questions) | Integrated engine (rule-chosen tools, same 10 questions) |
|---|---|---|
| outcome pass | 0.8 | 1.0 (10/10) |
| trajectory pass | 0.8 | 0.8 (8/10) |
| gap, outcome − trajectory | 0.0, but different questions failed each | +0.2, the same 2 questions are the whole gap |
| tool-choice accuracy | 1.0 (a call relevant to the question) | 0.913 (inside my allowed set: a stricter definition) |
| argument validity | 1.0 | version arguments 10/10 |
| mean steps | 1.55 | 2.3 |
| cost p50 | $0.000738 | $0.000360 |

Different harness, prompt, specs and scoring code, so differences are suggestive only. What does carry over from Week 8: **a pass on the outcome says nothing about the path**: two correct answers sit on a path my own spec rejects. What changed: Week 8's failures
were the model skipping the authoritative tool; here no required call is ever skipped (0/10), because the graph, not the model, makes the call.

## 6. What this evaluation cannot tell you

* Anything about LLM tool selection: the model never chooses a tool in this design (decision recorded in `tool_selection.md`). If a model-chosen router is ever added, this suite is the place to re-run it.
* Whether the 10 questions represent real use: they are the author's set; 4 of the 10 are one template (version comparison), so "hard" is 40% of this sample for one reason.
* Failure paths under load: 0 failed calls in 23 here; timeouts, malformed responses and a crashing server are covered by the offline suite (`m01`–`m03`) and by the load test (Phase 10), not by this run.
* Path quality for the other 35 questions: they were run but have no spec; only their sequences are listed in §3.
