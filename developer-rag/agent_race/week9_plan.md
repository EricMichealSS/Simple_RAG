# Week 9 Plan — MCP: Connect the Agent to Tools the Standard Way
**Track E: Developer Documentation (GitHub API + Swagger Codegen migration)**
**Planning only — nothing in this document has been built yet.**

---

## 1. What MCP actually is (in plain English)

Right now, in `agent_race/agent.py`, our agent's 3 tools are wired in like this:
```python
from tools import TOOL_SCHEMAS, TOOL_IMPLS
```
That's a plain Python import. The agent and the tools live in the **same process, same file, same codebase**. If someone else — a different team, a different app — wanted to use our `search_docs`/`get_openapi_spec`/`check_deprecation` tools, they'd have to copy our Python files. And if we wanted to use *someone else's* tool, we'd have to read their code and write our own wrapper for it, by hand, every time.

**MCP (Model Context Protocol)** replaces that hand-wiring with a shared standard — think of it like a USB port. A USB port doesn't care whether you plug in a keyboard, a mouse, or a webcam; it's a standard socket, and any device that speaks "USB" just works. MCP is the same idea for AI tools: instead of every agent needing custom code for every tool, tools expose themselves over a common protocol, and any agent that speaks MCP can find and use them — without either side needing to see the other's source code.

**The one thing to be very clear on (the brief says this outright): MCP does not make the AI smarter.** It's plumbing — wiring, not intelligence. It doesn't improve answer quality at all. What it buys is *reuse* and *easy swapping*: add a new tool by editing a config file, not by writing new integration code.

## 2. The three roles, mapped onto our actual project

MCP defines three roles. Here's what each one would be, concretely, in our codebase:

| Role | What it means | What it would be for us |
|---|---|---|
| **Host** | The application the human/user actually talks to; it owns the conversation and decides to use an agent | Our `agent.py` process — the thing running the ReAct loop |
| **Client** | The part of the host that speaks MCP — connects to servers, asks them what tools they have, and forwards tool calls to them | A new small piece of code inside/alongside `agent.py` that replaces the current `from tools import TOOL_SCHEMAS, TOOL_IMPLS` line |
| **Server** | A separate process that *offers* tools/data, but has no idea what AI (if any) is calling it, and never runs the LLM itself | Two new, separate small programs: one wrapping our own 3 docs tools, one simulating the "package-registry" tool DevRel is asking us to use |

**The most important architectural rule this week, stated plainly:** the AI model call (the actual "ask Groq's `openai/gpt-oss-120b` what to do next" step) only ever happens on the **host** side. The **server** never calls an LLM — it just answers "here are my tools" and "here's the result of running that tool" when asked. If a server ever tried to call an LLM itself, that would be a fundamental misuse of MCP (the brief calls this out directly as a common mistake). We'll need to say this out loud, in one sentence, as part of the `wire.json` deliverable.

## 3. Before vs. after — the actual architecture change

**Right now (Week 7/8, still true today):**
```
agent.py  ──(direct Python import)──▶  tools.py (search_docs, get_openapi_spec, check_deprecation)
```
One process. Adding a 4th tool means editing `tools.py` and `agent.py`'s imports.

**After Week 9:**
```
agent.py  ──(MCP client, reads mcp_config.json)──▶  "docs-search" MCP server (wraps our existing 3 tools)
                                                 └─▶  "package-registry" MCP server (new: package version/deprecation lookups)
```
Two separate processes (each one a small standalone program), connected to the agent only through a config file. Adding the *second* server means adding one entry to a config file — `agent.py` itself shouldn't need a single line changed. That's the literal thing we have to prove with a `git diff`.

## 4. Why this fits into what we've already built (the big picture across all 9 weeks)

| Weeks | What we built | What was still hard-wired |
|---|---|---|
| 1–6 | Basic RAG, then evals and judge validation | The retrieval pipeline itself |
| 7–8 | An agent loop that picks tools, then a trajectory eval that scores *how* it picks them | The 3 tools were still plain Python functions, imported directly |
| **9** | **Making tool *access* itself standard and swappable** | — |

Every previous week made the agent smarter or better-measured. This week doesn't touch intelligence at all — it touches **plumbing**: could someone else's agent use our docs tool without reading our code? Could we add DevRel's package-registry tool without touching our agent's code? That's a *different* kind of improvement than everything before it — production maturity, not answer quality — and it's worth being honest with a mentor that this week's number ("0 lines changed") is a plumbing metric, not a smarts metric.

