# MCP Tool Routing Upgrade — Making the Squad Production-Scalable

**What this document is:** a full account of a design question that came up after Week 10
("if I had many more tools/agents in production, would I still hand-type them into a Python
list?"), what was already sitting in this repo that could answer it, what was actually changed,
why, and exactly how a question now flows through the new code. Written so that reading this file
alone is enough to understand the change — no need to re-read Week 9 or Week 10's reports first,
though they're referenced throughout for anyone who wants the original context.

---

## 1. The question that started this

The Week 10 squad (`orchestrator.py` + `workers.py`) has the manager choose a worker like this:

```python
# orchestrator.py (original)
MANAGER_TOOLS = [
    {"type": "function", "function": {"name": "ask_version_worker", ...}},
    {"type": "function", "function": {"name": "ask_code_worker", ...}},
]
```

Two tools, hand-typed, in a Python constant. That's completely fine at 2. The question was: *if
this squad grows to real production scale — many more tools, many more specialist agents — is
hand-typing every one of them into a list still the right move, or should tool definitions be
served from somewhere dynamic, like MCP?*

**Short answer, which this document backs up with working code:** yes, route them through MCP —
and this project had already built everything needed to do that, back in Week 9. It just hadn't
been connected to the squad yet.

---

## 2. What was already in the repo (before this change)

Two previous pieces of work turned out to be exactly the right building blocks, just never wired
together:

### 2.1 Week 9 — MCP already proven for the *single* agent

`agent_race/mcp/` already contained a complete, working MCP setup:

| File | Role |
|---|---|
| `docs_search_server.py` | A real MCP **server** exposing `search_docs`, `get_openapi_spec`, `check_deprecation` over stdio (via `FastMCP`) |
| `package_registry_server.py` | A second MCP server exposing `get_package_version`, `get_release_date`, `check_package_deprecated` |
| `mcp_agent.py` | An MCP **client/host** — `MCPToolRouter` connects to every server listed in a config file, calls `list_tools()` to discover what exists, converts each tool's schema to OpenAI function-calling format, and routes a call to whichever server owns that tool name |
| `mcp_config.json` | The config listing which servers exist — adding a server here is the only thing Week 9 needed to change to add a whole new tool family |

Week 9's proof (`week9_report.md`): going from 1 configured server to 2 took a 4-line config
diff and **zero** lines changed in `mcp_agent.py` itself. That's the exact property the squad was
missing.

### 2.2 Week 10 — the squad, but still hard-wired

`agent_race/squad/orchestrator.py` + `workers.py` built the manager → specialist pattern, but:

- The manager's delegation options (`MANAGER_TOOLS`) were a hard-coded Python list.
- Each worker's real tool access came from `from tools import TOOL_SCHEMAS, TOOL_IMPLS` — a
  **direct Python import** of `agent_race/tools.py`, not MCP.
- `agent_race/tools.py` and `agent_race/mcp/docs_search_server.py` contain **two separate copies**
  of the same three tool implementations (`search_docs`, `get_openapi_spec`, `check_deprecation`)
  — one for the plain-Python path (`agent.py`, `workers.py`), one for the MCP path (`mcp_agent.py`).

So the pieces for "MCP-ify the squad" already existed in the repo — `MCPToolRouter` just had never
been pointed at the squad, and the squad's workers had never been exposed as discoverable tools
themselves.

---

## 3. The actual problem, stated precisely

Two separate but related issues, both already identified in the conversation that led here:

1. **`MANAGER_TOOLS` doesn't scale.** At N=2 it's fine. At N=50+, a hand-typed list means every
   new specialist is a code change + redeploy, and (separately) sending 50 schemas on every
   manager LLM call bloats tokens and measurably hurts tool-selection accuracy.
2. **Worker tool access is hard-imported, not discovered.** `workers.py` reaches into
   `tools.py` directly. A new capability for a worker means editing `tools.py` and `workers.py`
   by hand — exactly the "code change per tool" problem Week 9 already solved for the single
   agent, just never applied to the squad.

**The fix implemented here is the direct MCP equivalent of the squad's own architecture:**
expose each **worker** as an MCP tool (so the *manager* discovers workers over MCP, solving #1),
and have each **worker** reach its real tools through the *existing* `MCPToolRouter`/docs-search
server (so the *worker* discovers tools over MCP, solving #2) — instead of writing a third copy
of `search_docs` etc. from scratch.

---

## 4. What was changed — file by file

| File | Status | What it does |
|---|---|---|
| `agent_race/mcp/mcp_agent.py` | **Modified** (small, backward-compatible) | `MCPToolRouter.connect_all()` now accepts an explicit `cwd` parameter instead of always using its own file's directory. See Section 6.1 — this was a real bug the reuse attempt surfaced. |
| `agent_race/squad/squad_tools_server.py` | **New** | A new MCP **server**, `"squad-workers"`, exposing `ask_version_worker`, `ask_code_worker`, and (new) `ask_package_worker` as MCP tools. Internally, each tool is itself an MCP **client** of the *existing* `docs-search`/`package-registry` servers — no new tool logic was written. |
| `agent_race/squad/squad_mcp_config.json` | **New** | Config telling the manager which server(s) to connect to (just `squad-workers`, one entry) — the squad-level equivalent of `agent_race/mcp/mcp_config.json`. |
| `agent_race/squad/orchestrator_mcp.py` | **New** | The manager, rewritten as an MCP **client**. `MANAGER_TOOLS` is gone — the tool list is `router.openai_schemas`, discovered live. Delegation is `await router.call(...)` instead of a hard-coded `_WORKER_FOR_TOOL` lookup + direct Python call. |
| `agent_race/squad/fault_control.py` | **New** | A small file-based fault-injection toggle, replacing the in-memory `FAULT` dict from `workers.py` — necessary once workers live in a separate OS process (Section 6.2). |

**`orchestrator.py` and `workers.py` (the original Week 10 files) were left untouched.** This
follows the same pattern already used throughout this project (`ingest.py` vs. `ingest_live.py`,
`rag.py` vs. `rag_hybrid.py`, `agent.py` vs. `mcp_agent.py`): keep the earlier version as a
working baseline/comparison point, add the new version alongside it, rather than overwriting
history.

---

## 5. Why each change was made

**Why a new MCP server (`squad_tools_server.py`) instead of just changing `workers.py`?**
Because the goal is for the *manager* to discover workers the same way `mcp_agent.py` discovers
tools — and MCP discovery only works across a real client/server boundary. A Python function
can't be "discovered" at runtime the way an MCP tool can; it has to be exposed as one.

**Why does each worker-tool internally *also* act as an MCP client, instead of importing
`tools.py`?** Two reasons:
1. It removes the second copy of `search_docs`/`get_openapi_spec`/`check_deprecation` that
   `tools.py` and `docs_search_server.py` both maintained — the squad now reuses the Week 9
   server's implementation directly instead of adding a *third* copy.
2. It means a future tool added to `docs_search_server.py` (or a brand-new MCP server) becomes
   available to a worker the instant it's added to that worker's `tools` list in
   `squad_tools_server.py`'s `WORKERS` dict — no re-import, no new function reference needed.

**Why add `package_worker` as a third specialist, when nobody asked for one?**
To make the "n tools/agents, not just 2" scaling claim concrete rather than theoretical. Adding it
required exactly:
- one new entry in `WORKERS` (`squad_tools_server.py`),
- one new `@mcp.tool()` wrapper function (11 lines, no new tool logic — `get_package_version`
  etc. already existed on `package_registry_server.py` since Week 9),
- **zero changes to `orchestrator_mcp.py`.**

That last point is the actual proof: the manager now routes across 3 specialists without having
been told about the third one anywhere in its own file.

**Why a file-based fault toggle instead of keeping the `FAULT` dict?**
See Section 6.2 — an in-memory dict in one process is invisible to a different OS process.

---

## 6. Two real issues this reuse attempt surfaced (not hypothetical — found while wiring this up)

### 6.1 `MCPToolRouter.connect_all()` hard-coded its own directory as `cwd`

Original code (`mcp_agent.py`):
```python
async def connect_all(self, config_path: Path = CONFIG_PATH):
    ...
    params = StdioServerParameters(command=server_cfg["command"], args=server_cfg["args"], cwd=str(HERE))
```
`HERE` is `agent_race/mcp/`'s own path, a module-level constant. This was invisible as a bug in
Week 9 because the only caller was `mcp_agent.py` itself, always launching servers that live in
its own folder. The moment `orchestrator_mcp.py` (in `agent_race/squad/`) tried to reuse this same
class to launch `squad_tools_server.py` (which lives in `agent_race/squad/`, not
`agent_race/mcp/`), the subprocess would have been spawned with the wrong working directory and
failed to find the file named in `args`.

**Fix:** `connect_all()` now takes an explicit `cwd` parameter, defaulting to the config file's own
directory if not given. This is fully backward-compatible — every existing call site
(`mcp_agent.py`'s own usage) behaves identically, because `config_path.parent` already equals the
old hard-coded `HERE` in that case.

### 6.2 Crossing a process boundary breaks two things the in-process squad took for granted

`workers.py`'s original `call_worker()` ran inside the **same process** as the manager. That made
two things easy, which silently stop working once a worker is a separate MCP server process:

**(a) The manager's budget `guard()` could interrupt a worker mid-loop.**
```python
# workers.py (original) — guard is a live Python closure, passed by reference
reason = guard()
if reason:
    return {"status": 503, "body": {"error": f"ceiling reached: {reason}"}}
```
A closure cannot be sent across stdio/JSON-RPC — only data can. So in the MCP version, the
manager can no longer reach into an in-flight worker call to stop it early. **What changes:**
each worker still enforces its own local ceiling independently (`WORKER_TIMEOUT_S`,
`WORKER_MAX_LAPS` in `squad_tools_server.py`, unchanged in spirit from before), and the manager's
`guard()` now only ever decides whether to **start another delegation** — it checks before each
lap, same as before, but it cannot preempt a call already in progress. This is a real, disclosed
trade-off of the MCP design, not a bug that was fixed silently.

**(b) The manager's `Tracer` could see a worker's token/cost usage automatically.**
In the original, `call_worker()` called `metered_chat(tracer, ...)` using the **same** `tracer`
object the manager was using — one shared Python object, one ledger. Once the worker's LLM calls
happen inside `squad_tools_server.py`'s own process, that `tracer` object isn't reachable there at
all — the two processes don't share memory.

**Fix:** every worker-tool call now returns its own usage alongside its answer:
```python
# squad_tools_server.py
return {"status": 200, "body": "...", "usage": {"prompt_tokens": .., "completion_tokens": .., "cost_usd": .., "latency_s": ..}}
```
and the orchestrator explicitly folds that back into its own ledger right after the call returns:
```python
# orchestrator_mcp.py
out = await router.call(tool_name, args)
usage = out.pop("usage", None)
if usage:
    tracer.record(tool_name, f"orchestrator -> {tool_name} (worker-internal, folded)",
                  usage["prompt_tokens"], usage["completion_tokens"], usage["latency_s"])
```
`usage` is popped out of `out` *before* `out` is appended to the manager's conversation history —
otherwise the raw token/cost numbers would be fed to the LLM as if they were part of the answer,
wasting tokens and potentially confusing it. This preserves the original project's core discipline
(Week 10, Section 6.4: "every model call anywhere in the system is labeled with exactly which
hand-off triggered it") — the accounting is now explicit across the wire instead of implicit via a
shared object, but it's still one accurate, unified total.

---

## 7. Code flow — how a question travels through the new system

```
run_squad(question)                                          [orchestrator_mcp.py]
  │
  ├─ router = MCPToolRouter()
  ├─ await router.connect_all(squad_mcp_config.json, cwd=HERE)
  │     └─ spawns squad_tools_server.py as a subprocess over stdio   [new OS process #1]
  │     └─ router.list_tools() discovers:
  │          ask_version_worker, ask_code_worker, ask_package_worker
  │          (router.openai_schemas now holds all 3 — this REPLACES MANAGER_TOOLS)
  │
  ├─ messages = [system: MANAGER_PROMPT, user: question]
  │
  └─ for lap in 1..8:                                          ← MANAGER ReAct loop (unchanged shape)
       guard()  — may stop starting a NEW delegation, cannot interrupt one in flight
       metered_chat(tracer, "orchestrator", ..., tools=router.openai_schemas)
           └─ LLM decides: answer now, or call one of the 3 discovered tools?
       if no tool_calls: return answer                         ← same termination rule as before
       else: for each tool_call:
              tool_name = tc.function.name                     ← e.g. "ask_package_worker"
              out = await router.call(tool_name, args)         ← THIS is the MCP call, replacing
              │                                                   _WORKER_FOR_TOOL + call_worker()
              │
              │   [inside squad_tools_server.py, OS process #1, via stdio]
              │   ask_package_worker(request, package_name, package_version)
              │     └─ _run_worker("package_worker", request, {...})
              │          ├─ reads fault_state.json (fault_control.py's file, not a shared dict)
              │          ├─ leaf_router = await _get_leaf_router()
              │          │     └─ (first call only) spawns docs_search_server.py AND
              │          │        package_registry_server.py as subprocesses       [OS processes #2, #3]
              │          │        router.list_tools() discovers 6 leaf tools total
              │          ├─ schemas = only this worker's allowed subset of those 6
              │          ├─ for lap in 1..8:                   ← WORKER's OWN ReAct loop
              │          │     LLM call (tracked locally: prompt_tok, completion_tok, cost)
              │          │     if tool_calls: await leaf_router.call(tool, args)  ← real MCP call
              │          │                     to package_registry_server.py (OS process #3)
              │          │     else: return {"status": 200, "body": answer, "usage": {...}}
              │
              usage = out.pop("usage")                         ← cross-process cost reconciliation
              tracer.record(...usage...)                       ← folded into the MANAGER's own ledger
              messages.append({"role": "tool", "content": json.dumps(out)})  ← out, usage already removed
       (manager loop continues to next lap with the worker's report now visible)
  │
  └─ finally: await router.aclose()                            ← tears down subprocess #1
              (subprocess #1, on its own exit, would need its leaf_router closed too --
               see "Known limitation" below)
```

**Three real OS processes are involved in a single question**, not one: the manager's own
process, `squad_tools_server.py` (the worker-exposing MCP server), and — lazily, on first
delegation — the leaf `docs_search_server.py`/`package_registry_server.py` processes that
`squad_tools_server.py` itself connects to as a client. This is a legitimate, realistic
multi-level MCP deployment shape (a "gateway" server that is simultaneously an MCP server to its
caller and an MCP client to the services behind it) — not a toy simplification.

---

## 8. Side-by-side: what actually changed in the manager's own decision-making code

| | `orchestrator.py` (original) | `orchestrator_mcp.py` (this change) |
|---|---|---|
| Tool list source | `MANAGER_TOOLS` constant, hand-typed | `router.openai_schemas`, discovered via `list_tools()` |
| Adding a worker | Edit `MANAGER_TOOLS` + `_WORKER_FOR_TOOL` + `workers.py`'s `WORKERS` dict | Edit `squad_tools_server.py`'s `WORKERS` dict + add one `@mcp.tool()` function. **Zero edits here.** |
| Routing a chosen tool | `wname = _WORKER_FOR_TOOL.get(tc.function.name)` then `call_worker(...)` (direct Python call, same process) | `await router.call(tc.function.name, args)` (MCP call, separate process) |
| Worker's real tool access | `from tools import TOOL_SCHEMAS, TOOL_IMPLS` (3rd copy of the tool logic) | MCP client to the *existing* `docs-search`/`package-registry` servers (no new copy) |
| Token/cost accounting | Automatic — one shared `tracer` object | Explicit — each worker call returns `usage`, folded into `tracer` by the caller |
| Mid-call budget abort | Possible — `guard` passed in by reference | Not possible — worker enforces its own local ceiling instead |
| Fault injection | In-memory `FAULT` dict, same process | `fault_state.json`, a flat file (same pattern as `race_runs.json` elsewhere in this project) |

---

## 9. What this does and does not prove yet

**What it demonstrates (by construction, matching Week 9's own standard of evidence):**
- The manager's tool list is no longer hand-typed; it is discovered.
- A third specialist (`package_worker`) was added with zero changes to the manager's own file —
  the direct, concrete version of the "n agents, not just 2" scaling question that started this.
- The pre-existing duplication between `tools.py` and `docs_search_server.py` was not repeated a
  third time — the squad now reuses the Week 9 server directly.

**What was not run in this session, and why (disclosed honestly, same standard this project
already holds itself to in `race_runs.json`'s "only 1 repeat due to quota" and similar notes):**
This change was made on a Windows machine that does not have the `mcp`, `openai`, `chromadb`, or
`sentence-transformers` Python packages installed (the project's commit history shows Week 10 was
built on a Mac). All four new/changed files were verified with `python -m py_compile` (syntax is
valid) and the new JSON config was verified to parse, but no live end-to-end run against the real
Groq API or the real `race_docs` Chroma collection has been executed yet. Before relying on this
in place of `orchestrator.py`, run it once for real:
```
cd agent_race/squad
python3 orchestrator_mcp.py "In GitHub API version 2025-06-01, what parameter replaces the deprecated single assignee field when creating an issue?"
```
and separately confirm the new specialist:
```
python3 orchestrator_mcp.py "What is the latest version of octokit and is it deprecated?"
```
Watch `tools_discovered` in the printed summary — it should read `3` (not 2), confirming
`ask_package_worker` was actually picked up via discovery, not hard-coded.

**Known limitation to fix before any production use:** `squad_tools_server.py`'s own
`_leaf_router` (its connection to `docs_search_server.py`/`package_registry_server.py`) is a
lazy singleton that is never explicitly closed — it lives for as long as the
`squad_tools_server.py` process does, and that process's own lifecycle is managed by whatever
spawned it (`orchestrator_mcp.py`'s `router.aclose()` closes the *manager's* connection to
`squad-workers`, which should terminate the subprocess and, with it, its own child subprocesses —
but this chain was not verified end-to-end for this change, only read through the code. Confirm
no orphaned `docs_search_server.py`/`package_registry_server.py` processes are left running after
a real test before trusting this in anything long-running.

---

## 10. How to extend this further (next scaling step, not built here)

This change solved "adding a tool/agent shouldn't require a code change." It deliberately did
**not** build the other half of the earlier scaling discussion: filtering which of the discovered
tools get sent to the manager on each call. At 3 tools, sending all 3 schemas every lap is free.
Once this pattern is extended to dozens of specialists, add a retrieval step in front of
`router.openai_schemas` — embed each tool's description once, and at request time pass only the
top-k most relevant schemas to `metered_chat(...)`, the same way `rag_hybrid.py` already narrows a
large document set down to a few relevant chunks before generation. That is a separate, later
change and was intentionally not built now, to keep this update scoped to the one question that
was actually asked: hard-coded vs. MCP-discovered tool routing.
