"""The manager (orchestrator), MCP-client version. Same ReAct loop shape,
same budgets, same delegation-cap logic as orchestrator.py -- the ONLY
structural change is where MANAGER_TOOLS comes from and how a chosen worker
actually gets called.

orchestrator.py (original, Week 10):
    MANAGER_TOOLS = [ ...2 hand-typed dicts... ]
    _WORKER_FOR_TOOL = {"ask_version_worker": "version_worker", ...}
    out = call_worker(tracer, wname, ...)              # direct Python call

orchestrator_mcp.py (this file):
    router = MCPToolRouter(); await router.connect_all(squad_mcp_config.json)
    MANAGER_TOOLS  -->  router.openai_schemas           # discovered live
    out = await router.call(tool_name, args)            # MCP tool call

This is the direct implementation of "pass manager_tools via an MCP
connection": the manager no longer knows the worker list in advance -- it
asks squad_tools_server.py what it offers, the same way mcp_agent.py (Week
9) asks docs_search_server.py what it offers. Adding a 4th worker later
needs ZERO edits in this file: add one @mcp.tool() function to
squad_tools_server.py (and a new mcpServers entry in squad_mcp_config.json
only if it lives on a brand-new server process).

See mcp_tool_routing_upgrade.md for the full write-up, including two real
issues this reuse surfaced (process-boundary budget enforcement, and
cross-process token/cost metering) and how each was resolved below.

Run standalone:
    python3 orchestrator_mcp.py "your question here"
"""

import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE.parent / "mcp"))  # reuse MCPToolRouter from Week 9, not re-implemented

from openai import APIStatusError  # noqa: E402

from metering import Tracer, metered_chat  # noqa: E402
from mcp_agent import MCPToolRouter  # noqa: E402

SQUAD_CONFIG = HERE / "squad_mcp_config.json"

CEILINGS = dict(max_tokens=60000, max_cost_usd=0.05, max_wall_clock_s=120.0, max_iterations=8, max_delegations=6)

MANAGER_PROMPT = """You are the manager of a documentation squad answering questions about API \
versions (GitHub REST API 2022-11-28 and 2025-06-01, and Swagger Codegen 2.x/3.x), and about \
package-registry lookups when a package specialist is available. You have no documentation \
access yourself; you get facts only by delegating to specialists.

Every specialist you can delegate to was discovered dynamically over MCP -- you were not told \
about them by name in advance in this prompt; see the "tools" list attached to this request for \
the complete, current set. Delegate only what you need, then write the final answer from the \
specialists' reports. When you have enough, respond with plain text in exactly this format and \
delegate no further:

ANSWER: <your final answer>
"""


