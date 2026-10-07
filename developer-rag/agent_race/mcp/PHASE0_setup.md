# Phase 0 — Environment Setup

## What this phase did
Created a brand-new, fully isolated Python environment just for Week 9's MCP work, and verified it can do everything it needs to before building anything on top of it.

## Why a separate environment
The official `mcp` Python package requires Python 3.10+. The existing project venv (`developer-rag/.venv`) is Python 3.9.6 — the exact same version that already caused a real crash in Week 8 (`int | None` syntax). Rather than upgrading that venv (risking Weeks 5–8's pinned `chromadb`/`sentence-transformers` versions breaking), a second, independent venv was created using Homebrew's Python 3.12. The old venv is completely untouched.

## Files/commands used
- `agent_race/mcp/.venv-mcp/` — the new virtual environment (created with `/opt/homebrew/bin/python3.12 -m venv .venv-mcp`)
- Packages installed into it: `mcp` (official MCP SDK, includes the `FastMCP` server helper), `chromadb` (1.5.9 — much newer than whatever Weeks 5–8's venv pinned), `sentence-transformers` (6.1.0), `openai` (3.19.2), `python-dotenv`

## Edge cases checked before building anything else

1. **Could the much newer `chromadb` (1.5.9) even read the existing on-disk database** created by the old venv's older `chromadb` version? Real risk — major version jumps sometimes change the on-disk storage format. **Tested directly: it opened `data/chroma`, found the `race_docs` collection, and read all 39 chunks correctly.** No format mismatch.
2. **Does `config.py` (which lives in the old-venv project root) import cleanly under the new venv?** It only depends on `os` and `python-dotenv`, both trivial — confirmed it imports fine and finds the real `GROK_API_KEY`.
3. **Does the embedding model still work the same way?** Loaded `BAAI/bge-small-en-v1.5` fresh (downloads into the new venv's own cache, doesn't share the old venv's cached copy) and confirmed it produces the expected 384-dimension vectors.
4. **Does the much newer `openai` client (3.19.2 vs. whatever Week 7/8 used) still talk to Groq the same way?** Tested a real call. First attempt came back with an *empty* answer despite using 86 tokens — not a library bug, but the same reasoning-token trap found back in Week 6: `openai/gpt-oss-120b` is a reasoning model, and with `max_tokens` too small, all of it gets consumed by hidden reasoning tokens before anything visible gets written. Confirmed by re-running with `max_tokens=200`: the visible answer came through correctly (`content: 'OK'`, with `reasoning_tokens=33` visible in the usage breakdown). **Fix carried forward: use `max_tokens=1024` for every LLM call in the new MCP agent, matching what the existing Week 7/8 agent already uses** — this was already the right value for exactly this reason, now re-confirmed under the new environment.

## Outcome
Environment is verified solid — real data, real embedding model, real Groq calls, all confirmed working under Python 3.12 before any MCP-specific code was written. Moving to Phase 1: building the `docs-search` MCP server.
