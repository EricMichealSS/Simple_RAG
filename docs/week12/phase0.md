# Phase 0 — Repository discovery, baseline, plan

Date: 2026-11-02 · Status: **COMPLETE** (decisions received; see `week12_plan.md` §9a)

## Objective
Establish the real state of the repo before touching code: branches, architecture, gaps, tool choices, baseline. Do not invent results.

## Files inspected
Read in full: `developer-rag/{config,app,rag_hybrid,chunkers,ingest_live,taxonomy}.py|md`, `agent_race/{agent,tools,questions,trajectory_specs,pricing,page_metadata.json}`, `agent_race/mcp/{docs_search_server,mcp_agent}.py`, `agent_race/squad/metering.py`, `eval/{assertions,run_eval}.py`, `agent_race/week11/{week11_plan,tool_selection,phase0,test_regression_w11}`.
Header / grep only: `eval/{generate,judge}.py`, `eval/week6_report.md`, `agent_race/{workflow,week8_report}`, `squad/{verdict.md,orchestrator_mcp.py}`, `week11/drill.md`, `ingest*.py`, `agent_race/ingest.py`, `test_*.py`.
Not read: remaining Week 9/10 phase notes, `package_registry_server.py`, `squad_tools_server.py`, `wire_capture.py`, `error_transcript_test.py`, `verify_docs_search_server.py`, benchmark/evaluate scripts, golden-set JSONs, PDFs (until Phase 2).

## Files created
`docs/week12/{week12_plan,architecture_current,gap_analysis,tool_selection,baseline_results,phase0}.md` — docs only.

## Files modified / deleted
None in Phase 0. (After approval: branch `week12_implementation` created from `week11_implementation` @ `5e63530`; git-ignored `.venv/` created. Neither is a tracked change.)

## Commands run and actual results
See `baseline_results.md` §1 for the full table. Summary: 57/57 Python files parse; Week 11 offline regression GREEN (faked I/O); eval data parses (26 cases / 26 labels); secret scan clean; no dependencies/key/index present.

## Data-flow / connections found
Two disconnected stacks (A: `developer_docs`; B: `race_docs`). Details and diagram in `architecture_current.md` §1.

## Tests
No new tests in Phase 0.

## Security and privacy
No secrets read or printed; `GROK_API_KEY` presence was checked without echoing it. Findings recorded: raw Q&A traces tracked in git (`developer-rag/traces.jsonl`), no redaction, no injection defence, uploads share the official collection. Nothing was changed.

## Remaining limitations
- GitNexus graph tools were unusable for discovery (stale index, degraded keyword search); source was read directly. A re-index was started per `CLAUDE.md` (see Phase 1 notes for the result).
- Several Week 9/10 files were only skimmed.
- Historical numbers are single-run, one-machine results and were not re-run.

## Acceptance
All Phase 0 deliverables written; no application code changed; decisions Q1–Q4 answered by the owner; Q5–Q9 open (non-blocking, defaults in plan §9a).

## Next steps
Phase 1 (contracts, trace context, config, LangGraph skeleton) on corpus B, after the environment finishes installing and the `mcp` version question is resolved (see `week12_plan.md` §9a).
