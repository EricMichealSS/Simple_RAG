"""The Week 9 agent: same ReAct loop shape, same budgets, same system prompt
style as agent_race/agent.py -- but its tools are no longer imported from a
Python module. They're discovered live from whatever MCP servers are listed
in mcp_config.json, over the real Model Context Protocol.

This file is the thing Phase 4's "zero code changed" proof is about: adding
a second server to mcp_config.json should never require touching a single
line below.

Run standalone:
    python3 mcp_agent.py "your question here"
"""

import asyncio
import json
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent  # .../developer-rag
sys.path.insert(0, str(HERE.parent))  # agent_race/ -- for pricing.py
sys.path.insert(0, str(REPO_ROOT))    # developer-rag/ -- for config.py

from openai import OpenAI  # noqa: E402

from config import GROK_API_KEY  # noqa: E402
from pricing import MODEL, cost_usd  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)

CONFIG_PATH = HERE / "mcp_config.json"

SYSTEM_PROMPT = """You are a migration assistant for API documentation (GitHub REST API \
versions 2022-11-28 and 2025-06-01, and Swagger Codegen 2.x/3.x), and for package version \
lookups if a package-registry tool is available to you.

Every tool you have access to was discovered dynamically over MCP -- you were not told about \
them in advance in this prompt; see the "tools" list attached to this request for the complete, \
current set. Call as many tools as you genuinely need, in any order, across multiple turns. \
When -- and only when -- you have enough information to answer completely, respond with plain \
text in exactly this format and do not call any more tools:

ANSWER: <your final answer>
"""


def _build_default_budgets():
    return dict(max_iterations=8, max_tokens=8000, max_cost_usd=0.01, max_wall_clock_s=60.0)