## 5. The concrete plan — server by server

### Server #1 — `docs-search` (our own server, wraps what we already have)

- Take the 3 existing functions in `tools.py` (`search_docs`, `get_openapi_spec`, `check_deprecation`) and expose them as MCP tools, using the `fastmcp` library (a thin framework for writing MCP servers in Python without hand-rolling the JSON-RPC parts).
- This is "our own server" for two deliverables:
  - **Requirement 5**: rewrite one tool's docstring into a richer, guidance-style prompt (a good candidate: `check_deprecation`'s description, extended with worked examples of *when* to call it vs. `get_openapi_spec`).
  - **Requirement 5 (recoverable errors)**: right now, asking for an API version we don't have returns `{"error": "api_version must be one of [...], got 'v4'"}` — functional, but not guiding. Rewrite it to something like: *"no docs for API version 'v4' — the latest documented version is 2025-06-01; the previous one is 2022-11-28."* The before/after transcript (`error_before_after.md`) would show the model's response to the exact same bad input, once with the blunt error and once with the recoverable one — the brief's own worry is that a blunt error makes the model "cheerfully emit a sample against the deprecated version" instead of noticing something's wrong.

### Server #2 — `package-registry` (the new one, played as third-party)

DevRel's real package-registry server doesn't exist for us to attach to, so this would be a small **simulated** server we write ourselves (backed by a tiny made-up dataset of package names/versions/release dates/deprecation notices) — but *treated* as an untrusted third party for the security exercise, which is exactly the kind of realistic-but-synthetic setup every week so far has used (the docs PDFs, the golden sets — all synthetic, all treated as real for the exercise). This needs to be stated honestly, not disguised.

- Tools it would expose: something like `get_package_version(name)`, `get_release_date(name, version)`, `check_package_deprecated(name, version)`.
- This is what proves **Requirement 1** (a query that provably calls a tool from the *new* server) and **Requirement 3** (tool count before → after: 3 tools from `docs-search` alone, then N more once `package-registry` joins — reported from a live `tools/list` call, not from memory).

### The agent side — replacing the direct import with an MCP client

- `agent.py` currently does `from tools import TOOL_SCHEMAS, TOOL_IMPLS`. This becomes: read `mcp_config.json` (a small file just listing which servers to connect to and how to reach them), connect to each one, call `tools/list` on each to discover what they offer, merge the results, and hand that merged list to the LLM exactly the way `TOOL_SCHEMAS` is handed to it today. When the model picks a tool, the client looks up which server owns that tool name and forwards the call there instead of calling a local Python function directly.
- **The core proof point (Requirement 2, worth the most points — 30):** `git diff` on `agent.py` between "1 server configured" and "2 servers configured" should show **zero changed lines**. Only `mcp_config.json` changes.

### The wire capture (`wire.json`, Requirement 4)

- Turn on raw logging for one query against the package-registry server and save the actual JSON-RPC messages for the three phases:
  - `initialize` — the client and server introduce themselves and agree on protocol version/capabilities.
  - `tools/list` — the server tells the client exactly what tools it has, with their names and parameter schemas (this is the "discovery," the thing we have to prove is real and not hand-typed).
  - `tools/call` — the client asks the server to actually run one tool with specific arguments, and the server sends back the result.
- Hand-annotate every top-level field in each message (`jsonrpc`, `id`, `method`, `params`, `result`, etc.) — plain English notes on what each one is for.
- One required sentence distinguishing where the LLM call happens (host side, talking to Groq) from where this JSON-RPC exchange happens (client ↔ server, no LLM involved at all).

### The risk note (`risk_note.md`, Requirement 6)

