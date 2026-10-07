# A2A notes (bonus). Documents only: no live A2A server was built.

## Where the failed case ends in the A2A task lifecycle
Task states in the spec: submitted, working, completed, failed, canceled, rejected, input-required, auth-required.

The `hard_04` run with the persistent 500 should end as **`failed`**, not `input-required`:
- `input-required` is for when the USER can supply what is missing. Here the question already names both versions (2022-11-28 and 2025-06-01), so there is nothing to ask. A 500 is a server fault, not missing information.
- Our manager instead returned `completed` with a wrong answer. That is the real bug: in A2A terms the task finished in a state that claims success after a failed sub-task.
- `input-required` WOULD be right for a question like "is this deprecated?" with no version stated, where the worker cannot proceed without "which SDK version are you on?". That is a different case from this failure.
Lifecycle for the fixed behaviour: submitted -> working -> (version worker 500, bounded retries exhausted) -> failed, with a message "version check unavailable".

## What A2A buys over a plain REST call to the worker (2 lines)
1. A standard, discoverable contract: the AgentCard advertises skills, modes and auth, and a task has defined states (including failed and input-required), so a caller can tell "worker down" from "worker needs more info" without bespoke conventions.
2. Long-running work and structured hand-offs (task ids, status, messages, artifacts) are built in, where a bare REST call only returns a body or an error code.

MCP connects an agent to tools; A2A connects an agent to other agents.
