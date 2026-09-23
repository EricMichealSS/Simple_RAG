# Week 8 — Trajectory Evaluation, Gap Analysis, and One Mitigation
**Track E: Developer Documentation (GitHub API + Swagger Codegen migration)**
**Date: 2026-09-22**

---

## 1. Week 7 → Week 8 delta

Week 7 raced the agent against a fixed workflow on **outcome only** — did the
final answer match the golden answer? Week 8 adds a second, independent
scoring layer on the exact same agent (`agent.py`, `tools.py`, and the same
10 questions from `questions.py` — nothing rebuilt from scratch): does the
**path** the agent took to reach that answer hold up? A right answer reached
by skipping the authoritative tool is a bug that just hasn't been caught yet.

## 2. Trajectory ground truth (`trajectory_specs.py`) — written before any run

For each of the 10 questions: `required_tools` (must all appear),
`allowed_tools` (nothing outside this set is legitimate), an explicit set of
`valid_sequences` (the "assert as a set, not one sequence" requirement — the
4 cross-version "hard" questions all accept either tool order, with or
without a preceding `search_docs`), `min_steps`, and the expected
`(api_version, endpoint)` pair(s). `VALID_PAIRS` is built mechanically from
`page_metadata.json` — every real (version, endpoint) combination that
actually exists in the corpus — so "hallucinated argument" is a structural
check, not a judgment call.

## 3. The 4 trajectory numbers (baseline)

```
outcome_pass_rate:       0.8
trajectory_pass_rate:    0.8
tool_choice_accuracy:    1.0
argument_validity_rate:  1.0
step_efficiency_mean:    1.55
cost p50 / max (usd):    0.000738 / 0.001641
```

`tool_choice_accuracy` and `argument_validity_rate` are both a clean 1.0 —
whenever the agent did call a tool, it picked one relevant to the question
and never invented a (version, endpoint) pair. That's the good news, and
it's exactly the kind of number outcome-only grading would never have
surfaced on its own. Cost p50/max (not a bare mean) already shows real
variance: the worst run cost more than double the median.

## 4. The gap — and why the aggregate number nearly hid the real finding

**GAP (baseline) = outcome_pass_rate − trajectory_pass_rate = 0.8 − 0.8 = 0.0**

Reported alone, that number says "no gap, nothing to see." It's wrong to
stop there: outcome and trajectory don't fail on the *same* two questions.
Two different cases (`easy_13`, `medium_13` — both negative/refusal cases)
failed on **outcome** by hitting `max_tokens` before answering; two entirely
different cases passed outcome while failing trajectory:

**Right-answer-wrong-path trace — `medium_01`:**
> Q: *"In GitHub API version 2025-06-01, what parameter replaces the
> deprecated single assignee field when creating an issue?"*
> Tool sequence: **`['search_docs']`** — one call, nothing else.
> Answer: *"The deprecated `assignee` field is replaced by the `assignees`
> parameter, which accepts an array of user logins."* — **correct**, and
> graded PASS by the outcome judge.

The agent never called `get_openapi_spec`. It answered directly from
`search_docs`'s preview snippet, which happened to already contain enough
of the page text to be right. `easy_02` (Swagger CLI Maven group id) is the
same shape: `tool_sequence=['search_docs']`, correct answer, no
authoritative lookup. This is precisely the problem statement's scenario —
a right answer down a path that will silently break the day the underlying
spec changes and the cached snippet no longer matches it.

**The lesson:** a single aggregate gap number of 0.0 would have reported
"nothing found." Only tracing the *specific* cases where the two evals
disagree — which the aggregate necessarily discards — surfaces the bug.

## 5. Failure-mode taxonomy and baseline counts

| Mode | Count | What it means |
|---|---|---|
| `budget_exhausted_no_answer` | 2 | ran out of a budget before ever answering (both negative cases: `easy_13`, `medium_13`) |
| `hallucinated_argument` | 0 | called a tool with a nonexistent (version, endpoint) pair |
| `no_tool_used` | 0 | answered without calling any tool at all |
| `incomplete_grounding` | **2** | called some but not all required tools (`easy_02`, `medium_01` — the cases above) |
| `search_thrash_loop` | 0 | called `search_docs` more than once |
| `clean_pass` | 6 | correct, fully grounded path |

`incomplete_grounding` is tied for the top count (2) with
`budget_exhausted_no_answer`, but it's the mode the problem statement
literally names ("a correct v3 code sample without ever reading the spec"),
and it's the new finding this week — `budget_exhausted_no_answer` was
already characterized in Week 7. **`incomplete_grounding` is the mitigation
target.**

## 6. The mitigation — exactly one change