Exactly 5 lines, on the (simulated, but analyzed honestly as if real) package-registry server:
1. Who wrote/maintains it, and how much do we actually know about them.
2. What it can reach — does it only read data, or could it act on anything.
3. What it logs about our queries (does the server operator see what we're looking up).
4. What a stolen credential/token for this server could be used for.
5. Ship or don't ship — a direct verdict, not a hedge.

## 6. A real compatibility problem, found before writing any code

The official `mcp` Python package (and `fastmcp`) **requires Python 3.10 or newer**. This project's existing virtual environment (`developer-rag/.venv`) is **Python 3.9.6** — the same environment that already broke once this way in Week 8 (the `int | None` syntax error). Trying to `pip install mcp` into the current venv would fail outright, not just misbehave.

**The plan to avoid repeating that mistake:** build Week 9's work in a **separate, new virtual environment** using Homebrew's Python 3.12 (`/opt/homebrew/bin/python3.12`, already installed on this machine), completely isolated from the existing `.venv` that Weeks 5–8 depend on (`chromadb`, `sentence-transformers`, the pinned ML stack). This is the same "don't touch the pinned stack" precaution taken in Week 6 when `ragas` was skipped in favor of a hand-built equivalent, for the same reason — a new dependency tree colliding with an old one is a real, recurring risk in this project, not a hypothetical one.

Concretely: `docs-search`'s MCP server process would run in the new 3.12 environment (it needs `chromadb`/`sentence-transformers` too, reinstalled fresh there — those aren't shared between venvs), and so would the agent/client process. The old 3.9 venv keeps running Weeks 5–8's eval and race scripts exactly as they are, untouched.

## 7. Glossary — every "topics covered" item, tied to this project

| Topic | What it means here |
|---|---|
| **What MCP is** | The USB-port idea from section 1 |
| **Host, client, server** | `agent.py` = host+client; two new small programs = servers (section 2) |
| **Where the AI runs** | Only on the host side; servers never call an LLM (the required 1-line statement) |
| **Tools, resources, prompts** | We're exposing *tools* (things the model can invoke, like our 3 functions) — not *resources* (data just attached to context, no model decision needed). The brief warns against this exact mix-up: putting something that should just be a fact in the prompt behind a "tool call" wastes a whole turn fetching it |
| **Transports (stdio, HTTP)** | *How* the client and server talk — stdio means "over the pipes of a subprocess running on the same machine" (simplest, good for local servers like ours); HTTP means "over the network" (needed for a truly remote server) |
| **JSON-RPC handshake** | The literal message format `initialize`/`tools/list`/`tools/call` use — a simple "call this method with these params, get this result back" pattern, captured in `wire.json` |
| **Tool discovery** | The client asking the server "what have you got?" via `tools/list`, instead of us hard-typing a tool list ourselves |
| **Building a server (fastmcp)** | The library used to write our two servers without hand-rolling the protocol |
| **Recoverable errors** | The docstring/error rewrite in section 5 |
| **Remote MCP & auth** | Relevant if `package-registry` were a real, network-hosted server needing a credential — feeds directly into the risk note's "stolen token" question |

## 8. Decisions locked in for this plan

- **`package-registry` will be a small local simulation** (own code, own tiny fake dataset of package names/versions/dates/deprecation notices), clearly labeled as simulated rather than a real third party. This keeps the exercise fully offline and reproducible, matching how every previous week's "external" data (docs PDFs, golden sets) was handled — and it still lets us do the security audit honestly, since we can reason about it "as if" it were a real third-party service we don't control.
- **A new, separate Python 3.12 virtual environment** will be created just for this week's MCP work (servers + the MCP-client side of the agent), kept fully isolated from the existing `.venv` that Weeks 5–8 depend on. Nothing in the current environment gets upgraded or touched.

These are the defaults I'd build against — say so now if either should change before implementation starts.

## 9. Files this would produce (none created yet)

| File | Deliverable it satisfies |
|---|---|
| `agent_race/mcp/docs_search_server.py` | our own MCP server, wrapping the existing 3 tools |
| `agent_race/mcp/package_registry_server.py` | the simulated second server |
| `agent_race/mcp/mcp_config.json` | the config file that lists which servers to connect to (this is what changes to add server #2 — `agent.py` shouldn't) |
| `agent_race/mcp/agent_diff.txt` | the `git diff` proving 0 lines changed in `agent.py` |
| `agent_race/mcp/wire.json` | the hand-annotated raw protocol exchange |
| `agent_race/mcp/error_before_after.md` | the before/after transcript for the recoverable-error rewrite |
| `agent_race/mcp/risk_note.md` | the exact 5-line security note |
| `agent_race/mcp/tool_discovery.md` or similar | the N → M tool count report, from a live `tools/list` call |
