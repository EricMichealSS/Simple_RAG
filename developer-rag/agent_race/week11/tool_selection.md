# Tool Selection — Week 11

For every candidate, the question asked was the brief's own: does the EXISTING
architecture, a real measured need, or a hard course requirement justify adding this,
versus extending what is already here.

## 1. Which "Week 6 eval suite" and which "app" — disambiguated first

Two candidate systems exist in this repo under a "developer documentation" label:

1. `developer-rag/app.py` + `eval/` — the live Streamlit RAG app, its own 26-case Week 6
   eval suite (`eval/cases.json`), v2/v3 SDK docs.
2. `agent_race/` — the GitHub API / Swagger Codegen migration-assistant track built across
   Weeks 7–10 (agent vs. workflow, trajectory eval, MCP, multi-agent squad).

**Chosen: #2, `agent_race/`.** Reasons, in order of weight:
- The Week 11 master prompt's own framing says "the previous week's work focused on MCP, a
  multi-agent orchestrator, specialist workers, and agent-to-agent communication" — that is
  an exact description of Weeks 9–10, i.e. `agent_race/`, not the main app.
- `W10-Task-Set-E.md` (this track's own Week 10 brief, read directly) explicitly calls
  `agent_race/questions.py`'s 10 race questions "the Week-6 eval cases" in writing — direct
  repo-adjacent evidence for which suite "Week 6" refers to in this track's own vocabulary.
- `agent_race`'s own `squad/protocol.md` makes the same disambiguation explicit: "Note: Week
  10 text says 'Week-6 eval cases'; the Week 6 set was for the earlier RAG corpus, so the
  agent-corpus race set from Week 7 is the only set the single agent can answer."
- `agent_race`'s existing tooling (`check_deprecation`, `tools.py`'s deprecation-aware
  retrieval, `metering.py`'s Tracer) is a direct, purpose-built fit for a
  "deprecated-method-in-a-code-sample" incident; the main app's docs do not model API
  deprecation at all.

## 2. Logging and tracing

**Chosen: extend `agent.py`'s own existing `log` list in place** (new keys: `timestamp`,
per-call `cost_usd`, `result_full`), rather than adopting `squad/metering.py`'s `Tracer`
class wholesale or installing an external platform.

| Option | Verdict | Why |
|---|---|---|
| Extend `agent.py`'s existing log list (chosen) | **Selected** | Zero new dependency, additive-only keys (nothing existing reads/validates an exact key set — confirmed by inspection), smallest possible blast radius for a "don't break anything" constraint. |
| Adopt `squad/metering.py`'s `Tracer` in `agent.py` | Considered, deferred | Already built, already proven (JSONL sink, trace_id, per-call cost) — the better long-term answer, but wiring it into `agent.py` means every one of `agent.py`'s existing callers (`trajectory_eval.py`, `compare_one.py`, `budget_demo.py`, `squad/run_squad_race.py`) would need a `Tracer`/`sink` parameter threaded through, a larger surface change than this pass's scope justified. Named as the concrete next step in `final_review.md`. |
| OpenTelemetry | Rejected for now | No existing OTel usage anywhere in this repo; would be the first new dependency in a project that has deliberately avoided frameworks throughout (`agent.py`'s own docstring: "No framework... built from scratch to understand exactly what's happening"). The brief explicitly says "do not add an external logging service just to emit JSON logs" — the same reasoning applies to adding a tracing SDK when plain structured dicts already carry every required field. |
| Arize Phoenix / Langfuse | Rejected for now | Both require running an additional local service/UI; this environment has no network access and could not even install `openai` (see `phase0.md`), so standing up a second service was not attempted. The brief explicitly says not to install Phoenix and Langfuse together, and to prefer the simpler solution when a JSONL log is sufficient — it is sufficient here. |

- **Purpose:** per-request/per-span observability sufficient to answer "what happened, what
  did it cost, which prompt version produced it."
- **Free/open-source vs. paid:** fully free — no new package.
- **Network/credentials required:** none.
- **Tested:** `week11/test_regression_w11.py` and `week11/extract_trace.py`, both run for
  real in this session (see `phase2.md`, `phase4.md`).
- **Connects to existing app:** directly inside `agent.py`'s own loop — no adapter layer.

## 3. Evaluation and testing

**Chosen: `questions.py` + a new, separate `get_regression_cases()` function, plus a new
`deprecation_assertions.py` deterministic-check module.** No pytest dependency added —
`week11/test_regression_w11.py` is a plain script with its own `main()`/exit-code
convention, runnable with the stdlib alone, because `pytest` is not installed in this
environment and installing it was not necessary to get a real, deterministic pass/fail
signal.

- **Why not append the new case directly into `get_race_questions()`'s `SELECTED` list:**
  `trajectory_specs.py` has one hand-written spec per `race_id`, and `trajectory_eval.py`
  assumes `get_race_questions()` returns exactly those specced cases — silently growing it to
  11 would break trajectory evaluation for the new case (no spec) without anyone asking for
  that. A separate function keeps every existing caller of `get_race_questions()` untouched.
- **Why a new deterministic-check module instead of extending `grader.py`:** the brief
  explicitly prefers deterministic checks for known forbidden methods over LLM judgment.
  `developer-rag/eval/assertions.py` already established this exact pattern (deterministic
  checks alongside an LLM judge) for the main app; `deprecation_assertions.py` is the same
  pattern applied to this track, which had no deterministic-check module at all before now.

## 4. Model and provider

**Unchanged.** `openai/gpt-oss-120b` via Groq remains the model for every role, per
`pricing.py` and `protocol.md`. No model-routing change was made in response to the
incident — the brief explicitly warns against changing models as a first response to a
correctness bug, and the real root cause here was a prompt/default-flag issue, not a model
capability issue (the SAME model, given the mitigated prompt, already produces the correct
grounded answer — see `phase5.md`).

## 5. Cost measurement

**Chosen: `pricing.py`'s existing `cost_usd()` function, unmodified**, applied per-call
inside `agent.py`'s log (new `cost_usd` key per `llm_call` event) and aggregated in
`trace.json`/`cost_by_stage.md`. Already separates input/output tokens; already versioned by
model name in its own rate table (`PRICE_PER_MILLION`). No change was needed here beyond
calling the existing function one more place (per-call, not just cumulative).

## 6. Explicitly rejected, with reasons (brief Section 3.6)

LiteLLM, a new vector database, Redis, a message broker, Kubernetes, a cloud platform, or
another agent framework: none were considered necessary or even relevant to this incident.
The root cause was a default-argument bug in a hand-built ReAct loop, not an infrastructure
scaling problem — see `tenx.md` for the actual, measured, un-glamorous bottleneck (Groq
free-tier rate limits, already hit at 1x volume).
