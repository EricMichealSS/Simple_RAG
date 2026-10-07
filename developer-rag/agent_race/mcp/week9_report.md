# Week 9 — MCP: Bolting on the Package-Registry Server Without Touching the Agent
**Track E: Developer Documentation (GitHub API + Swagger Codegen migration)**
**Date: 2026-09-28**

---

## 1. What Week 9 is, and the Week 8 → 9 delta

Week 8 measured *whether the agent's own reasoning path* was trustworthy (trajectory evals, one mitigation, a regression check). Week 9 doesn't touch reasoning quality at all — it replaces *how the agent finds its tools* with an industry-standard protocol (MCP), so that adding DevRel's new package-registry capability requires editing one config file, never the agent's code. Same domain, same underlying capabilities (docs lookup + now package metadata), completely different plumbing underneath.

A new, separate Python 3.12 virtual environment (`mcp/.venv-mcp`) was used for all of this, isolated from the Python 3.9 venv Weeks 5–8 depend on — the official `mcp` package requires 3.10+, and this project has already been bitten once (Week 8) by a Python-version mismatch.

## 2. The core result

```
Tools discovered, 1 server configured:  3   (search_docs, get_openapi_spec, check_deprecation)
Tools discovered, 2 servers configured: 6   (+ get_package_version, get_release_date, check_package_deprecated)

agent_diff.txt (mcp_agent.py, 1-server config vs. 2-server config): 0 bytes, 0 lines changed
mcp_config.json diff: +4 lines (one new server entry), the only thing that changed
```

Both tool counts came from live `session.list_tools()` calls logged at the start of every run, not typed by hand. The zero-diff result was re-verified twice (once right after adding server #2, once again from the final code state after a later, unrelated fix — see Phase 4/8 docs) — same result both times.

## 3. Proof the new server's tool was genuinely used, not assumed

Question: *"Is version 2.4.0 of the octokit package deprecated, and if so what should I upgrade to?"* Real trace:
```
tool_call: get_package_version       server: package-registry   {"name":"octokit"}
tool_call: check_package_deprecated  server: package-registry   {"name":"octokit","version":"2.4.0"}
```
Answer: *"Yes, octokit 2.4.0 is deprecated. You should upgrade to the 3.x series (e.g., the latest 3.1.4)"* — correct.

## 4. What was actually built

| Piece | File |
|---|---|
| Our own server, wrapping the existing 3 docs tools | `docs_search_server.py` |
| The new, simulated (honestly labeled) second server | `package_registry_server.py` + `package_registry_data.json` |
| The MCP-speaking agent (host + client in one file) | `mcp_agent.py` |
| The one file that changes to add a server | `mcp_config.json` |
| Raw, hand-annotated protocol capture | `wire_capture.py` → `wire.json` |
| Docstring-as-prompt + recoverable-error rewrite evidence | `docs_search_server.py`'s `check_deprecation`, `error_transcript_test.py` → `error_before_after.md` |
| Supply-chain risk audit | `risk_note.md` |

Per-phase detail, including every bug found and fixed along the way, is in `PHASE0` through `PHASE8` in this same folder — written as each phase completed, not reconstructed afterward.

## 5. What genuinely worked, and what didn't (reported both ways)

**Worked cleanly:** the core zero-code-change proof, tool discovery counts, the docstring-as-prompt rewrite, the risk audit, the wire capture, and the recoverable unknown-package error on the new server (verified reachable and correctly informative).

**Two real bugs found by testing, not assumed away:** a tool returning a Python `list` serializes completely differently over MCP than one returning a `dict` (one `TextContent` block per item, not one JSON array) — broke `search_docs` silently until caught and fixed. And typing `api_version`/`endpoint` as a strict enum — good practice carried over from Week 7/8 — turned out to *prevent* the custom recoverable-error code from ever running for that specific input shape, since schema validation catches it first; the official evidence was honestly re-targeted to a failure shape that really is reachable.

**One honest null result:** the recoverable-error rewrite for `check_deprecation`'s wrong-direction case was tested three separate ways (single-shot, wider enum, 8 trials at temperature=0.7) and showed **no measurable behavior difference** versus the original blunt message — `openai/gpt-oss-120b` recovers reliably either way in this specific scenario, because the user's own question already names both versions being compared. Documented as a real finding with the reason, not hidden or replaced with a cherry-picked positive anecdote.

## 6. Bonus (gateway + token scoping) — not attempted

Out of scope for this pass given time. Flag if wanted: a single gateway process fanning out to both servers, one unified audit log line per `tools/call` (`caller`, `tool`, `requested_package@version`), and a scoped token that denies `check_package_deprecated` specifically while still allowing `get_package_version` — verifying that denial reaches the model as a recoverable message rather than a hard crash.
