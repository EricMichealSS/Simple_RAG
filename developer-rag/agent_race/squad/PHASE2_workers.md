# Phase 2: The two workers

**Done:** 2026-10-04. Code: `workers.py`.

| Worker | Tools | Job |
|---|---|---|
| version_worker | search_docs, check_deprecation | what changed / was deprecated, and finding where a topic lives |
| code_worker | get_openapi_spec | current parameter table and a minimal sample |

- Each is a small ReAct loop (max 8 laps, 30 s timeout) with a narrow prompt and a SUBSET of the single agent's tools. Neither gets all three tools (that would remove the only plausible source of a squad win).
- Contract is HTTP-shaped: `{"status": int, "body": ...}`; status >= 400 is a failed call. Workers run in-process (limitation: network latency between agents is hidden, so the squad's latency is understated).
- Target version never gets lost: the manager must pass `api_version` and `endpoint`, and the worker prompt appends them to its task and tells the worker to state which version its facts come from.
- Workers return only their short report, never the raw tool output, to avoid the over-sharing mistake in the brief.
- Fault injection (`set_fault`) is off by default. A 500 costs no tokens, as with a real down service.

## Edge cases
- A worker asked to use a tool outside its set gets an error as data.
- Timeout, lap exhaustion and ceiling hit return 504 / 504 / 503 instead of hanging.
