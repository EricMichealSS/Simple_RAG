"""Controlled before/after test for the recoverable-error rewrite
(Requirement 5). Isolates exactly one variable: given the IDENTICAL
conversation up to and including a wrong-direction check_deprecation call,
does the model behave differently depending only on what that tool's error
message says?

"Before" = the original Week 7/8 message, still literally present in
agent_race/tools.py's check_deprecation():
    {"found": False, "text": None, "note": "No recorded change/deprecation for this version+endpoint."}

"After" = this week's rewrite, from docs_search_server.py's check_deprecation():
    {"found": False, "note": "...this usually means either (a) you passed the OLDER
     of two versions being compared -- try the newer one instead, or (b) nothing
     changed..."}

Both are fed to the model in an otherwise-identical conversation, and its
next response is captured verbatim for both.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from openai import OpenAI  # noqa: E402

from config import GROK_API_KEY  # noqa: E402
from pricing import MODEL  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)

QUESTION = (
    "What changed about the assignee field for creating an issue between GitHub API "
    "version 2022-11-28 and 2025-06-01?"
)

# The identical prior turns for both runs: the model has already (mistakenly)
# called check_deprecation with the OLDER version -- this is what we're
# testing the RECOVERY from, not whether the model makes this mistake.
PRIOR_TURNS = [
    {"role": "system", "content": (
        "You are a migration assistant for GitHub REST API documentation "
        "(versions 2022-11-28 and 2025-06-01). Use tools to answer precisely. "
        "When you have enough information, answer in the format: ANSWER: <answer>"
    )},
    {"role": "user", "content": QUESTION},
    {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "check_deprecation", "arguments": json.dumps({"api_version": "2022-11-28", "endpoint": "create_issue"})},
    }]},
]

BEFORE_TOOL_RESULT = {"found": False, "text": None, "note": "No recorded change/deprecation for this version+endpoint."}
AFTER_TOOL_RESULT = {
    "found": False,
    "note": (
        "No recorded change for (2022-11-28, create_issue). This usually means either "
        "(a) you passed the OLDER of two versions being compared -- try the newer one instead, or "
        "(b) nothing changed for this endpoint between versions."
    ),
}


TOOL_SCHEMA = [{
    "type": "function",
    "function": {
        "name": "check_deprecation",
        "description": "What changed for one (api_version, endpoint) pair.",
        "parameters": {"type": "object", "properties": {
            "api_version": {"type": "string", "enum": ["2022-11-28", "2025-06-01", "2.x", "3.x"]},
            "endpoint": {"type": "string", "enum": ["create_issue"]},
        }, "required": ["api_version", "endpoint"]},
    },
}]


def run_one(tool_result: dict, temperature: float = 0.7):
    messages = PRIOR_TURNS + [
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps(tool_result)},
    ]
    resp = _client.chat.completions.create(
        model=MODEL, messages=messages, temperature=temperature, max_tokens=1024,
        tools=TOOL_SCHEMA, tool_choice="auto",
    )
    msg = resp.choices[0].message
    if msg.tool_calls:
        tc = msg.tool_calls[0]
        args = json.loads(tc.function.arguments)
        retried_correctly = args.get("api_version") == "2025-06-01" and args.get("endpoint") == "create_issue"
        return {"outcome": "retried_tool", "retried_correctly": retried_correctly,
                "call": f"{tc.function.name}({tc.function.arguments})"}
    content = msg.content or ""
    return {"outcome": "answered", "confident": "2025-06-01" in content or "assignees" in content.lower(),
            "text": content}


def main():
    print("QUESTION:", QUESTION)
    print("Both runs share identical prior turns: the model already (mistakenly) called")
    print("check_deprecation(api_version='2022-11-28', endpoint='create_issue') -- the wrong")
    print("direction. Only the tool's error message differs. Single-shot at temperature=0 showed")
    print("no difference (the user's own question names both versions, making 'try the other one'")
    print("trivially guessable regardless of message quality) -- so this runs N trials at")
    print("temperature=0.7 to check for a difference in RELIABILITY, not a single anecdote.\n")

    N = 8
    results = {"before": [], "after": []}
    for label, tool_result in [("before", BEFORE_TOOL_RESULT), ("after", AFTER_TOOL_RESULT)]:
        for i in range(N):
            r = run_one(tool_result)
            results[label].append(r)
            print(f"[{label} {i+1}/{N}] {r}")

    def score(rows):
        good = sum(1 for r in rows if (r["outcome"] == "retried_tool" and r["retried_correctly"])
                   or (r["outcome"] == "answered" and r["confident"]))
        return good, len(rows)

    before_good, before_n = score(results["before"])
    after_good, after_n = score(results["after"])
    print(f"\nBEFORE: correct recovery {before_good}/{before_n}")
    print(f"AFTER:  correct recovery {after_good}/{after_n}")

    Path(HERE / "error_before_after.md").write_text(
        "# Error Before/After Transcript\n\n"
        f"**Question:** {QUESTION}\n\n"
        "Both runs share an identical conversation up to and including the model having already "
        "(mistakenly) called `check_deprecation(api_version=\"2022-11-28\", endpoint=\"create_issue\")` "
        "-- the wrong direction for this comparison. Only the tool result's error message differs.\n\n"
        "**Single-shot at temperature=0 showed no difference** -- documented honestly below, not hidden "
        "-- because the user's own question names both versions explicitly, so the model can recover by "
        "re-reading the question alone, regardless of the tool message's quality. This test instead runs "
        f"{N} trials per condition at temperature=0.7 to check for a difference in *reliability* of "
        "recovery, which a single deterministic trial can't reveal.\n\n"
        "## BEFORE -- original Week 7/8 message (still in `agent_race/tools.py`)\n\n"
        f"Tool result given to the model:\n```json\n{json.dumps(BEFORE_TOOL_RESULT, indent=2)}\n```\n\n"
        f"**Correct recovery: {before_good}/{before_n} trials**\n\n"
        "Per-trial outcomes:\n```\n" + "\n".join(str(r) for r in results["before"]) + "\n```\n\n"
        "## AFTER -- this week's rewrite (`docs_search_server.py`)\n\n"
        f"Tool result given to the model:\n```json\n{json.dumps(AFTER_TOOL_RESULT, indent=2)}\n```\n\n"
        f"**Correct recovery: {after_good}/{after_n} trials**\n\n"
        "Per-trial outcomes:\n```\n" + "\n".join(str(r) for r in results["after"]) + "\n```\n\n"
        "## Honest conclusion\n\n"
        f"{'The richer message shows a measurable reliability improvement.' if after_good > before_good else 'No measurable difference was found in this test -- see the writeup for why, and what would be needed to detect one.'}\n",
        encoding="utf-8",
    )
    print("\nSaved -> error_before_after.md")


if __name__ == "__main__":
    main()
