# Week 11 Plan — Production Observability, Cost & the Failure-to-Test Loop

Track E (Developer Documentation), continuing directly from Weeks 7–10's `agent_race/`
work (single agent vs. workflow, trajectory evaluation, MCP, multi-agent squad + A2A).

## 1. Assignment summary

Find a planted/real incident ("a code sample used a deprecated method") using logs alone in
under 5 minutes, extract its full trace (per-span latency/tokens/cost/prompt version/context
IDs), turn it into a permanent regression case, fix the root cause, prove RED→GREEN with
real suite pass counts, break the incident's cost down by stage, and determine what breaks
first at 10x traffic. Deliverables: `drill.md`, `trace.json`, a new eval case in the existing
suite, `test_results/red.txt` + `green.txt`, `cost_by_stage.md`, `tenx.md`, a prompt-version
bump, and a two-line canary/rollback plan.

## 2. Existing architecture summary (confirmed by inspection, not assumed)

- **Stack:** Python. No LangChain/CrewAI/LangGraph anywhere in `agent_race/` — every agent
  loop (`agent.py`, `workflow.py`, `orchestrator.py`, `orchestrator_mcp.py`,
  `mcp_agent.py`) is hand-built. LLM calls go through the `openai` SDK pointed at Groq's
  OpenAI-compatible endpoint (`base_url=https://api.groq.com/openai/v1`).
- **Retrieval:** ChromaDB (`PersistentClient`, local, cosine similarity), embeddings via
  local `sentence-transformers` (`BAAI/bge-small-en-v1.5`), no per-query provider cost.
- **Tools:** `agent_race/tools.py` — `search_docs`, `get_openapi_spec`, `check_deprecation`,
  shared by the single agent and (duplicated in `agent_race/mcp/docs_search_server.py`) the
  MCP track.
- **"Production" system, per the Week 10 pre-registered verdict:** the single agent
  (`agent.py`) — the squad was explicitly KILLed (tied pass rate, 1.2–1.3x cost, and a
  "lied" failure behavior; see `squad/verdict.md`). Week 11 work targets `agent.py`, not the
  squad, because that is what the project's own evidence says is actually in production.
- **Existing "Week 6" evaluation suite for this track:** `agent_race/questions.py`'s 10
  race questions + `agent_race/grader.py`'s PASS/FAIL judge — confirmed by Week 10's own
  Task Set E brief, which literally calls these "the Week-6 eval cases." (The main RAG app's
  separate `developer-rag/eval/` 26-case suite, also labeled Week 6, belongs to a different
  corpus and a different app — see `tool_selection.md` for how this was disambiguated.)
- **Existing observability:** `agent.py`'s own `log` list (per-iteration `llm_call`/
  `tool_call` events with tokens, no timestamps, no trace ID, no cost-per-call, no context
  IDs beyond a 200-char truncated preview); `squad/metering.py`'s `Tracer` (trace_id,
  per-call token/cost/latency, JSONL sink) is considerably more production-shaped but was
  never wired into `agent.py` itself.
- **Existing cost accounting:** `agent_race/pricing.py` — explicit per-model, per-million-
  token rate table, `cost_usd()` pure function, already separates input/output tokens.

## 3. Current baseline and repository state

`git status --short` at the start of this pass showed only untracked documentation files
from prior weeks' work (`.claude/skills/`, `AGENTS.md`, `CLAUDE.md`,
`developer-rag/After_week10_full_workflow.md`,
`developer-rag/agent_race/squad/mcp_tool_routing_upgrade.md`) — no uncommitted source changes
existed before this pass began. Nothing was reset, discarded, or overwritten.

**Environment blocker (see `phase0.md` for the full audit):** this session runs on a Windows
machine with Python 3.14 and none of `openai`, `chromadb`, `sentence-transformers`, or
`python-dotenv` installed, and no `.env` file (the project's commit history shows it was
built on a Mac). Live end-to-end execution against the real Groq API and the real `race_docs`
Chroma collection was not possible. All RED/GREEN evidence and the extracted trace were
produced by running the REAL `agent.py`/`tools.py` source files with the external I/O
boundary (OpenAI client, Chroma, embeddings, `.env`) replaced by deterministic fakes seeded
with REAL historical data already on disk (`trajectory_results_baseline.json`,
`trajectory_results_after.json`) — see `week11/fakes.py`'s module docstring for exactly what
is REAL vs. SYNTHETIC in every value used.

## 4. Known gaps (confirmed, not assumed)

1. `agent.py`'s Week 8 mitigation (`SYSTEM_PROMPT_MITIGATED`, the `mitigated` flag) existed
   in code but defaulted to `False`, and no production-shaped caller ever passed `True` —
   confirmed by grepping every `run_agent(` call site in the repo (`phase5.md`).
2. No `trace_id`, timestamp, per-call cost, or context-ID field existed anywhere in
   `agent.py`'s own logging before this pass (`phase1.md`/`phase2.md`).
3. No deterministic (non-LLM) assertion existed in this track for "does the answer use a
   forbidden/deprecated term" — only the LLM judge (`grader.py`) existed, which the brief
   explicitly says to prefer deterministic checks over for known forbidden methods.
