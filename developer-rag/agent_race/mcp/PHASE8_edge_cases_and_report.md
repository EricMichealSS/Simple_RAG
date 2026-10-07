# Phase 8 — Edge Cases Checked, and Wrap-Up

Every real bug/gotcha found across all 8 phases, in one place, with where it was actually caught (not asserted):

| # | Edge case | Where found | Outcome |
|---|---|---|---|
| 1 | Does much-newer `chromadb` (1.5.9) read the on-disk DB an older version wrote? | Phase 0, tested directly | Yes — no format mismatch |
| 2 | Does the much-newer `openai` client (3.19.2) still talk to Groq correctly? | Phase 0, tested directly | Yes, but exposed a real gotcha (#3) |
| 3 | Reasoning-model empty-response trap: `max_tokens` too small lets hidden reasoning tokens consume the whole budget | Phase 0, first live Groq call came back blank | Fixed by using `max_tokens=1024` everywhere, matching what Week 7/8's agent already used |
| 4 | Which `mcp` major version to install — `pip install mcp` silently grabbed 2.x, which renamed `FastMCP` → `MCPServer` | Phase 1, checking API signatures before writing any server code | Pinned `mcp<2` to get the documented, stable `FastMCP` API the assignment names |
| 5 | Does typing `api_version`/`endpoint` as `Literal[...]` prevent the custom recoverable-error code from ever running? | Phase 1, standalone server test | Yes — schema validation intercepts bad enum values before the function body runs; documented honestly, re-targeted the official evidence to a failure shape that IS reachable (wrong-direction `check_deprecation`) |
| 6 | Does a tool returning a Python `list` come back the same way as one returning a `dict`? | Phase 2, first live agent run — `search_docs` result silently mis-parsed | No — FastMCP emits one `TextContent` block per list item, not one JSON array. Fixed in `MCPToolRouter.call()` |
| 7 | Does the richer recoverable error message actually change model behavior? | Phase 6, deliberately tested with 3 escalating attempts | Honest null result for this model/scenario — documented why, not hidden |
| 8 | What happens if a configured server fails to start? | Phase 8, deliberately broke a server's config entry | Fails fast (doesn't hang), but the real error was buried under a messy internal traceback — fixed with a clear, named error in `connect_all()` |
| 9 | Tool name collision across two servers offering the same tool name | Built defensively in `MCPToolRouter.connect_all()` (raises `ValueError` naming both servers) | Not naturally triggered by this week's 2 servers (no overlapping names) — verified the guard code exists and reads correctly, not exercised live since no real collision exists to trigger it |

## What changed after the Phase 4 proof was captured, and why it's still valid

Edge case #8's fix touched `mcp_agent.py` after the zero-diff proof was first produced. Rather than leave a stale artifact, the whole proof was re-run from the final code state (documented as an addendum in `PHASE4_zero_code_expansion.md`) — same result, 0 bytes.

## Final submission checklist, mapped to files

| Deliverable | File |
|---|---|
| `agent_diff.txt` (0 changed lines) | `mcp/agent_diff.txt` |
| Config diff adding server #2 | `mcp/mcp_config_diff.txt` |
| `wire.json`, annotated | `mcp/wire.json` |
| Tool count N → M, with names | `PHASE4_zero_code_expansion.md`, `tool_discovery_trace.txt` |
| `error_before_after.md` | `mcp/error_before_after.md` |
| `risk_note.md` (5 lines) | `mcp/risk_note.md` |

## Honest overall summary

3 tools → 6 tools, added by editing one JSON file, with a byte-for-byte unchanged agent module, re-verified twice. Two real bugs found and fixed along the way (list-vs-dict serialization, a version-pin surprise in `mcp` itself), one planned feature (the recoverable-error rewrite) found to not move the needle for this specific model in this specific test, reported as a null result rather than papered over. That mix — some things working as designed, one thing genuinely not, all measured rather than assumed — is the same standard every week since Week 5 has been held to, and this week doesn't get an exception.
