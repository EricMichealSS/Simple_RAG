# Phase 4 — Adding Server #2 With Zero Agent Code Changes (the core proof)

## What this phase did
Added the `package-registry` server to `mcp_config.json` — and nothing else — then re-ran the exact same `mcp_agent.py` and confirmed both that the file genuinely didn't change and that the agent genuinely discovered and used the new server's tools live.

## The zero-diff proof, exactly as run

A snapshot of `mcp_agent.py` was saved at the end of Phase 2 (`mcp_agent.py.phase2_snapshot`), **before** `package-registry` was ever added anywhere. After adding it to the config:

```
$ diff -u mcp_agent.py.phase2_snapshot mcp_agent.py
(exit code: 0)
```

`agent_diff.txt` contains the raw output of that command — **0 bytes, completely empty**. Not "a small diff," not "only comments changed" — literally nothing.

**Why a plain `diff`, not an actual `git diff`:** across this whole project this session has been instructed not to run git commits on its own initiative — commits and pushes are the user's to do. Since `mcp_agent.py` was never committed at the Phase 2 checkpoint, there's no git history to diff against yet. A plain file-level `diff -u` against a saved snapshot proves exactly the same fact (the file's bytes are unchanged) without needing a commit that wasn't authorized. If a real `git diff` is wanted for the submission, committing `mcp_agent.py` right after Phase 2 (before touching the config) and running `git diff -- mcp_agent.py` now would show the same empty result — worth doing if a literal git artifact matters for grading.

## What DID change — `mcp_config_diff.txt`

```diff
     "docs-search": {
       "command": "python3",
       "args": ["docs_search_server.py"]
+    },
+    "package-registry": {
+      "command": "python3",
+      "args": ["package_registry_server.py"]
     }
```
Four lines added, one file, zero code.

## Tool count, before → after, from live discovery (not notes)

**Before** (`log[0]` of the Phase 2 run): `count: 3`, names `['search_docs', 'get_openapi_spec', 'check_deprecation']`, all owned by `docs-search`.

**After** (`log[0]` of this run, see `tool_discovery_trace.txt`):
```
count: 6
names: ['search_docs', 'get_openapi_spec', 'check_deprecation',
        'get_package_version', 'get_release_date', 'check_package_deprecated']
owners: {..., 'get_package_version': 'package-registry',
              'get_release_date': 'package-registry',
              'check_package_deprecated': 'package-registry'}
```
**3 → 6 tools.** Both numbers came from a real `session.list_tools()` call logged into `log[0]` at the start of every run — not typed by hand.

## Proof the new server's tool was actually invoked (Requirement 1)

Question: *"Is version 2.4.0 of the octokit package deprecated, and if so what should I upgrade to?"* (saved in full in `tool_discovery_trace.txt`). The real trace:

```
tool_call: get_package_version       server: package-registry   args: {"name":"octokit"}
tool_call: check_package_deprecated  server: package-registry   args: {"name":"octokit","version":"2.4.0"}
```
Both calls are explicitly tagged with `"server": "package-registry"` in the log (`mcp_agent.py`'s `router.call()` records which server owns each tool, precisely so this is provable rather than assumed). Final answer: *"Yes, octokit 2.4.0 is deprecated. You should upgrade to the 3.x series (e.g., the latest 3.1.4)"* — correct, matching `package_registry_data.json`.

## Outcome
This is the core deliverable of the entire week: **the agent module is provably unchanged, and it provably used a tool that didn't exist for it a moment before, discovered entirely at runtime.** Moving to Phase 5: capturing and annotating the raw protocol messages behind this.

## Addendum — re-verified after a later, unrelated fix

While checking edge cases afterward (Phase 8), a small robustness fix was made to `connect_all()` (clearer error message when a configured server fails to start — see PHASE8 doc). That fix touches error-handling around server *connection*, not tool discovery or config parsing, and it was made after this proof was first captured. Rather than leave a stale snapshot, this entire proof was re-run cleanly from the current, final `mcp_agent.py`: 1-server config re-tested (3 tools, correct answer) → 2-server config restored (6 tools, `package-registry` tool call confirmed) → `agent_diff.txt` regenerated — still 0 bytes. `agent_diff.txt` and `tool_discovery_trace.txt` in this folder reflect this final, self-consistent re-verification, not the original one.
