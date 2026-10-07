# Phase 1 — Building the `docs-search` MCP Server (our own server)

## What this phase did
Took the same 3 tools that already existed as plain Python functions in `agent_race/tools.py` (`search_docs`, `get_openapi_spec`, `check_deprecation`) and re-exposed them as a real MCP server using `FastMCP`, pointed at the exact same existing ChromaDB collection (`race_docs`) — no re-ingestion, no new data.

## Files created
- `docs_search_server.py` — the server itself, 3 `@mcp.tool()`-decorated functions, run standalone as `python3 docs_search_server.py` (speaks stdio by default, expects to be launched as a subprocess by a client, not run interactively)
- `verify_docs_search_server.py` — a real MCP client script used to test the server end to end before building anything on top of it (kept as evidence, not deleted)

## How it works, briefly
`FastMCP(name="docs-search")` creates the server object; `@mcp.tool()` on a function turns its type hints into a JSON Schema automatically (this is why `api_version`/`endpoint` are typed as `Literal["2022-11-28", "2025-06-01", "2.x", "3.x"]` etc. instead of plain `str` — FastMCP reads that and generates an `enum` constraint in the schema, the same guarantee the original `tools.py` built by hand). `mcp.run(transport="stdio")` at the bottom starts the server listening on stdin/stdout for JSON-RPC messages.

## The two Week 9 Requirement 5 rewrites

**Docstring-as-prompt:** `check_deprecation`'s docstring was rewritten from a one-line description into guidance with a worked example (when to call it vs. `get_openapi_spec`, and the "always pass the newer version" rule with an explanation of why the older version reliably returns "not found"). This docstring IS what an MCP client sees as the tool's description — it's not internal documentation, it's the actual prompt content the model reads to decide whether to call this tool.

**Recoverable errors** — see the edge case below for why the *target* of this rewrite changed partway through the phase.

## A real edge case found by testing, not assumed

The original plan was to make a bad `api_version` string (e.g. `"v4"`) return a rich, custom recoverable message via `_version_error()`. Testing this directly (`verify_docs_search_server.py`) showed it doesn't work the way planned:

```
--- invalid api_version (recoverable error test) ---
Error executing tool get_openapi_spec: 1 validation error for get_openapi_specArguments
api_version
  Input should be '2022-11-28', '2025-06-01', '2.x' or '3.x' [type=literal_error, input_value='v4', input_type=str]
```

Because `api_version` is typed as `Literal[...]`, FastMCP validates the incoming arguments against that enum **before** the function body ever runs — a bad value never reaches `_version_error()` at all. It gets caught at the protocol layer with a generic pydantic error instead. Arguably that's a *stronger* guarantee than an app-level recoverable message (the bad call is prevented outright, not just handled gracefully after the fact) — but it means the rich message that was written for that path is currently dead code, reachable only if someone calls the undecorated Python function directly, not through the real MCP protocol.

**What's actually reachable and became the real evidence instead:** `check_deprecation` called with a syntactically-*valid* but wrong-direction version (e.g. the older `2022-11-28` instead of the newer `2025-06-01`, for a comparison question). This can't be caught by schema validation — both are legal enum members — so it really does reach the function body:

```
--- check_deprecation, WRONG direction (older version) ---
{
  "found": false,
  "note": "No recorded change for (2022-11-28, create_issue). This usually means either
  (a) you passed the OLDER of two versions being compared -- try the newer one instead, or
  (b) nothing changed for this endpoint between versions."
}
```

Compare against the original Week 7/8 version in `tools.py`, which for the exact same wrong-direction call just says:
```
{"found": False, "text": None, "note": "No recorded change/deprecation for this version+endpoint."}
```
True, but doesn't tell you *why* or what to try instead. **This pair (old generic note vs. new directional-hint note) is this week's official recoverable-error evidence** — see Phase 6 for the full before/after model transcript built from it.

## Outcome
- Server verified working standalone: discovery lists all 3 tools correctly, valid calls return real data from the actual index, and the reachable error path (wrong-direction `check_deprecation`) returns the intended richer message.
- The unreachable helpers (`_version_error`, `_endpoint_error`) were kept in the file, but the code comment above them now says plainly why they're not the official evidence, instead of leaving that undiscovered.
- Moving to Phase 2: building the MCP-speaking agent that connects to this server.
