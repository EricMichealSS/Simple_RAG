# Phase 3 — MCP server, allow-listing client, runtime discovery, trace across the hop

Date: 2026-11-02 · **Status: COMPLETE for its scope** (three version-scoped tools, runtime discovery with an operator allowlist, schema-validated calls, timeouts/caps, independent-client test, one trace id across the process boundary). **Not done here:** the graph does not yet *call* `check_symbol` (that is the Phase 6 code-validation node); no model-based tool selection exists (rules only). (No live LLM run existed when this phase was written; one has since — see `phase1.md`'s live-run addendum.)

## Objective
Expose a genuine version-scoped docs capability over MCP, consume MCP tools via runtime discovery under an operator allowlist, and make one trace id survive graph → client → server → tool.

## Files created (all additive; legacy `agent_race/mcp/*` untouched)
| File | Purpose |
|---|---|
| `sdk_engine/mcp_server.py` | FastMCP stdio server `sdk-docs`: `search_docs`, `lookup_changes`, `check_symbol`. **`version` is required on every tool**; bounds live in the tool schemas; output capped; continues the caller's trace from request `_meta`; structured errors with categories; never calls an LLM, no write/delete/shell/file capability |
| `sdk_engine/symbols.py` | Corpus-backed symbol index: `deprecated` / `present_in_docs` / `absent_per_docs` / `not_found` with evidence; never says "removed" |
| `sdk_engine/tool_policy.py` | `ServerConfig` (operator allowlist, optional pinned schema hashes), `build_registry` (discovery ≠ authorization; sanitises names/descriptions, flags instruction-like descriptions), `ToolDecision`, `validate_decision` (allowlist + JSON-Schema + unknown-key rejection + size bound + **caller-owned arguments win**) |
| `sdk_engine/mcp_client.py` | `McpToolClient`: one long-lived task on a dedicated loop thread (anyio requires enter/exit in one task), runtime `tools/list`, per-call timeout, output cap, result contract (`{"ok": bool, …}`), `_meta` trace propagation, minimal explicit subprocess env, `default_server_config` using `sys.executable` |
| `sdk_engine/toolbox.py` | decide → validate → call → parse → **re-check scope client-side**; comparison = union of strict per-version calls; non-security failures raise `ToolFailure` (graph falls back to the in-process retriever) |
| `tests/{raw_mcp_client,fake_mcp_server}.py` + 7 test files | stdlib independent client; hostile fake server; see Tests |
Modified (my own Phase 1/2 files only): `retrieval.py` (`RetrievalScope.endpoints`, `ChromaRetriever.fetch`, `"hash-test"` embedder for tests), `config.py` (env vars), `schemas.py` (`deprecation_facts`, `retrieval_via`), `graph.py` (tools in `Deps`, tool-first retrieve/changelog with fallback, endpoint/product fix), `factory.py` (`LazyRetriever`, MCP client), `cli.py` (`--no-mcp`, prints facts).

## Data flow (one request, tools enabled)
`Engine.ask` → span `request` → `resolve_version` (deterministic) → `route` (rules) → `retrieve`: **rule builds `ToolDecision(search_docs)`** → `validate_decision` against the discovered registry with `enforced={"version": <resolved>}` → `McpToolClient.call` (span `mcp.call`, `_meta` = trace/parent/request ids) → server subprocess (`mcp.tool.search_docs`, filter in the vector query, scope re-checked) → client parses to `Passage`s and **re-checks scope again** → endpoint **boost** → `changelog`: `lookup_changes` (metadata-only fetch + explicit-deprecation facts) → `assess` → `generate` (the single LLM call) → `verify` → `finalize`.

## Commands run and actual results
- Whole suite, `GROK_API_KEY` unset, final state: **272 passed** (268 unit + 4 integration against the real index) in 55 s. Per file (from `pytest --collect-only`): versioning 46, retrieval_scope 37, redaction 33, tool_policy 31, graph 26, symbols 23, protocol_independent 17, answering 15, mcp_client 14, graph_tools 12, trace 12, mcp_graph_e2e 3, integration_real_index 3. (An intermediate run before the last fix showed 271; the number above is the post-fix run.)
- Mutation checks (throwaway plugin, not committed): client-side scope check disabled → exactly the "lying server" test fails; allowlist disabled → 6 tests fail; decision allowed to override `version` → its test fails; always-true absence matcher → 2 symbol tests fail.
- **Real CLI, real index, real MCP server, no key**: `python -m sdk_engine query "On API version 2022-11-28, how do I set the assignee…"` → `retrieval=mcp`, citations only from 2022-11-28, plus *"assignee is deprecated in favor of assignees in 2025-06-01 (newer than your version)"* from `lookup_changes`; migration question → both versions cited; ≈110–230 ms per request after the server is up. The server takes several seconds to start (model load) — paid once per CLI invocation.
- Real-index symbol battery (observed, then encoded as tests): `assignee`@2025 deprecated→`assignees`; `assignee`@2022 present + labelled upgrade note; `type`/`make_latest`/`visibility`@2022 `absent_per_docs` (evidence from the 2025 diff text); `--generate-markdown`@2.x and `asyncio`@2.x `absent_per_docs` (evidence from the 3.x "Compared to v2" text); `assignees` and `maintainer_can_modify`@2022 only `not_found` (weak, says so).

## Tests (what they prove)
`test_mcp_protocol_independent.py` (17): a **stdlib JSON-RPC client that does not import the `mcp` SDK** initialises the real server, lists exactly three tools (each requires `version`; no `path/file/command/url` parameters; `top_k ≤ 10`, `query ≤ 1000`), calls them, sees schema violations rejected, gets structured errors for unsupported versions/unknown endpoints/bad symbols, and proves the trace id and parent span id sent in `_meta` come back in the server's own span file (`service=mcp_server`, parent = client span) with the query text never logged; stdout carries only protocol frames; output cap truncates and says so.
`test_mcp_client.py` (14): against a **hostile fake server** — non-allowlisted tool advertised but refused *before being sent*; instruction-style description flagged; timeout leaves the client usable; 200 KB output refused; non-JSON refused; trace context reaches the server; failed calls become error spans; **the server subprocess does not inherit `GROK_API_KEY`/other secrets**; unstartable server → `McpUnavailable`; default config uses `sys.executable`; two servers offering one tool name → only one callable; concurrent calls; call-after-close is a clean error.
`test_tool_policy.py` (31), `test_symbols.py` (23, one of them integration), `test_graph_tools.py` (12), `test_mcp_graph_e2e.py` (3): decision/allowlist/schema/override rules; symbol statuses on verbatim corpus text; fallback on every non-security failure; **a lying server returning a wrong-version chunk fails closed and is NOT papered over by falling back**; and the end-to-end test where `request → validate → resolve → route → retrieve → mcp.call → [server] mcp.tool.search_docs / lookup_changes → … → finalize` share **one trace id**, every server span's parent is the client's `mcp.call` span, and the query text appears in neither process's logs.

How another developer re-runs the independent-client test: `cd developer-rag && python -m pytest tests/test_mcp_protocol_independent.py -q` (no key, no model download; ~8 s). Manual smoke against the real index: `python tests/raw_mcp_client.py`. This is an independent *client*, written by the same author; it is **not** external fresh-eyes verification.

## Defects found by tests / real runs (all fixed, each with a regression test)
1. **Server hung on its first tool call on Windows.** The first `tools/call` blocked for 120 s. A faulthandler dump showed the event-loop thread stuck loading numpy's native extension (first `import chromadb` inside the tool), while an AnyIO worker thread sat in its stdin read. The legacy Week 9 server avoided this by importing at load. Fix: initialise native-heavy state **before** `mcp.run()` starts reading stdin; failures become a structured `server_unavailable` tool error. *Mechanism is my inference from the stack dump (a handle with a pending synchronous read); the fix is verified, the exact cause is not proven.*
2. **Client accepted a non-contract result.** A tool returning prose was wrapped by FastMCP as `{"result": "…"}` and treated as a valid object. Fix: results must be an object with boolean `ok`, else `malformed_result`.
3. **Absence matcher false positive.** `-g` was reported `absent_per_docs` because it is a substring of `--generate-markdown` next to a "did not exist" phrase. Fix: same boundary discipline as the presence matcher.
4. **Alias words ignored the resolved product.** "On Swagger Codegen 2.x, which flag generates **markdown** documentation?" matched the GitHub endpoint `render_markdown` and abstained for the wrong reason. Fix: detected endpoints are restricted to the resolved version's product.
5. Test-side: output-cap test data was smaller than the cap; a mis-typed counter assertion.

## Security and privacy
Operator-owned allowlist and optional schema pinning; discovery never authorises; tool descriptions are untrusted (sanitised, flagged, never shown to a model in this phase); arguments validated against the *discovered* schema with unknown keys rejected; **the graph owns `version`**; server subprocess gets a minimal explicit environment (no inherited secrets) and `HF_HUB_OFFLINE=1`; scope enforced in the server, re-checked in the client; output/timeouts bounded; error messages carry classes/short redacted text, never request content; spans record keys and sizes, not text.

## Remaining limitations
- `check_symbol` is implemented and tested but **not yet invoked by the graph** (Phase 6 wires it into code validation).
- Tool selection is rules-only; there is no model-proposed `ToolDecision` path yet (the validation gates exist and are tested for when there is).
- `search_docs` ranking inside a version is weak on a tiny pool: the table-of-contents chunk (`p1`) frequently outranks substantive pages (seen in CLI output). Needs a chunk-quality/metadata fix (Phase 2 enrichment) and the Phase 8 eval.
- MCP server start-up costs several seconds per CLI invocation (model load); a long-lived server or HTTP transport would remove it. Not built.
- The in-process fallback and the server share code; a bug in `retrieval.py` affects both.
- Legacy `agent_race/mcp/*` servers and `mcp_config.json` (hard-coded `python3`, unbounded `top_k`, no version scope) are **unchanged and still unsafe as-is** for Week 12 purposes; they are simply not used by the new path.
- Untested: server crash mid-session (client would see `transport_error` per call); Windows-only behaviour on other OSes.

## Acceptance
Accepted for Phase 3's scope. Three required capabilities exist and are callable over MCP; two (`search_docs`, `lookup_changes`) are invoked by the integrated graph, the third by Phase 6.

## Next
Phase 4 — uploads (scoped collection, validation, hashing, list/delete, isolation), then Phase 5 (injection + redaction suite), Phase 6 (code validation + citations).