4. The distance-metadata bug (`taxonomy.md` Mode 5) and the Groq 413/TPM failure mode
   (`taxonomy.md` Mode 1, `protocol.md` Amendments A1/A2) were already documented in prior
   weeks and directly inform `tenx.md`'s conclusion — not re-discovered here, reused as
   evidence.

## 5. Selected tools and reasons

See `tool_selection.md` for the full table. Headline decision: **no new tool or platform was
installed.** Logging/tracing reuses and extends `agent.py`'s own log list plus the pattern
already proven in `squad/metering.py`; evaluation reuses `questions.py`/`grader.py`;
cost accounting reuses `pricing.py`. OpenTelemetry, Phoenix, and Langfuse were all
considered and explicitly rejected for this pass — see `tool_selection.md` for why.

## 6. Proposed architecture (what changed, net)

```
agent.py (FIXED defaults + PROMPT_VERSION + timestamps + result_full in log)
  |
  +-- questions.py (+ get_regression_cases(), isolated from get_race_questions())
  +-- deprecation_assertions.py (NEW -- deterministic forbidden-term check)
  +-- week11/
        fakes.py              -- I/O-boundary fakes for this offline environment
        test_regression_w11.py -- the permanent regression test
        extract_trace.py      -- incident -> trace.json extractor
        trace.json, drill.md, cost_by_stage.md, tenx.md, deployment_plan.md
        test_results/red.txt, green.txt
        phase0.md .. phase7.md, tool_selection.md, final_review.md (this document set)
trajectory_eval.py (companion fix: pin parallel_hint=False, compact_history=False so its
                     own baseline/after A/B test stays isolated to the `mitigated` variable,
                     unaffected by agent.py's new defaults)
```

## 7. Phase-by-phase plan and status

| Phase | Title | Status |
|---|---|---|
| 0 | Repository discovery and baseline | COMPLETED |
| 1 | Observability schema design | COMPLETED |
| 2 | Logging, tracing, error handling implementation | COMPLETED (scoped — see phase2.md) |
| 3 | Incident investigation drill | COMPLETED |
| 4 | Trace extraction and cost accounting | COMPLETED |
| 5 | Regression test, root cause, fix, RED/GREEN | COMPLETED |
| 6 | Cost by stage and 10x analysis | COMPLETED |
| 7 | Deployment plan, final integration, review | COMPLETED (deployment itself NOT executed — see phase7.md) |

## 8. Expected files created or modified

See `final_review.md` for the complete, categorized file list with one-line purpose each.

## 9. Dependencies and configuration

Zero new third-party dependencies added. `week11/fakes.py` is pure Python stdlib
(`sys`, `types`). No `requirements.txt` change was needed or made.

## 10. Testing strategy

Deterministic, dependency-free regression test (`week11/test_regression_w11.py`) run twice
(RED before the fix, GREEN after), both real executions captured verbatim in
`test_results/`. Full-suite regression risk assessed against REAL historical data
(`trajectory_results_optimized.json`, which already measured the exact fixed configuration
across all 10 original questions) rather than re-running the live suite, which this
environment cannot do (see Section 3).

## 11. Security and privacy considerations

No API keys or secrets were committed; the placeholder key in `test_regression_w11.py` is a
literal string (`"test-only-placeholder-not-a-real-key"`), never read by any real network
call because the OpenAI client itself is faked. No personal data appears in any fixture — all
fixture content concerns GitHub API/Swagger Codegen documentation facts.

## 12. Cost-measurement strategy

Reuse `pricing.py`'s existing rate table; separate input/output tokens throughout; label
every number in `cost_by_stage.md`/`tenx.md`/`trace.json` as REAL, CALCULATED, ESTIMATED, or
UNAVAILABLE at the point of use rather than presenting one undifferentiated number.

## 13. Risks and mitigations

- **Risk:** flipping `agent.py`'s defaults silently changes other scripts' behavior.
  **Mitigation:** every `run_agent(` call site in the repo was grepped and checked;
  `trajectory_eval.py` was patched to pin its own three flags explicitly so its controlled
  A/B experiment stays isolated (see `phase5.md`).
- **Risk:** mitigation alone (without `parallel_hint`/`compact_history`) regresses other
  questions (real measured drop, 0.8→0.6 pass rate). **Mitigation:** all three defaults were
  changed together, matching the already-proven `agent_optimized_experiment.py`
  configuration, not just the one flag directly implicated in the incident.
- **Risk:** offline environment could tempt fabricated results. **Mitigation:** every
  fake value is sourced from real historical JSON already in the repo, with provenance
  comments in `fakes.py`, and the one deliberately synthetic value (the bad answer's exact
  wording) is labeled as such everywhere it appears.

## 14. Acceptance criteria / Definition of done

See the Week 11 brief's own checklist — tracked item-by-item in `final_review.md`.

## 15. Actual progress and outstanding blockers

All required deliverables exist and are evidence-backed. The one real, disclosed blocker is
that no live Groq/production execution was possible in this session's environment — see
Section 3 and `phase0.md`. The deployment plan is written but not executed (no authorization
requested or given to deploy or incur API cost). Both are stated plainly in `final_review.md`
as outstanding, not glossed over.
