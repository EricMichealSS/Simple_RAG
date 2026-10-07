# Phase 2 — The MCP Agent (Host + Client), Connected to One Server

## What this phase did
Built `mcp_agent.py`: the same ReAct loop shape, budgets, and system-prompt style as the original `agent_race/agent.py`, but with `from tools import TOOL_SCHEMAS, TOOL_IMPLS` replaced entirely by a live MCP connection. This file plays both the **host** role (owns the loop, makes the actual LLM calls to Groq) and the **client** role (speaks MCP to servers) at once — a common simplification when there's only one application involved, and worth naming explicitly since the brief treats host/client as conceptually separate roles.

## Files created
- `mcp_agent.py` — the agent itself, runnable standalone as `python3 mcp_agent.py "question"`
- `mcp_config.json` — the one file that says which servers to connect to (currently just `docs-search`)

## How discovery actually works, mapped to the code

`MCPToolRouter.connect_all()`:
1. Reads `mcp_config.json`'s `mcpServers` dict.
2. For each entry, spawns that server as a subprocess (`StdioServerParameters(command=..., args=...)` + `stdio_client`) and opens an `mcp.ClientSession` over its stdin/stdout.
3. Calls `session.initialize()` — the JSON-RPC handshake.
4. Calls `session.list_tools()` — this is the actual discovery step. Nothing about `search_docs`/`get_openapi_spec`/`check_deprecation` is written anywhere in this file; their names, descriptions, and parameter schemas all come back from this one live call.
5. Converts each discovered MCP `Tool` into the OpenAI-style function-calling schema (`_mcp_tool_to_openai_schema`) and remembers which server owns which tool name (`tool_owner` dict), so a later tool call can be routed to the right subprocess.

The rest of the loop (budget checks, message accumulation, deciding whether the model's response is a tool call or a final answer) is intentionally almost line-for-line identical to `agent_race/agent.py` — the only structural difference is that `TOOL_IMPLS[name](**args)` became `await router.call(name, args)`, and the whole function had to become `async` because the MCP SDK's client session is async.

## Real edge case found and fixed

First real run produced a broken tool result for `search_docs` specifically (but not for `get_openapi_spec`/`check_deprecation`):

```
'result_preview': '{\'raw\': \'{\\n  "text": "Example\\\\ncurl -L -X POST..."'
```

Falling into the `{"raw": ...}` fallback means the code failed to parse the tool's result as JSON. Root cause, found by calling `search_docs` directly through a real `ClientSession` and inspecting the raw content blocks: **when a tool returns a Python `list`, FastMCP emits one separate `TextContent` block per list item — not one JSON array.** `get_openapi_spec`/`check_deprecation` return `dict`s, which FastMCP always wraps as exactly one text block, so this never showed up until the one list-returning tool was actually exercised.

Fix (`MCPToolRouter.call()`): parse each text block independently; if there's exactly one, unwrap it back to that single object (matching a dict-returning tool); if there's more than one, reassemble them into the list they came from (matching a list-returning tool). Verified directly afterward — `search_docs` now returns a genuine Python `list` of `dict`s, exactly matching what the original `tools.py` function returned before any of this MCP wrapping existed.

This is exactly the kind of thing "check the raw messages once so MCP stops being a mystery" (from the brief's own "what you'll learn" list) is warning you not to skip — the bug was invisible from the outside (the agent still produced a plausible-looking, if slightly off, tool result) and only became obvious by looking at the actual raw protocol content.

## Verified end to end
Two real questions run through the full agent: one that only needed `get_openapi_spec` (correct, 2 iterations, 2,534 tokens), and one that needed `search_docs` first (correct, and confirmed via a targeted follow-up call that the parsed result is now a proper list of chunk dicts, not a broken string).

## Outcome
Tool discovery confirmed real: **3 tools discovered** (`search_docs`, `get_openapi_spec`, `check_deprecation`), all correctly attributed to the `docs-search` server, none hard-coded anywhere in `mcp_agent.py`. Moving to Phase 3: building the second server.