def _mcp_tool_to_openai_schema(tool, server_name: str) -> dict:
    """Convert one MCP Tool (name, description, inputSchema=JSON Schema) into
    the OpenAI-style function-calling schema the Groq/OpenAI-compatible API
    expects. This is the only "translation" needed -- MCP's inputSchema
    already IS JSON Schema, so nothing about the shape needs to change, only
    the wrapping.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": f"[via MCP server '{server_name}'] {tool.description or ''}",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


class MCPToolRouter:
    """Connects to every server in mcp_config.json, discovers its tools, and
    routes a tool call by name to whichever server actually owns it. This
    class is the only thing standing in for the old `from tools import
    TOOL_SCHEMAS, TOOL_IMPLS` line -- everything about which tools exist
    comes from live `tools/list` calls here, not from anything hard-coded.
    """

    def __init__(self):
        self._stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}   # server_name -> session
        self.tool_owner: dict[str, str] = {}           # tool_name -> server_name
        self.openai_schemas: list = []

    async def connect_all(self, config_path: Path = CONFIG_PATH):
        config = json.loads(config_path.read_text())
        for server_name, server_cfg in config["mcpServers"].items():
            params = StdioServerParameters(command=server_cfg["command"], args=server_cfg["args"], cwd=str(HERE))
            try:
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
            except Exception as e:
                # Edge case found by testing (see PHASE8 doc): a server that fails to even
                # start (bad command/args) otherwise surfaces as a messy internal anyio
                # traceback with the real cause buried at the top. Fail loudly but clearly
                # instead, naming exactly which server and config entry is at fault.
                raise RuntimeError(
                    f"Failed to connect to MCP server '{server_name}' "
                    f"(command={server_cfg['command']!r}, args={server_cfg['args']!r}) "
                    f"defined in {config_path}: {type(e).__name__}: {e}"
                ) from e
            self.sessions[server_name] = session

            listed = await session.list_tools()
            for tool in listed.tools:
                if tool.name in self.tool_owner:
                    raise ValueError(
                        f"Tool name collision: '{tool.name}' is offered by both "
                        f"'{self.tool_owner[tool.name]}' and '{server_name}'"
                    )
                self.tool_owner[tool.name] = server_name
                self.openai_schemas.append(_mcp_tool_to_openai_schema(tool, server_name))

    async def call(self, tool_name: str, arguments: dict):
        """Real edge case found by testing (see PHASE2 doc): when a tool
        returns a Python list, FastMCP emits ONE TextContent block PER LIST
        ITEM, not a single JSON array -- naively joining all blocks and
        json.loads()-ing the result breaks (concatenated JSON objects are
        not valid JSON). dict-returning tools only ever produce one block,
        so this distinction is invisible until a list-returning tool
        (search_docs) is actually exercised.
        """
        server_name = self.tool_owner.get(tool_name)
        if server_name is None:
            return {"error": f"no connected server offers a tool named '{tool_name}'"}
        session = self.sessions[server_name]
        result = await session.call_tool(tool_name, arguments)
        text_parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
        if not text_parts:
            return {"raw": str(result.content)}

        parsed = []
        for part in text_parts:
            try:
                parsed.append(json.loads(part))
            except (json.JSONDecodeError, TypeError):
                parsed.append(part)
        # One block -> the tool returned a single dict, unwrap it back to
        # that object. Multiple blocks -> the tool returned a list,
        # reassemble them into the list they came from.
        return parsed[0] if len(parsed) == 1 else parsed

    async def aclose(self):
        await self._stack.aclose()


async def run_agent(question: str, config_path: Path = CONFIG_PATH, **budget_overrides) -> dict:
    budgets = _build_default_budgets()
    budgets.update(budget_overrides)

    router = MCPToolRouter()
    await router.connect_all(config_path)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    log = [{"event": "tools_discovered", "count": len(router.openai_schemas),
            "names": list(router.tool_owner.keys()), "owners": dict(router.tool_owner)}]
    start = time.perf_counter()
    total_tokens = 0
    total_cost = 0.0

    def finish(status, budget=None, answer=None, iteration=0):
        return {
            "status": status, "budget_hit": budget, "answer": answer,
            "iterations": iteration, "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "wall_clock_s": round(time.perf_counter() - start, 3),
            "tools_discovered": len(router.openai_schemas),
            "tool_names": list(router.tool_owner.keys()),
            "log": log,
        }

    try:
        for iteration in range(1, budgets["max_iterations"] + 1):
            elapsed = time.perf_counter() - start
            if elapsed > budgets["max_wall_clock_s"]:
                log.append({"event": "budget_exceeded", "budget": "max_wall_clock_s", "elapsed_s": round(elapsed, 2)})
                return finish("budget_exceeded", "max_wall_clock_s", iteration=iteration - 1)

            resp = _client.chat.completions.create(
                model=MODEL, messages=messages, tools=router.openai_schemas, tool_choice="auto",
                temperature=0.0, max_tokens=1024,
            )
            usage = resp.usage
            total_tokens += usage.total_tokens
            total_cost += cost_usd(MODEL, usage.prompt_tokens, usage.completion_tokens)
            log.append({
                "event": "llm_call", "iteration": iteration,
                "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                "cumulative_tokens": total_tokens, "cumulative_cost_usd": round(total_cost, 6),
            })

            if total_tokens > budgets["max_tokens"]:
                log.append({"event": "budget_exceeded", "budget": "max_tokens", "total_tokens": total_tokens})
                return finish("budget_exceeded", "max_tokens", iteration=iteration)
            if total_cost > budgets["max_cost_usd"]:
                log.append({"event": "budget_exceeded", "budget": "max_cost_usd", "total_cost_usd": round(total_cost, 6)})
                return finish("budget_exceeded", "max_cost_usd", iteration=iteration)

            msg = resp.choices[0].message
            assistant_msg = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            messages.append(assistant_msg)

            if not msg.tool_calls:
                return finish("pass", answer=msg.content, iteration=iteration)

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                    result = await router.call(name, args)
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
                log.append({"event": "tool_call", "iteration": iteration, "tool": name,
                            "server": router.tool_owner.get(name), "args": tc.function.arguments,
                            "result_preview": str(result)[:200]})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})

        log.append({"event": "budget_exceeded", "budget": "max_iterations", "limit": budgets["max_iterations"]})
        return finish("budget_exceeded", "max_iterations", iteration=budgets["max_iterations"])
    finally:
        await router.aclose()


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or (
        "In GitHub API version 2025-06-01, what parameter replaces the deprecated "
        "single assignee field when creating an issue?"
    )
    result = asyncio.run(run_agent(q))
    print(f"QUESTION: {q}\n")
    for entry in result["log"]:
        print(entry)
    print(f"\nstatus={result['status']} budget_hit={result['budget_hit']} "
          f"iterations={result['iterations']} total_tokens={result['total_tokens']} "
          f"cost_usd={result['total_cost_usd']} wall_clock_s={result['wall_clock_s']} "
          f"tools_discovered={result['tools_discovered']}")
    print(f"\n{result['answer']}")
