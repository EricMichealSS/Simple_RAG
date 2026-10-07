# Phase 5 — Capturing and Annotating the Raw Protocol Exchange

## What this phase did
Captured the actual bytes exchanged between a client and the real, unmodified `package-registry` server for the 3 required phases: `initialize`, `tools/list`, `tools/call`. Saved to `wire.json`, with hand-written annotations on every top-level field.

## Why a hand-rolled client instead of the SDK's `ClientSession`
`mcp.ClientSession`/`stdio_client` already parse each JSON-RPC line into typed Python objects (`JSONRPCMessage` etc.) before handing it to application code — reasonable for building an agent, but it means anything captured *through* that layer would technically be a re-serialization of an already-parsed object, not the literal wire content. `wire_capture.py` instead opens the real server subprocess directly (`subprocess.Popen`, unmodified `package_registry_server.py`) and speaks raw newline-delimited JSON-RPC over its actual stdin/stdout pipes by hand — the same protocol, but captured at the point closest to the actual bytes.

## What got captured (real, not fabricated)

**`initialize`** — request states the protocol version (`2025-11-25`, from `mcp.types.LATEST_PROTOCOL_VERSION` in the installed SDK) and empty client capabilities; the REAL server responds with its own negotiated capabilities (`prompts`, `resources`, `tools` — each with a `listChanged` flag) and `serverInfo: {"name": "package-registry", "version": "1.30.0"}` — that name comes directly from `FastMCP(name="package-registry")` in the server file, confirming this is the real running server, not a mock.

**`notifications/initialized`** — sent immediately after, correctly has no `id` field (it's a notification, not a request) and receives no response, per protocol.

**`tools/list`** — the real response lists all 3 `package-registry` tools with their actual `inputSchema` (auto-generated JSON Schema from the Python type hints in `package_registry_server.py`).

**`tools/call`** — a real call to `check_package_deprecated(name="octokit", version="2.4.0")`, returning the actual deprecation note from `package_registry_data.json`, wrapped in one `text` content block, with `isError: false` at the protocol level (the call succeeded; the fact that the package IS deprecated is an application-level detail inside that text, not a protocol error).

## The required one-line statement

> This entire initialize → tools/list → tools/call exchange happens locally, over a subprocess pipe, between this script and `package_registry_server.py`, with no LLM involved at any point; the actual model call (deciding to invoke `check_package_deprecated` in the first place) happens separately, on the host side in `mcp_agent.py`, as a normal HTTPS request to Groq's API — the server never sees that call and never makes one itself.

This is the architectural boundary the brief calls out as the most commonly-violated rule this week: an MCP server that ever called an LLM itself would have broken this separation entirely.

## Outcome
`wire.json` is genuine captured protocol traffic, annotated field by field. Moving to Phase 6: the before/after error-recoverability transcript.
