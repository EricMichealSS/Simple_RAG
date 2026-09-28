"""Week 8: trajectory evaluation. Runs the SAME 10 questions from Week 7's
race through the SAME agent (agent.py, unmodified for the baseline run),
but this time scores the PATH it took, not just the final answer.

    python3 agent_race/trajectory_eval.py --tag baseline           # before
    python3 agent_race/trajectory_eval.py --tag after --mitigated  # after the one mitigation
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from agent import run_agent
from grader import grade
from questions import get_race_questions
from trajectory_specs import TRAJECTORY_SPECS, VALID_PAIRS
from failure_modes import classify, ALL_MODES

RACE_DIR = Path(__file__).parent


def _extract_trajectory(log: list):
    """Pull the tool-call sequence and (tool, version, endpoint) triples out
    of an agent run's raw step log."""
    tool_sequence = []
    tool_pairs = []
    for entry in log:
        if entry.get("event") != "tool_call":
            continue
        tool_sequence.append(entry["tool"])
        try:
            args = json.loads(entry["args"])
        except (json.JSONDecodeError, TypeError):
            args = {}
        if "api_version" in args and "endpoint" in args:
            tool_pairs.append((entry["tool"], args["api_version"], args["endpoint"]))
    return tool_sequence, tool_pairs


def evaluate_one(question_row: dict, mitigated: bool = False) -> dict:
    race_id = question_row["race_id"]
    spec = TRAJECTORY_SPECS[race_id]

    result = run_agent(question_row["question"], mitigated=mitigated)
    tool_sequence, tool_pairs = _extract_trajectory(result["log"])

    steps_taken = len(tool_sequence)
    step_efficiency = steps_taken / spec["min_steps"] if spec["min_steps"] else (1.0 if steps_taken == 0 else float("inf"))

    tool_choice_valid_steps = sum(1 for t in tool_sequence if t in spec["allowed_tools"])
    arg_valid_steps = sum(1 for (_t, v, e) in tool_pairs if (v, e) in VALID_PAIRS)

    required_satisfied = spec["required_tools"].issubset(set(tool_sequence))
    args_all_valid = all((v, e) in VALID_PAIRS for (_t, v, e) in tool_pairs)
    tools_all_allowed = all(t in spec["allowed_tools"] for t in tool_sequence)
    trajectory_pass = required_satisfied and args_all_valid and tools_all_allowed

    outcome_verdict = grade(question_row["question"], question_row["golden_answer"], result["answer"])["verdict"]
    outcome_pass = outcome_verdict == "PASS"

    failure_mode = classify(spec, result["status"], tool_sequence, tool_pairs)

    return {
        "race_id": race_id, "level": question_row["level"], "is_negative_case": question_row["is_negative_case"],
        "status": result["status"], "budget_hit": result.get("budget_hit"),
        "answer": result["answer"], "outcome_pass": outcome_pass, "outcome_verdict": outcome_verdict,
        "tool_sequence": tool_sequence, "tool_pairs": tool_pairs,
        "steps_taken": steps_taken, "min_steps": spec["min_steps"], "step_efficiency": round(step_efficiency, 3),
        "tool_choice_valid_steps": tool_choice_valid_steps, "total_steps": steps_taken,
        "arg_valid_steps": arg_valid_steps, "total_arg_steps": len(tool_pairs),
        "trajectory_pass": trajectory_pass, "failure_mode": failure_mode,
        "total_tokens": result["total_tokens"], "total_cost_usd": result["total_cost_usd"],
        "wall_clock_s": result["wall_clock_s"],
        "log": result["log"],
    }


def run_all(mitigated: bool = False) -> list:
    questions = get_race_questions()
    rows = []
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['race_id']}...", flush=True)
        rows.append(evaluate_one(q, mitigated=mitigated))
        time.sleep(1.0)
    return rows


def summarize(rows: list) -> dict:
    n = len(rows)
    total_tool_steps = sum(r["total_steps"] for r in rows)
    total_valid_tool_steps = sum(r["tool_choice_valid_steps"] for r in rows)
    total_arg_steps = sum(r["total_arg_steps"] for r in rows)
    total_valid_arg_steps = sum(r["arg_valid_steps"] for r in rows)
    costs = sorted(r["total_cost_usd"] for r in rows)

    mode_counts = {m: 0 for m in ALL_MODES}
    for r in rows:
        mode_counts[r["failure_mode"]] += 1

    return {
        "n": n,
        "outcome_pass_rate": round(sum(1 for r in rows if r["outcome_pass"]) / n, 3),
        "trajectory_pass_rate": round(sum(1 for r in rows if r["trajectory_pass"]) / n, 3),
        "tool_choice_accuracy": round(total_valid_tool_steps / total_tool_steps, 3) if total_tool_steps else None,
        "argument_validity_rate": round(total_valid_arg_steps / total_arg_steps, 3) if total_arg_steps else None,
        "step_efficiency_mean": round(statistics.mean(r["step_efficiency"] for r in rows if r["step_efficiency"] != float("inf")), 3),
        "cost_p50_usd": round(statistics.median(costs), 6),
        "cost_max_usd": round(max(costs), 6),
        "mode_counts": mode_counts,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="baseline", help="label for output files, e.g. 'baseline' or 'after'")
    ap.add_argument("--mitigated", action="store_true", help="apply the SYSTEM_PROMPT_MITIGATED fix for incomplete_grounding")
    args = ap.parse_args()

    rows = run_all(mitigated=args.mitigated)
    summary = summarize(rows)

    print("\n" + "=" * 72)
    print(f"TRAJECTORY EVAL -- tag={args.tag}" + (" (MITIGATED)" if args.mitigated else " (baseline)"))
    print("=" * 72)
    print(f"outcome_pass_rate:       {summary['outcome_pass_rate']}")
    print(f"trajectory_pass_rate:    {summary['trajectory_pass_rate']}")
    print(f"GAP (outcome-trajectory):{round(summary['outcome_pass_rate'] - summary['trajectory_pass_rate'], 3)}")
    print(f"tool_choice_accuracy:    {summary['tool_choice_accuracy']}")
    print(f"argument_validity_rate:  {summary['argument_validity_rate']}")
    print(f"step_efficiency_mean:    {summary['step_efficiency_mean']}")
    print(f"cost p50 / max (usd):    {summary['cost_p50_usd']} / {summary['cost_max_usd']}")
    print("\nfailure mode counts:")
    for mode, count in summary["mode_counts"].items():
        print(f"  {mode:<28}{count}")

    print("\nright-answer-wrong-path candidates (outcome PASS, trajectory FAIL):")
    for r in rows:
        if r["outcome_pass"] and not r["trajectory_pass"]:
            print(f"  {r['race_id']}: mode={r['failure_mode']} tool_sequence={r['tool_sequence']}")

    out_path = RACE_DIR / f"trajectory_results_{args.tag}.json"
    out_path.write_text(json.dumps({"rows": rows, "summary": summary}, indent=2), encoding="utf-8")
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