**What:** `agent.py` — added `SYSTEM_PROMPT_MITIGATED`, a single appended
paragraph telling the agent that `search_docs` is a preview only and it must
still call `get_openapi_spec`/`check_deprecation` before finalizing an
answer, never answer from a `search_docs` result alone. `run_agent()` gained
one new parameter, `mitigated: bool = False`, that selects which prompt to
use. Nothing else changed — same tools, same budgets, same loop, same
model. This is "tighter tool description" from the list of allowed
mitigations, applied as a system-prompt rule.

**Before → after, target mode:**

| | incomplete_grounding | trajectory_pass_rate |
|---|---|---|
| before | **2** | 0.8 |
| after | **0** | **1.0** |

Both `easy_02` and `medium_01` now call `get_openapi_spec` before
answering. Fixed exactly as intended, nothing partial about it.

**The price paid — measured, not assumed:**

The aggregate cost moved: p50 **$0.000738 → $0.000965** (+30.7%), max
**$0.001641 → $0.00175** (+6.6%), step efficiency mean **1.55 → 1.85**.
But the real story is in two individual runs, isolated cleanly:

- **`hard_06`, same exact 4-lap tool sequence both times** (`search_docs` →
  `check_deprecation` → `get_openapi_spec` → answer): baseline **7,863**
  tokens, mitigated **8,656** tokens — **+793 tokens** for carrying the added
  system-prompt paragraph across every one of those 4 resent laps. Baseline
  was already within 137 tokens of the 8,000 `max_tokens` ceiling; the added
  793 tokens push it over, and the run now returns `budget_exceeded` with no
  answer at all — outcome flips PASS → FAIL.
- **`medium_07`:** baseline needed only 2 tool calls (`check_deprecation`,
  `get_openapi_spec`, no `search_docs` — it didn't need it, both
  api_version/endpoint were already stated in the question) and finished at
  3,108 tokens. Mitigated, it took an extra, unneeded `search_docs` call
  first, then the other two — one whole additional lap — landing at 8,586
  tokens, again over budget. The mitigation's language didn't just add
  per-lap overhead here; it nudged the model into a redundant safety-search
  it didn't take before.

**This is a genuine regression, not a rounding error**: both of these were
`clean_pass` in the baseline and are now `budget_exhausted_no_answer`.

## 7. Regression check — all modes, honestly

| Mode | Before | After | Verdict |
|---|---|---|---|
| `incomplete_grounding` | 2 | **0** | ✅ fixed — the target |
| `budget_exhausted_no_answer` | 2 | **4** | ❌ **worse** — `medium_07` and `hard_06` newly landed here |
| `hallucinated_argument` | 0 | 0 | unchanged |
| `no_tool_used` | 0 | 0 | unchanged |
| `search_thrash_loop` | 0 | 0 | unchanged |
| `clean_pass` | 6 | 6 | unchanged in count, but **not the same 6 questions** — `easy_02`/`medium_01` moved in, `medium_07`/`hard_06` moved out |

No brand-new failure mode was created — every mitigated-run result still
falls inside the existing taxonomy. But one existing mode, the one that
matters most for actually shipping an answer to the user, got worse: net
**outcome_pass_rate dropped from 0.8 to 0.6**. Trajectory correctness
improved; whether a user gets an answer at all got worse for two previously-
fine questions. That trade-off is the whole point of measuring the price
instead of asserting the mitigation is free — a real engineer would follow
this up by raising `max_tokens` slightly (untested here, since that would be
a second mitigation) rather than shipping this prompt change alone.

## 8. Bonus (prompt injection) — not attempted

Out of scope for this pass given time. Flag if you want it built: plant an
injected instruction inside a `search_docs`/`get_openapi_spec` tool result
(the docs corpus doesn't currently have an attacker-controlled field to hide
one in, so this would need a small synthetic "community comment" chunk
added to the index), verify the unmodified agent obeys it, then add
output/tool-output sanitization and re-attack, re-running
`trajectory_eval.py` to measure that defense's cost the same way the main
mitigation's cost was measured above.

## 9. Files

| File | What it is |
|---|---|
| `trajectory_specs.py` | ground truth: required/allowed tools, valid sequences, min steps, valid (version, endpoint) pairs — written before any run |
| `failure_modes.py` | the 6-mode taxonomy + deterministic classifier |
| `trajectory_eval.py` | runs the 10 questions, computes all 4 trajectory numbers, the gap, and per-mode counts |
| `trajectory_results_baseline.json` / `trajectory_results_after.json` | full per-question detail, both runs, including complete step logs |
| `agent.py` | +`SYSTEM_PROMPT_MITIGATED` and the `mitigated` flag on `run_agent()` — the one change |
| `week8_report.md` | this file |
