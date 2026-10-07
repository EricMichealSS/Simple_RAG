"""The two specialist workers. Each is a small ReAct loop with a narrow
prompt and a SUBSET of the single agent's tools (that narrowness is the only
plausible source of a squad win, so it is not diluted):

    version_worker : search_docs, check_deprecation   -> what changed / deprecated
    code_worker    : get_openapi_spec                  -> current parameter table

Workers are in-process but behind an HTTP-shaped contract: they return
{"status": <int>, "body": <str|dict>}. status >= 400 means the call failed,
exactly as a remote worker's 500 would. The manager is given the whole
object and decides what to do (we add no failure handling here or there).

Fault injection lives in `FAULT` below and is OFF unless a test turns it on.
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

from metering import metered_chat, active_now  # noqa: E402
from tools import TOOL_SCHEMAS, TOOL_IMPLS  # noqa: E402

WORKER_TIMEOUT_S = 30.0
WORKER_MAX_LAPS = 8

_SCHEMA_BY_NAME = {s["function"]["name"]: s for s in TOOL_SCHEMAS}

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

WORKERS = {
    "version_worker": {"prompt": VERSION_PROMPT, "tools": ["search_docs", "check_deprecation"]},
    "code_worker": {"prompt": CODE_PROMPT, "tools": ["get_openapi_spec"]},
}

# ---- fault injection (default: off) --------------------------------------
FAULT = {"worker": None, "mode": None, "calls_seen": 0}


def set_fault(worker=None, mode=None):
    """mode: 'persistent' = every call to `worker` returns 500;
             'transient'  = only the first call returns 500."""
    FAULT.update(worker=worker, mode=mode, calls_seen=0)


def _injected_500(name):
    if FAULT["worker"] != name or FAULT["mode"] is None:
        return False
    FAULT["calls_seen"] += 1
    return FAULT["mode"] == "persistent" or FAULT["calls_seen"] == 1


def call_worker(tracer, name, request, api_version, endpoint, guard):
    """Run worker `name` once. Returns {"status": int, "body": ...}."""
    if _injected_500(name):
        # a service that is down costs us no tokens, like a real 500
        tracer.record(name, f"orchestrator -> {name} (injected 500, no model call)", 0, 0, 0.0, status="http_500")
        return {"status": 500, "body": {"error": "Internal Server Error"}}

    cfg = WORKERS[name]
    task = request
    if api_version:
        task += f"\napi_version: {api_version}"
    if endpoint:
        task += f"\nendpoint: {endpoint}"
    messages = [{"role": "system", "content": cfg["prompt"]}, {"role": "user", "content": task}]
    schemas = [_SCHEMA_BY_NAME[t] for t in cfg["tools"]]
    start = active_now()

    for lap in range(1, WORKER_MAX_LAPS + 1):
        if active_now() - start > WORKER_TIMEOUT_S:
            return {"status": 504, "body": {"error": f"{name} timed out after {WORKER_TIMEOUT_S:.0f}s"}}
        reason = guard()
        if reason:
            return {"status": 503, "body": {"error": f"ceiling reached: {reason}"}}
        label = f"orchestrator -> {name} (task in)" if lap == 1 else f"{name} tool-loop lap {lap}"
        try:
            resp = metered_chat(tracer, name, label, messages, tools=schemas)
        except APIStatusError as e:
            if e.status_code == 413:
                return {"status": 413, "body": {"error": f"{name}: request too large for the model provider"}}
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
        if not msg.tool_calls:
            return {"status": 200, "body": (msg.content or "").strip()}
        for tc in msg.tool_calls:
            tname = tc.function.name
            try:
                if tname not in cfg["tools"]:
                    raise ValueError(f"tool {tname} is not available to {name}")
                result = TOOL_IMPLS[tname](**json.loads(tc.function.arguments))
            except Exception as e:  # tool errors go back to the worker as data
                result = {"error": f"{type(e).__name__}: {e}"}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    return {"status": 504, "body": {"error": f"{name} used all {WORKER_MAX_LAPS} laps without an answer"}}
