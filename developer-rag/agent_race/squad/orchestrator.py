"""The orchestrator (manager). A ReAct loop whose only 'tools' are the two
workers. It has no docs tools of its own and NO failure-handling code or
failure instruction in its prompt: whatever it does when a worker returns a
500 is its own behaviour, which is what the failure test measures.

Same ceilings as the single agent (protocol.md): 60,000 tokens, $0.05,
120 s, 8 laps for the manager loop, plus at most 6 delegations.
Returns the same result shape as agent.run_agent, plus the trace.
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from openai import APIStatusError  # noqa: E402

from metering import Tracer, metered_chat  # noqa: E402
from workers import call_worker  # noqa: E402
from tools import KNOWN_ENDPOINTS, KNOWN_API_VERSIONS  # noqa: E402

CEILINGS = dict(max_tokens=60000, max_cost_usd=0.05, max_wall_clock_s=120.0, max_iterations=8, max_delegations=6)

MANAGER_PROMPT = f"""You are the manager of a documentation squad answering questions about API \
versions (GitHub REST API 2022-11-28 and 2025-06-01, and Swagger Codegen 2.x/3.x). You have no \
documentation access yourself; you get facts only by delegating to specialists:
- ask_version_worker: what changed / was added / was deprecated between versions, and it can \
search the docs when you do not know which version or endpoint a topic belongs to.
- ask_code_worker: the CURRENT parameter table (names, types, defaults, allowed values) and code \
samples for one api_version + endpoint.
Known api_versions: {KNOWN_API_VERSIONS}. Known endpoints: {KNOWN_ENDPOINTS}.
Always pass api_version and endpoint when you know them. Delegate only what you need, then write \
the final answer from the workers' reports. When you have enough, respond with plain text in \
exactly this format and delegate no further:

ANSWER: <your final answer>
"""

MANAGER_TOOLS = [
    {"type": "function", "function": {
        "name": "ask_version_worker",
        "description": "Version/deprecation specialist: what changed, was added or deprecated for an "
                       "api_version + endpoint, or search the docs to find where a topic lives.",
        "parameters": {"type": "object", "properties": {
            "request": {"type": "string", "description": "The precise question for the worker."},
            "api_version": {"type": "string", "description": f"One of {KNOWN_API_VERSIONS} if known; omit if unknown."},
            "endpoint": {"type": "string", "description": f"One of {KNOWN_ENDPOINTS} if known; omit if unknown."},
        }, "required": ["request"]}}},
    {"type": "function", "function": {
        "name": "ask_code_worker",
        "description": "Code-sample specialist: the CURRENT parameter table and a sample for one "
                       "api_version + endpoint.",
        "parameters": {"type": "object", "properties": {
            "request": {"type": "string", "description": "What parameter details or sample you need."},
            "api_version": {"type": "string", "enum": KNOWN_API_VERSIONS},
            "endpoint": {"type": "string", "enum": KNOWN_ENDPOINTS},
        }, "required": ["request", "api_version", "endpoint"]}}},
]
_WORKER_FOR_TOOL = {"ask_version_worker": "version_worker", "ask_code_worker": "code_worker"}


def run_squad(question, race_id="adhoc", repeat=0, sink=None, **overrides):
    c = dict(CEILINGS)
    c.update(overrides)
    tracer = Tracer("squad", race_id, repeat, sink=sink)
    log = []
    delegations = 0

    def guard():
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
                "trace_id": tracer.trace_id, "tracer": tracer, "log": log}

    messages = [{"role": "system", "content": MANAGER_PROMPT}, {"role": "user", "content": question}]
    last_workers = []
    for lap in range(1, c["max_iterations"] + 1):
        reason = guard()
        if reason:
            return finish("budget_exceeded", reason, iteration=lap - 1)
        if lap == 1:
            label = "user -> orchestrator"
        else:
            label = " + ".join(f"{w} -> orchestrator" for w in last_workers) + " (results read)"
        try:
            resp = metered_chat(tracer, "orchestrator", label, messages, tools=MANAGER_TOOLS)
        except APIStatusError as e:
            if e.status_code == 413:  # provider per-request size cap: deterministic, counts as a failed run
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

        last_workers = []
        for tc in msg.tool_calls:
            wname = _WORKER_FOR_TOOL.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments)
                if isinstance(args, dict):
                    args = {k: v for k, v in args.items() if v is not None}  # models send null for "omit"
            except json.JSONDecodeError:
                args = None
            if wname is None or not isinstance(args, dict) or "request" not in args:
                out = {"status": 400, "body": {"error": "unknown tool or malformed arguments"}}
            elif delegations >= c["max_delegations"]:
                out = {"status": 429, "body": {"error": f"delegation cap of {c['max_delegations']} reached"}}
            else:
                delegations += 1
                out = call_worker(tracer, wname, args["request"], args.get("api_version"),
                                  args.get("endpoint"), guard)
            if wname:
                last_workers.append(wname)
            log.append({"event": "delegation", "lap": lap, "worker": wname, "args": args,
                        "status": out["status"], "result_preview": str(out["body"])[:200]})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out)})
    return finish("budget_exceeded", "max_iterations", iteration=c["max_iterations"])


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "In GitHub API version 2025-06-01, what parameter replaces the deprecated single assignee field when creating an issue?"
    r = run_squad(q)
    for e in r["log"]:
        print(e)
    for row in r["tracer"].calls:
        print(row["seq"], row["caller"], "|", row["handoff"], "|", row["prompt_tokens"], row["completion_tokens"], row["latency_s"])
    print(f"status={r['status']} tokens={r['total_tokens']} cost={r['total_cost_usd']} wall={r['wall_clock_s']} delegations={r['delegations']}")
    print(r["answer"])
