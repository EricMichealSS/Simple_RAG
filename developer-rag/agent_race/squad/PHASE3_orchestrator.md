# Phase 3: The orchestrator

**Done:** 2026-10-04. Code: `orchestrator.py`.

- Manager = ReAct loop whose only tools are `ask_version_worker` and `ask_code_worker`. It has no docs tools. Its prompt lists the known versions/endpoints (like the single agent's enums) and requires `api_version` + `endpoint` on delegations.
- Ceilings identical to the single agent (protocol): 60,000 tokens, $0.05, 120 s, 8 manager laps, plus 6 delegations. Checked before every model call, including inside workers.
- NO failure handling and no failure instruction in the prompt: what it does on a 500 is the measurement.
- Hand-off labels on every model call (e.g. `user -> orchestrator`, `orchestrator -> code_worker (task in)`, `version_worker -> orchestrator (results read)`) so the log can attribute tokens.

## Smoke test (before freezing, one question)
Question: assignee replacement in 2025-06-01 create_issue. 4 model calls, 2,948 tokens, 1 delegation, correct answer (`assignees` array). This confirmed the wiring only; the prompts were frozen after this and not tuned on race results.

## Edge cases handled
Malformed tool arguments or unknown tool name -> status 400 to the manager; delegation cap -> status 429; ceiling hit -> `budget_exceeded` with the reason, same as the single agent.
