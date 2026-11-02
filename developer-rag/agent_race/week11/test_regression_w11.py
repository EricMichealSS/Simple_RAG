"""Week 11 permanent regression test for the deprecated-method-in-code-sample
incident (race_id w11_reg_01, see ../questions.py get_regression_cases()).

Run directly (no pytest required, works with the stdlib alone once the
fakes below are in place):

    python week11/test_regression_w11.py

Exit code 0 = PASS (prints GREEN), exit code 1 = FAIL (prints RED).

This imports the REAL agent.py and REAL tools.py from agent_race/ --
nothing about the agent's actual control flow is reimplemented or mocked.
Only the three external I/O boundaries agent.py/tools.py reach out to
(the OpenAI-compatible HTTP client, the Chroma vector store, the embedding
model) are replaced with deterministic fakes, because none of
openai/chromadb/sentence-transformers are installed in this environment
(see week11/phase0.md for the full environment audit). See fakes.py's
module docstring for exactly which values are REAL (copied from actual
historical traces already in this repo) and which are SYNTHETIC.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_RACE_DIR = HERE.parent

# config.py raises if GROK_API_KEY is unset; this is a test-only placeholder,
# never used for a real network call because the OpenAI client itself is
# faked below -- no request ever leaves this process.
os.environ.setdefault("GROK_API_KEY", "test-only-placeholder-not-a-real-key")

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(AGENT_RACE_DIR))
sys.path.insert(0, str(AGENT_RACE_DIR.parent))  # developer-rag/ for config.py

import fakes  # noqa: E402
fakes.install_all_fakes()

import agent  # noqa: E402  -- the REAL agent.py, now importable thanks to the fakes
from questions import get_regression_cases  # noqa: E402
from deprecation_assertions import uses_forbidden_term_without_replacement  # noqa: E402


def run_case(case: dict) -> dict:
    """Calls run_agent EXACTLY the way every real caller does: no mitigated=
    kwarg. Whatever agent.py's CURRENT DEFAULT is, is what gets exercised --
    this is deliberate: it is the only way this test can catch "the fix
    exists in the code but nobody turned it on by default."
    """
    result = agent.run_agent(case["question"])
    check = uses_forbidden_term_without_replacement(
        result.get("answer"), case["forbidden_term"], case["required_replacement"]
    )
    return {
        "race_id": case["race_id"],
        "question": case["question"],
        "answer": result.get("answer"),
        "status": result.get("status"),
        "prompt_version": result.get("prompt_version"),
        "mitigated_effective": result.get("mitigated"),
        "total_tokens": result.get("total_tokens"),
        "total_cost_usd": result.get("total_cost_usd"),
        "passed": check["passed"],
        "reason": check["reason"],
    }


def main() -> int:
    cases = get_regression_cases()
    all_passed = True
    print(f"Week 11 regression suite -- {len(cases)} case(s)")
    print("=" * 78)
    for case in cases:
        r = run_case(case)
        verdict = "PASS" if r["passed"] else "FAIL"
        if not r["passed"]:
            all_passed = False
        print(f"[{r['race_id']}] {verdict}")
        print(f"  question           : {r['question']}")
        print(f"  prompt_version      : {r['prompt_version']}")
        print(f"  mitigated_effective : {r['mitigated_effective']}  (True means the Week 8 fix prompt was used)")
        print(f"  answer              : {r['answer']}")
        print(f"  reason              : {r['reason']}")
        print(f"  tokens={r['total_tokens']}  cost_usd={r['total_cost_usd']}")
        print("-" * 78)

    print(f"SUITE RESULT: {'GREEN' if all_passed else 'RED'} "
          f"({sum(1 for c in cases if True) - (0 if all_passed else 1)}/{len(cases)} shown above)")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
