"""Run ANY single new question through both systems and print a side-by-side
comparison -- the same 4 numbers race.py reports, just for one question
instead of the fixed 10.

Usage:
    python3 agent_race/compare_one.py "your question here"
    python3 agent_race/compare_one.py "your question here" "the expected/golden answer, if you know it"

Without a golden answer, both systems' raw answers are printed so you can
eyeball them yourself. With one, both get graded PASS/FAIL the same way
race.py's questions do, and a verdict line is printed at the end.
"""

import sys
import time

from agent import run_agent
from workflow import run_workflow


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 agent_race/compare_one.py "question" ["golden answer (optional)"]')
        sys.exit(1)

    question = sys.argv[1]
    golden_answer = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"QUESTION: {question}\n")

    print("Running agent...")
    t0 = time.perf_counter()
    agent_result = run_agent(question)
    print(f"  done in {time.perf_counter() - t0:.1f}s (wall_clock_s recorded: {agent_result['wall_clock_s']})\n")

    print("Running workflow...")
    t0 = time.perf_counter()
    workflow_result = run_workflow(question)
    print(f"  done in {time.perf_counter() - t0:.1f}s (wall_clock_s recorded: {workflow_result['wall_clock_s']})\n")

    agent_verdict = workflow_verdict = None
    if golden_answer:
        from grader import grade
        agent_verdict = grade(question, golden_answer, agent_result["answer"])["verdict"]
        workflow_verdict = grade(question, golden_answer, workflow_result["answer"])["verdict"]

    print("=" * 78)
    print(f"{'':<22}{'agent':>26}{'workflow':>26}")
    print("-" * 78)
    if golden_answer:
        print(f"{'verdict':<22}{agent_verdict:>26}{workflow_verdict:>26}")
    print(f"{'status':<22}{agent_result['status']:>26}{workflow_result['status']:>26}")
    print(f"{'iterations':<22}{agent_result['iterations']:>26}{workflow_result['iterations']:>26}")
    print(f"{'total_tokens':<22}{agent_result['total_tokens']:>26}{workflow_result['total_tokens']:>26}")
    print(f"{'cost_usd':<22}{agent_result['total_cost_usd']:>26}{workflow_result['total_cost_usd']:>26}")
    print(f"{'wall_clock_s':<22}{agent_result['wall_clock_s']:>26}{workflow_result['wall_clock_s']:>26}")
    print("=" * 78)

    print(f"\n--- AGENT ANSWER ---\n{agent_result['answer']}")
    print(f"\n--- WORKFLOW ANSWER ---\n{workflow_result['answer']}")

    print("\n--- WINNER ---")
    if golden_answer:
        if agent_verdict == "PASS" and workflow_verdict == "FAIL":
            print("AGENT wins: only the agent got it right.")
        elif workflow_verdict == "PASS" and agent_verdict == "FAIL":
            print("WORKFLOW wins: only the workflow got it right.")
        elif agent_verdict == "FAIL" and workflow_verdict == "FAIL":
            print("Neither passed -- check both answers above manually.")
        else:
            # both passed -- fall back to latency/tokens/cost like the race does
            faster = "agent" if agent_result["wall_clock_s"] < workflow_result["wall_clock_s"] else "workflow"
            cheaper = "agent" if agent_result["total_cost_usd"] < workflow_result["total_cost_usd"] else "workflow"
            print(f"Both passed. Faster: {faster}. Cheaper: {cheaper}.")
    else:
        print("No golden answer given -- read both answers above and judge correctness yourself, "
              "then compare the numbers in the table (lower latency/tokens/cost wins, all else equal).")


if __name__ == "__main__":
    main()
