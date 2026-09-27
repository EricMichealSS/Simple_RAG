"""Hand-built ReAct agent loop. No framework (no LangChain/LangGraph) --
the loop itself is the ~50 lines below `run_agent`'s docstring; everything
above it is tool wiring and the system prompt.

Loop shape: plan (LLM call with tool schemas) -> act (execute any tool calls
it made) -> look at the result (append as tool messages) -> repeat, until the
model returns a plain text answer with no tool calls, or a budget fires.

All 4 budgets are checked EVERY iteration, before doing more work:
  - max_iterations : hard cap on loop laps
  - max_tokens     : cumulative across ALL laps (every lap resends the full
                     message history, so tokens are summed, not just the
                     last call's -- see Common Mistakes in the brief)
  - max_cost_usd   : cumulative $ from pricing.cost_usd()
  - max_wall_clock_s: real elapsed time since the question started
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI  # noqa: E402

from config import GROK_API_KEY  # noqa: E402
from pricing import MODEL, cost_usd  # noqa: E402
from tools import TOOL_SCHEMAS, TOOL_IMPLS  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)

SYSTEM_PROMPT = """You are a migration assistant for API documentation (GitHub REST API \
versions 2022-11-28 and 2025-06-01, and Swagger Codegen 2.x/3.x).

Answer the user's question completely and precisely using the tools available:
- search_docs: use this FIRST if you don't yet know the exact api_version/endpoint.
- get_openapi_spec: the CURRENT parameter table for one (api_version, endpoint) pair.
- check_deprecation: what changed/was deprecated for one (api_version, endpoint) pair.

Call as many tools as you genuinely need, in any order, across multiple turns. When -- and \
only when -- you have enough information to answer completely, respond with plain text in \
exactly this format and do not call any more tools:

ANSWER: <your final answer>
"""

# Week 8 mitigation for the "incomplete_grounding" failure mode: the baseline
# prompt above let the model treat search_docs's preview snippet as good
# enough to answer from directly, skipping the authoritative spec/diff tool
# entirely -- a right answer today, unverified against a source that WILL
# change. This is the only change made for the mitigation; everything else
# (tools, budgets, loop shape) is untouched.
SYSTEM_PROMPT_MITIGATED = SYSTEM_PROMPT + """
IMPORTANT: search_docs returns only a short preview snippet to help you locate the right
(api_version, endpoint) pair -- it is NOT the authoritative record, even when it looks like it
already contains the answer. Before writing your final ANSWER, you must have called
get_openapi_spec (for a current-parameter question) and/or check_deprecation (for a
what-changed/deprecated question) for the specific pair the question is about. Never finalize
an answer using only a search_docs result.
"""

# Follow-up experiment (not part of the graded Week 8 mitigation -- that was
# exactly one change, this is a separate, later, optional efficiency test):
# encourage batching tool calls the model already knows it needs, instead of
# spending one whole lap per tool.
PARALLEL_HINT = """
EFFICIENCY: if you already know, from the question itself, that you will need more than one
tool call (for example both get_openapi_spec and check_deprecation for the same
api_version/endpoint), request them together in the same response instead of one at a time
across multiple turns.
"""


def _build_default_budgets():
    return dict(max_iterations=8, max_tokens=8000, max_cost_usd=0.01, max_wall_clock_s=60.0)


def _compact_messages(messages: list, keep_last_n_tool_msgs: int = 2, max_len: int = 180) -> list:
    """Return a COPY of messages where all but the most recent N tool-result
    messages have their content truncated. The model already made its
    decision after seeing a tool result once; it doesn't need the full text
    resent, in full, on every subsequent lap forever. Does not mutate the
    original `messages` -- the caller keeps the untouched full history for
    logging/trajectory analysis; only what's sent to the API is compacted.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    keep = set(tool_indices[-keep_last_n_tool_msgs:]) if tool_indices else set()
    compacted = []
    for i, m in enumerate(messages):
        if m.get("role") == "tool" and i not in keep and len(m["content"]) > max_len:
            compacted.append({**m, "content": m["content"][:max_len] + "...[earlier result, truncated]"})
        else:
            compacted.append(m)
    return compacted


def run_agent(question: str, mitigated: bool = False, parallel_hint: bool = False,
              compact_history: bool = False, **budget_overrides) -> dict:
    budgets = _build_default_budgets()
    budgets.update(budget_overrides)

    system_prompt = SYSTEM_PROMPT_MITIGATED if mitigated else SYSTEM_PROMPT
    if parallel_hint:
        system_prompt = system_prompt + PARALLEL_HINT

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    log = []
    start = time.perf_counter()
    total_tokens = 0
    total_cost = 0.0

    def finish(status, budget=None, answer=None, iteration=0):
        return {
            "status": status, "budget_hit": budget, "answer": answer,
            "iterations": iteration, "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "wall_clock_s": round(time.perf_counter() - start, 3),
            "log": log,
        }

    for iteration in range(1, budgets["max_iterations"] + 1):
        elapsed = time.perf_counter() - start
        if elapsed > budgets["max_wall_clock_s"]:
            log.append({"event": "budget_exceeded", "budget": "max_wall_clock_s", "elapsed_s": round(elapsed, 2)})
            return finish("budget_exceeded", "max_wall_clock_s", iteration=iteration - 1)

        messages_for_api = _compact_messages(messages) if compact_history else messages
        resp = _client.chat.completions.create(
            model=MODEL, messages=messages_for_api, tools=TOOL_SCHEMAS, tool_choice="auto",
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
                result = TOOL_IMPLS[name](**args)
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            log.append({"event": "tool_call", "iteration": iteration, "tool": name,
                        "args": tc.function.arguments, "result_preview": str(result)[:200]})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})

    log.append({"event": "budget_exceeded", "budget": "max_iterations", "limit": budgets["max_iterations"]})
    return finish("budget_exceeded", "max_iterations", iteration=budgets["max_iterations"])


if __name__ == "__main__":
    import sys as _sys
    q = " ".join(_sys.argv[1:]) or (
        "In GitHub API version 2025-06-01, what parameter replaces the deprecated "
        "single assignee field when creating an issue?"
    )
    result = run_agent(q)
    print(f"QUESTION: {q}\n")
    for entry in result["log"]:
        print(entry)
    print(f"\nstatus={result['status']} budget_hit={result['budget_hit']} "
          f"iterations={result['iterations']} total_tokens={result['total_tokens']} "
          f"cost_usd={result['total_cost_usd']} wall_clock_s={result['wall_clock_s']}")
    print(f"\n{result['answer']}")
