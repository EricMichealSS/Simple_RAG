"""Fixed, deterministic 3-step workflow doing the identical task as agent.py --
same 3 tools, same model, same output contract ("ANSWER: ...") -- but no loop:
the step order is hard-coded in Python, not decided by an LLM call.

Step 1: search_docs(question)                          -- always run
Step 2: get_openapi_spec(top hit's api_version/endpoint) -- always run
Step 3: check_deprecation(top hit's api_version/endpoint) -- always run,
        unconditionally, even when the question doesn't need it (that's the
        cost of a fixed pipeline: no branching on what step 2 found)
Step 4: one plain (non-tool) LLM call synthesizes the final answer from
        whatever steps 1-3 returned.

No step's *choice of tool* depends on a model's decision. The only thing
step 2/3 take from step 1 is search_docs's top hit's metadata, read directly
in Python -- there is no "if the model thinks X, call Y" branching anywhere.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI  # noqa: E402

from config import GROK_API_KEY  # noqa: E402
from pricing import MODEL, cost_usd  # noqa: E402
from tools import search_docs, get_openapi_spec, check_deprecation  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)

SYNTHESIS_PROMPT = """You are a migration assistant for API documentation (GitHub REST API \
versions 2022-11-28 and 2025-06-01, and Swagger Codegen 2.x/3.x).

Answer the question below completely and precisely, using ONLY the retrieved information \
provided. Respond in exactly this format:

ANSWER: <your final answer>

QUESTION:
{question}

RETRIEVED INFORMATION:

[Step 1 -- search_docs top hit, page {page} of {source_file}, api_version={api_version}, endpoint={endpoint}]
{search_hit_text}

[Step 2 -- get_openapi_spec({api_version}, {endpoint})]
{spec_text}

[Step 3 -- check_deprecation({api_version}, {endpoint})]
{diff_text}
"""


def run_workflow(question: str) -> dict:
    log = []
    start = time.perf_counter()
    total_tokens = 0
    total_cost = 0.0

    # Step 1 -- fixed: always search_docs first, take the top hit.
    hits = search_docs(question, top_k=1)
    top = hits[0] if hits else {"text": "(no results)", "source_file": "?", "page": "?", "api_version": "unknown", "endpoint": "unknown"}
    log.append({"event": "tool_call", "step": 1, "tool": "search_docs", "args": {"query": question}, "result_preview": str(top)[:200]})

    # Step 2 -- fixed: always get_openapi_spec for whatever step 1 found.
    spec = get_openapi_spec(top["api_version"], top["endpoint"])
    log.append({"event": "tool_call", "step": 2, "tool": "get_openapi_spec",
                "args": {"api_version": top["api_version"], "endpoint": top["endpoint"]},
                "result_preview": str(spec)[:200]})

    # Step 3 -- fixed: ALWAYS check_deprecation too, unconditionally.
    diff = check_deprecation(top["api_version"], top["endpoint"])
    log.append({"event": "tool_call", "step": 3, "tool": "check_deprecation",
                "args": {"api_version": top["api_version"], "endpoint": top["endpoint"]},
                "result_preview": str(diff)[:200]})

    # Step 4 -- fixed: one synthesis call, no tools, same model as the agent.
    prompt = SYNTHESIS_PROMPT.format(
        question=question, page=top["page"], source_file=top["source_file"],
        api_version=top["api_version"], endpoint=top["endpoint"],
        search_hit_text=top["text"],
        spec_text=spec.get("text") or "(not found)",
        diff_text=diff.get("text") or "(no recorded change for this version/endpoint)",
    )
    resp = _client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": prompt}],
        temperature=0.0, max_tokens=512,
    )
    usage = resp.usage
    total_tokens += usage.total_tokens
    total_cost += cost_usd(MODEL, usage.prompt_tokens, usage.completion_tokens)
    log.append({"event": "llm_call", "step": 4, "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens, "cumulative_tokens": total_tokens,
                "cumulative_cost_usd": round(total_cost, 6)})

    return {
        "status": "pass", "answer": resp.choices[0].message.content,
        "iterations": 4, "total_tokens": total_tokens, "total_cost_usd": round(total_cost, 6),
        "wall_clock_s": round(time.perf_counter() - start, 3), "log": log,
    }


if __name__ == "__main__":
    import sys as _sys
    q = " ".join(_sys.argv[1:]) or (
        "In GitHub API version 2025-06-01, what parameter replaces the deprecated "
        "single assignee field when creating an issue?"
    )
    result = run_workflow(q)
    print(f"QUESTION: {q}\n")
    for entry in result["log"]:
        print(entry)
    print(f"\nstatus={result['status']} total_tokens={result['total_tokens']} "
          f"cost_usd={result['total_cost_usd']} wall_clock_s={result['wall_clock_s']}")
    print(f"\n{result['answer']}")