async def run_squad(question: str, race_id: str = "adhoc", repeat: int = 0, sink=None, **overrides) -> dict:
    c = dict(CEILINGS)
    c.update(overrides)
    tracer = Tracer("squad_mcp", race_id, repeat, sink=sink)
    log = []
    delegations = 0

    router = MCPToolRouter()
    await router.connect_all(SQUAD_CONFIG, cwd=HERE)

    def guard():
        """Same ceilings as orchestrator.py's guard(). IMPORTANT LIMIT, not
        present in the original: this can only refuse to START another
        delegation -- it can no longer abort a worker's MCP call already in
        flight, because that call is a real subprocess on the other end of a
        stdio pipe, not a Python object this process can reach into. Each
        worker enforces its OWN local ceiling independently instead (see
        WORKER_TIMEOUT_S / WORKER_MAX_LAPS in squad_tools_server.py)."""
        t = tracer.totals()
        if t["total_tokens"] > c["max_tokens"]:
            return "max_tokens"
        if t["total_cost_usd"] > c["max_cost_usd"]:
            return "max_cost_usd"
        if tracer.elapsed() > c["max_wall_clock_s"]:
            return "max_wall_clock_s"
        return None

    def finish(status, budget=None, answer=None, iteration=0):
        t = tracer.totals()
        return {"status": status, "budget_hit": budget, "answer": answer, "iterations": iteration,
                "total_tokens": t["total_tokens"], "total_cost_usd": t["total_cost_usd"],
                "wall_clock_s": round(tracer.elapsed(), 3), "delegations": delegations,
                "trace_id": tracer.trace_id, "tracer": tracer, "log": log,
                "tools_discovered": len(router.openai_schemas),
                "tool_names": list(router.tool_owner.keys())}

    messages = [{"role": "system", "content": MANAGER_PROMPT}, {"role": "user", "content": question}]
    last_tools = []
    try:
        for lap in range(1, c["max_iterations"] + 1):
            reason = guard()
            if reason:
                return finish("budget_exceeded", reason, iteration=lap - 1)
            label = "user -> orchestrator" if lap == 1 else \
                " + ".join(f"{t} -> orchestrator" for t in last_tools) + " (results read)"
            try:
                resp = metered_chat(tracer, "orchestrator", label, messages, tools=router.openai_schemas)
            except APIStatusError as e:
                if e.status_code == 413:
                    return finish("budget_exceeded", "provider_413_request_too_large", iteration=lap)
                raise
            msg = resp.choices[0].message
            assistant = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                assistant["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            messages.append(assistant)
            reason = guard()
            if reason:
                return finish("budget_exceeded", reason, iteration=lap)
            if not msg.tool_calls:
                return finish("pass", answer=msg.content, iteration=lap)

            last_tools = []
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                    if isinstance(args, dict):
                        args = {k: v for k, v in args.items() if v is not None}  # models send null for "omit"
                except json.JSONDecodeError:
                    args = None

                if tool_name not in router.tool_owner or not isinstance(args, dict):
                    out = {"status": 400, "body": {"error": "unknown tool or malformed arguments"}}
                elif delegations >= c["max_delegations"]:
                    out = {"status": 429, "body": {"error": f"delegation cap of {c['max_delegations']} reached"}}
                else:
                    delegations += 1
                    t0 = time.perf_counter()
                    out = await router.call(tool_name, args)
                    wire_latency = time.perf_counter() - t0

                    # Fold the worker's OWN token/cost usage into this
                    # process's tracer -- without this, total_tokens/
                    # total_cost_usd would only reflect the manager's own
                    # reasoning calls and silently understate the true cost,
                    # since the worker's LLM calls happened in a different
                    # OS process this tracer never saw directly.
                    usage = out.pop("usage", None) if isinstance(out, dict) else None
                    if usage:
                        tracer.record(tool_name, f"orchestrator -> {tool_name} (worker-internal, folded)",
                                      usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                                      usage.get("latency_s", wire_latency), status="ok")
                    else:
                        # injected-fault or malformed response: no tokens were spent, but
                        # still record the call so the trace shows it happened.
                        tracer.record(tool_name, f"orchestrator -> {tool_name} (no usage reported)", 0, 0,
                                      wire_latency, status="ok")

                last_tools.append(tool_name)
                log.append({"event": "delegation", "lap": lap, "tool": tool_name, "args": args,
                            "status": out.get("status") if isinstance(out, dict) else None,
                            "result_preview": str(out)[:200]})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out)})

        return finish("budget_exceeded", "max_iterations", iteration=c["max_iterations"])
    finally:
        await router.aclose()


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "In GitHub API version 2025-06-01, what parameter replaces the deprecated single assignee field when creating an issue?"
    r = asyncio.run(run_squad(q))
    for e in r["log"]:
        print(e)
    for row in r["tracer"].calls:
        print(row["seq"], row["caller"], "|", row["handoff"], "|", row["prompt_tokens"], row["completion_tokens"], row["latency_s"])
    print(f"status={r['status']} tokens={r['total_tokens']} cost={r['total_cost_usd']} "
          f"wall={r['wall_clock_s']} delegations={r['delegations']} tools_discovered={r['tools_discovered']} "
          f"tool_names={r['tool_names']}")
    print(r["answer"])
