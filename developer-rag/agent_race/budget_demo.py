"""Requirement 4 deliverable: a log of one run that hits a budget and
terminates cleanly instead of spinning. Deliberately sets max_iterations=1
on a hard, genuinely multi-step question -- the model will want to call at
least 2 tools (get_openapi_spec, then check_deprecation) before it has
enough to answer, so capping the loop at 1 iteration forces a real,
observable budget termination, not a contrived one.
"""

import json
from pathlib import Path

from agent import run_agent

QUESTION = "How does the make_latest parameter in GitHub API version 2025-06-01 release creation replace the undocumented heuristic used in 2022-11-28?"


def main():
    result = run_agent(QUESTION, max_iterations=1)
    print(f"status: {result['status']}")
    print(f"budget_hit: {result['budget_hit']}")
    print(f"iterations completed: {result['iterations']}")
    print(f"total_tokens: {result['total_tokens']}")
    print(f"total_cost_usd: {result['total_cost_usd']}")
    print(f"wall_clock_s: {result['wall_clock_s']}")
    print(f"answer: {result['answer']}")
    print()
    for entry in result["log"]:
        print(entry)

    out_path = Path(__file__).parent / "logs" / "budget_termination_max_iterations.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nFull log -> {out_path}")


if __name__ == "__main__":
    main()
