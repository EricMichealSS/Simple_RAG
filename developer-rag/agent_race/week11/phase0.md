# Phase 0 — Repository Discovery and Baseline

**Status: COMPLETED**

## Objective

Understand the real architecture before touching anything; protect existing work; establish
what can and cannot actually be executed in this environment.

## Why needed

The brief is explicit: "Do not invent baseline results if tests cannot run. Record the exact
blocker." This phase exists to find that blocker before any later phase could be tempted to
paper over it.

## Starting state

`git status --short` (run before any Week 11 file was touched):
```
?? .claude/skills/
?? AGENTS.md
?? CLAUDE.md
?? developer-rag/After_week10_full_workflow.md
?? developer-rag/agent_race/squad/mcp_tool_routing_upgrade.md
```
All untracked, all prior-session documentation. No uncommitted source edits existed. Nothing
was reset, stashed, or discarded.

`git log --oneline -5`:
```
91c1f03 week 10 Enhancement changes.
080e2f1 week 10 implementation and local files from mac
38ece70 Week 9 mcp integration
b48cf3e Week 7 fine tune
37fb5ee Week 8 trajectory implementation.
```

## Work performed

1. Read the three supplied Week 11 assignment files directly:
   `Week11_Module6_Production_Task_Brief 1.docx` (extracted via its `word/document.xml`,
   since the Read tool cannot parse binary .docx — a small PowerShell script unzipped it and
   stripped XML tags), `W11-Task-Set-E Refined.md`, `W10-Task-Set-E.md`.
2. Confirmed Track E = "Developer documentation — Find the code sample that used a
   deprecated method" from the docx's own track table.
3. Disambiguated which system/eval-suite "Week 11" and "existing Week 6 eval suite" refer to
   in THIS repo — see `tool_selection.md` Section 1 for the full reasoning; concluded
   `agent_race/` (not the main RAG app).
4. Inspected `agent_race/agent.py`, `tools.py`, `pricing.py`, `questions.py`, `grader.py`,
   `trajectory_eval.py`, `compare_one.py`, `budget_demo.py`, `agent_optimized_experiment.py`,
   `squad/metering.py`, `squad/protocol.md`, `taxonomy.md` directly (full file reads, not
   excerpts) to confirm the real architecture rather than relying on summary memory.
5. Located real historical result data already on disk: `race_results.json`,
   `race_summary.json`, `trajectory_results_baseline.json`,
   `trajectory_results_after.json`, `trajectory_results_optimized.json` — all genuine prior
   run outputs, used throughout later phases as REAL evidence rather than invented numbers.
6. Attempted to run the existing test/eval commands and audited the environment:
   ```
   py -c "import mcp"               -> ModuleNotFoundError
   py -c "import openai"            -> ModuleNotFoundError
   py -m pip freeze | grep ...      -> no mcp/openai/chromadb/sentence-transformers installed
   ls developer-rag/.env            -> No such file
   py -0p                            -> only Python 3.14 found, no project venv
   ```

## Baseline test/evaluation record — the exact blocker

**Blocker: this machine has none of `openai`, `chromadb`, `sentence-transformers`, or
`python-dotenv` installed, and no `.env` file with a real `GROK_API_KEY`.** `agent.py`
cannot even be imported (`from openai import OpenAI` fails immediately), so no live
evaluation run, no live Groq call, and no live Chroma query could be executed in this
session. The project's own commit history ("week 10 implementation and local files from
mac") confirms it was built and last run on a different machine.

This is recorded here honestly, per the brief's own instruction, rather than fabricating a
baseline pass/fail count. The REAL historical result files listed above (genuine past runs
on the live API) are used as the baseline evidence for "current suite state" throughout
later phases instead.

## Minimum instrumentation identified as needed for Week 11

1. A `trace_id`-bearing, timestamped log entry per model/tool call in `agent.py` (did not
   exist before this pass).
2. A deterministic (non-LLM) check for "does the answer use a forbidden/deprecated term"
   (did not exist in this track).
3. A way to exercise `agent.py`'s REAL control flow without live network/ML dependencies, so
   RED/GREEN evidence could be produced honestly in this environment — resolved in Phase 5
   via `week11/fakes.py`.

## Acceptance criteria — verified

- [x] Architecture understood from direct source reads, not assumption.
- [x] The track's own "Week 6" eval suite located and confirmed (`questions.py`/`grader.py`).
- [x] Existing uncommitted work and git history confirmed untouched.
- [x] The environment blocker is named exactly, not glossed over.
