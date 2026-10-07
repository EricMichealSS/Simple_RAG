"""LIVE demo of Week 10 (real model calls, a few thousand tokens). Shows the manager
delegating to a worker, every hand-off with its token count, and then the injected
HTTP 500 on the version worker.

    cd developer-rag && source .venv/bin/activate
    python3 agent_race/squad/demo_live.py                       # clean run, then persistent 500
    python3 agent_race/squad/demo_live.py --question "..."      # your own question
    python3 agent_race/squad/demo_live.py --no-fault            # skip the 500 part

Uses gpt-oss-20b (same as the race) unless RACE_MODEL is set.
"""

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("RACE_MODEL", "openai/gpt-oss-20b")  # must be set before pricing is imported

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import workers  # noqa: E402
from agent import run_agent  # noqa: E402
from orchestrator import run_squad  # noqa: E402

DEFAULT_Q = ("How did setting maintainer_can_modify during pull request creation change between "
             "GitHub API 2022-11-28 and 2025-06-01?")


def show_squad(title, r):
    print(f"\n--- {title}")
    for e in r["log"]:
        print(f"  manager -> {e['worker']}  args={e['args']}  => HTTP-like status {e['status']}")
    print(f"  {'#':>2} {'who':<15}{'hand-off':<52}{'prompt':>7}{'out':>6}")
    for c in r["tracer"].calls:
        print(f"  {c['seq']:>2} {c['caller']:<15}{c['handoff'][:51]:<52}{c['prompt_tokens']:>7}{c['completion_tokens']:>6}")
    print(f"  status={r['status']}  total_tokens={r['total_tokens']:,}  cost=${r['total_cost_usd']:.5f}  delegations={r['delegations']}")
    print("  ANSWER:", (r["answer"] or "(none)").replace("\n", " ")[:400])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default=DEFAULT_Q)
    ap.add_argument("--no-fault", action="store_true")
    a = ap.parse_args()
    print(f"MODEL for every role: {os.environ['RACE_MODEL']}\nQUESTION: {a.question}")

    print("\n=== ARM A: SINGLE AGENT (one loop, 3 tools) ===")
    s = run_agent(a.question)
    for e in s["log"]:
        if e["event"] == "tool_call":
            print(f"  tool call: {e['tool']} {e['args']}")
    print(f"  status={s['status']} laps={s['iterations']} total_tokens={s['total_tokens']:,} cost=${s['total_cost_usd']:.5f}")
    print("  ANSWER:", (s["answer"] or "(none)").replace("\n", " ")[:400])

    print("\n=== ARM B: SQUAD (manager + 2 workers), healthy ===")
    show_squad("healthy squad run", run_squad(a.question, race_id="demo"))

    if not a.no_fault:
        print("\n=== FAILURE TEST: version worker returns HTTP 500 on EVERY call (manager has no failure-handling code) ===")
        workers.set_fault("version_worker", "persistent")
        show_squad("persistent 500", run_squad(a.question, race_id="demo_fault"))
        workers.set_fault(None, None)
        print("\nCheck: did it say the version check failed? If not, it presented unverified version facts = 'lied'.")


if __name__ == "__main__":
    main()
