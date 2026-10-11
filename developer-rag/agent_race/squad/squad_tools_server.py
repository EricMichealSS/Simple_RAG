"""Our own MCP server, "squad-workers". Exposes each specialist worker
(ask_version_worker, ask_code_worker, ask_package_worker) as one MCP tool,
so the manager can discover them live via `list_tools()` instead of reading
them off a hard-coded MANAGER_TOOLS constant (see orchestrator_mcp.py).

This is the direct answer to "do I pass manager_tools via an MCP
connection?" -- yes, and this file is the server that makes that possible.

Each tool implementation below is, in turn, an MCP *client* of the EXISTING
Week 9 servers (agent_race/mcp/docs_search_server.py and
package_registry_server.py) for its own real tool access -- it does NOT
import agent_race/tools.py. That means the squad no longer needs its own
copy of search_docs/get_openapi_spec/check_deprecation; it reuses the one
already exposed over MCP. Adding package_worker required zero new tool
code at all -- get_package_version / get_release_date /
check_package_deprecated already existed on the package-registry server
built in Week 9; this file only adds the specialist's prompt, its allowed
tool subset, and one @mcp.tool() wrapper function.

IMPORTANT process-boundary notes (see mcp_tool_routing_upgrade.md for the
full write-up):
  - This process is spawned over stdio by orchestrator_mcp.py. stdio IS the
    wire protocol, so nothing here may ever print() to stdout -- only
    return values leave this process.
  - Fault injection can no longer be a Python dict (see workers.py's
    original `FAULT` global) because this is a separate OS process from
    whatever test script wants to flip a fault. See fault_control.py.
  - Token/cost usage for each worker's internal LLM calls happens in THIS
    process and is invisible to the manager's Tracer unless we hand it
    back explicitly -- so every tool return below includes a "usage" key
    the orchestrator pulls out and folds into its own ledger.

Run standalone for a quick manual check (prints nothing useful -- it only
speaks stdio -- so in practice this is launched by orchestrator_mcp.py's
MCPToolRouter, not run by hand):
    python3 squad_tools_server.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP
from openai import APIStatusError

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent / "mcp"
LEAF_CONFIG = MCP_DIR / "mcp_config.json"

sys.path.insert(0, str(HERE.parent))        # agent_race/      -- for pricing.py
sys.path.insert(0, str(HERE.parent.parent))  # developer-rag/   -- for config.py
sys.path.insert(0, str(MCP_DIR))            # agent_race/mcp/  -- for mcp_agent.MCPToolRouter

from config import GROK_API_KEY  # noqa: E402
from pricing import MODEL, cost_usd  # noqa: E402
from mcp_agent import MCPToolRouter  # noqa: E402 -- REUSED, not re-implemented
from openai import OpenAI  # noqa: E402

from fault_control import FAULT_FILE  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)

WORKER_TIMEOUT_S = 30.0
WORKER_MAX_LAPS = 8

ApiVersion = Literal["2022-11-28", "2025-06-01", "2.x", "3.x"]
Endpoint = Literal[
    "render_markdown", "create_issue", "create_pull_request", "create_repository", "create_release",
    "cli_general", "java_generator", "python_generator", "ignore_file", "config_file",
]

mcp = FastMCP(name="squad-workers")

VERSION_PROMPT = """You are the VERSION/DEPRECATION specialist for API documentation \
(GitHub REST API versions 2022-11-28 and 2025-06-01, and Swagger Codegen 2.x/3.x).
Your tools: search_docs (find which version/endpoint a topic lives in) and check_deprecation \
(what changed, was added, or was deprecated for one api_version + endpoint).
Answer ONLY what was asked about versions, changes and deprecations, using tool results. \
Name the exact version(s) each fact belongs to. If the docs do not contain the answer, say \
plainly that the docs do not contain it. Be concise: facts only, no code, no preamble."""

CODE_PROMPT = """You are the CODE-SAMPLE specialist for API documentation. Your tool: \
get_openapi_spec (the CURRENT parameter table for one api_version + endpoint).
Given a request, an api_version and an endpoint, call get_openapi_spec for that exact pair and \
report the parameter details the request needs (names, types, defaults, allowed values), and a \
minimal code/request sample if asked. Always state which api_version your facts come from. If the \
spec does not contain what was asked, say plainly that it does not. Be concise."""

PACKAGE_PROMPT = """You are the PACKAGE-REGISTRY specialist for this project's related tooling \
packages. Your tools: get_package_version (latest version of a named package), get_release_date \
(release date of one package+version), and check_package_deprecated (whether one package+version \
is deprecated and what to upgrade to).
Answer ONLY what was asked about package versions, release dates, and deprecation status. If the \
registry does not have the package or version, say plainly that it does not. Be concise."""

# New specialists go here as one more entry -- call_worker/_run_worker below
# never changes when this dict grows.
WORKERS = {
    "version_worker": {"prompt": VERSION_PROMPT, "tools": ["search_docs", "check_deprecation"]},
    "code_worker": {"prompt": CODE_PROMPT, "tools": ["get_openapi_spec"]},
    "package_worker": {"prompt": PACKAGE_PROMPT,
                        "tools": ["get_package_version", "get_release_date", "check_package_deprecated"]},
}

# ---- this process's OWN MCP client to the leaf doc/package servers --------
# Lazy singleton: connect once (spawns docs_search_server.py and
# package_registry_server.py as subprocesses of THIS process) and reuse the
# connection for every worker call, rather than reconnecting per call.
_leaf_router: Optional[MCPToolRouter] = None
_leaf_router_lock = asyncio.Lock()


async def _get_leaf_router() -> MCPToolRouter:
    global _leaf_router
    async with _leaf_router_lock:
        if _leaf_router is None:
            router = MCPToolRouter()
            await router.connect_all(LEAF_CONFIG, cwd=MCP_DIR)
            _leaf_router = router
    return _leaf_router


def _read_fault() -> dict:
    if not FAULT_FILE.exists():
        return {}
    try:
        return json.loads(FAULT_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _bump_fault_calls_seen(fault: dict) -> int:
    calls_seen = fault.get("_calls_seen", 0) + 1
    FAULT_FILE.write_text(json.dumps({**fault, "_calls_seen": calls_seen}), encoding="utf-8")
    return calls_seen


def _no_usage() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}


async def _run_worker(name: str, request: str, extra: Optional[dict] = None) -> dict:
    """Run one specialist worker's own ReAct loop. Returns
    {"status": int, "body": ..., "usage": {...}} -- the HTTP-status-shaped
    contract from the original workers.py is kept so a worker failure still
    looks and feels like a real microservice failure to the manager; "usage"
    is new -- it is how token/cost cross back over the process boundary so
    the orchestrator's Tracer still sees a true total (see module docstring).
    """
    fault = _read_fault()
    if fault.get("worker") == name and fault.get("mode"):
        calls_seen = _bump_fault_calls_seen(fault)
        if fault["mode"] == "persistent" or calls_seen == 1:
            return {"status": 500, "body": {"error": "Internal Server Error"}, "usage": _no_usage()}

    cfg = WORKERS[name]
    router = await _get_leaf_router()
    schema_by_name = {s["function"]["name"]: s for s in router.openai_schemas}
    schemas = [schema_by_name[t] for t in cfg["tools"] if t in schema_by_name]

    task = request
    for key, value in (extra or {}).items():
        if value:
            task += f"\n{key}: {value}"
    messages = [{"role": "system", "content": cfg["prompt"]}, {"role": "user", "content": task}]

    prompt_tok = completion_tok = 0
    cost = 0.0
    start = time.perf_counter()

    def usage_so_far() -> dict:
        return {"prompt_tokens": prompt_tok, "completion_tokens": completion_tok,
                "cost_usd": round(cost, 8), "latency_s": round(time.perf_counter() - start, 3)}

    for _lap in range(1, WORKER_MAX_LAPS + 1):
        if time.perf_counter() - start > WORKER_TIMEOUT_S:
            return {"status": 504, "body": {"error": f"{name} timed out after {WORKER_TIMEOUT_S:.0f}s"},
                    "usage": usage_so_far()}
        try:
            resp = _client.chat.completions.create(
                model=MODEL, messages=messages, tools=schemas, tool_choice="auto",
                temperature=0.0, max_tokens=1024,
            )
        except APIStatusError as e:
            if e.status_code == 413:
                return {"status": 413, "body": {"error": f"{name}: request too large for the model provider"},
                        "usage": usage_so_far()}
            raise

        u = resp.usage
        prompt_tok += u.prompt_tokens
        completion_tok += u.completion_tokens
        cost += cost_usd(MODEL, u.prompt_tokens, u.completion_tokens)

        msg = resp.choices[0].message
        assistant = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        messages.append(assistant)

        if not msg.tool_calls:
            return {"status": 200, "body": (msg.content or "").strip(), "usage": usage_so_far()}

        for tc in msg.tool_calls:
            tname = tc.function.name
            try:
                if tname not in cfg["tools"]:
                    raise ValueError(f"tool {tname} is not available to {name}")
                result = await router.call(tname, json.loads(tc.function.arguments))
            except Exception as e:  # tool errors go back to the worker as data
                result = {"error": f"{type(e).__name__}: {e}"}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})

    return {"status": 504, "body": {"error": f"{name} used all {WORKER_MAX_LAPS} laps without an answer"},
            "usage": usage_so_far()}


# ---------------------------------------------------------------------------
# The 3 worker-tools exposed to the manager. Each description is what the
# manager's LLM reads to decide which specialist fits a given question --
# same role MANAGER_TOOLS's hand-written descriptions played in
# orchestrator.py, just discovered live instead of hard-coded there.
# ---------------------------------------------------------------------------

@mcp.tool()
async def ask_version_worker(request: str, api_version: Optional[ApiVersion] = None,
                              endpoint: Optional[Endpoint] = None) -> dict:
    """Version/deprecation specialist: what changed, was added or deprecated for an
    api_version + endpoint, or search the docs to find where a topic lives when you
    don't yet know the exact api_version/endpoint. Pass api_version/endpoint when known;
    omit them to let the specialist search first."""
    return await _run_worker("version_worker", request, {"api_version": api_version, "endpoint": endpoint})


@mcp.tool()
async def ask_code_worker(request: str, api_version: ApiVersion, endpoint: Endpoint) -> dict:
    """Code-sample specialist: the CURRENT parameter table and a sample for one
    api_version + endpoint. Always pass both -- this specialist looks up an exact
    pair, it does not search."""
    return await _run_worker("code_worker", request, {"api_version": api_version, "endpoint": endpoint})


@mcp.tool()
async def ask_package_worker(request: str, package_name: Optional[str] = None,
                              package_version: Optional[str] = None) -> dict:
    """Package-registry specialist: latest package version, release dates, and
    deprecation status for this project's related tooling packages.

    NEW specialist, added after the original 2-worker squad, to demonstrate
    scaling to more agents: adding it required exactly one new WORKERS entry
    above and this one wrapper function -- orchestrator_mcp.py needed zero
    changes, because it discovers this tool via list_tools() like the other two."""
    return await _run_worker("package_worker", request,
                              {"package_name": package_name, "package_version": package_version})


if __name__ == "__main__":
    mcp.run(transport="stdio")
