"""Follow-up experiment (NOT part of the graded Week 8 deliverable, which
applied exactly one mitigation and stops there). This tests whether the two
efficiency changes discussed afterward -- encouraging parallel tool calls,
and not resending the full tool-result history every lap -- let the agent
close the gap with the workflow, without touching anything already reported
in trajectory_results_baseline.json / trajectory_results_after.json /
race_summary.json.

Runs the agent with mitigated=True (keeps the Week 8 correctness fix) PLUS
parallel_hint=True and compact_history=True (the new efficiency changes),
over the same 10 questions, and reports both the trajectory numbers (like
trajectory_eval.py) and the race-style 4 numbers (like race.py), so it's
directly comparable to every previous result.

    python3 agent_race/agent_optimized_experiment.py
"""

import json
import statistics
import time
from pathlib import Path

from agent import run_agent
from grader import grade
from questions import get_race_questions
from trajectory_specs import TRAJECTORY_SPECS, VALID_PAIRS
from failure_modes import classify, ALL_MODES
from trajectory_eval import _extract_trajectory

RACE_DIR = Path(__file__).parent


def evaluate_one_optimized(question_row: dict) -> dict:
    race_id = question_row["race_id"]
    spec = TRAJECTORY_SPECS[race_id]

    result = run_agent(question_row["question"], mitigated=True, parallel_hint=True, compact_history=True)
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
        "race_id": race_id, "level": question_row["level"],
        "status": result["status"], "budget_hit": result.get("budget_hit"),
        "answer": result["answer"], "outcome_pass": outcome_pass,
        "tool_sequence": tool_sequence, "steps_taken": steps_taken,
        "step_efficiency": round(step_efficiency, 3),
        "tool_choice_valid_steps": tool_choice_valid_steps, "total_steps": steps_taken,
        "arg_valid_steps": arg_valid_steps, "total_arg_steps": len(tool_pairs),
        "trajectory_pass": trajectory_pass, "failure_mode": failure_mode,
        "total_tokens": result["total_tokens"], "total_cost_usd": result["total_cost_usd"],
        "wall_clock_s": result["wall_clock_s"], "log": result["log"],
    }


def main():
    questions = get_race_questions()
    rows = []
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['race_id']}...", flush=True)
        rows.append(evaluate_one_optimized(q))
        time.sleep(1.0)

    n = len(rows)
    total_tool_steps = sum(r["total_steps"] for r in rows)
    total_valid_tool_steps = sum(r["tool_choice_valid_steps"] for r in rows)
    total_arg_steps = sum(r["total_arg_steps"] for r in rows)
    total_valid_arg_steps = sum(r["arg_valid_steps"] for r in rows)
    costs = sorted(r["total_cost_usd"] for r in rows)
    latencies = sorted(r["wall_clock_s"] for r in rows)

    mode_counts = {m: 0 for m in ALL_MODES}
    for r in rows:
        mode_counts[r["failure_mode"]] += 1

    summary = {
        "outcome_pass_rate": round(sum(1 for r in rows if r["outcome_pass"]) / n, 3),
        "trajectory_pass_rate": round(sum(1 for r in rows if r["trajectory_pass"]) / n, 3),
        "tool_choice_accuracy": round(total_valid_tool_steps / total_tool_steps, 3) if total_tool_steps else None,
        "argument_validity_rate": round(total_valid_arg_steps / total_arg_steps, 3) if total_arg_steps else None,
        "step_efficiency_mean": round(statistics.mean(r["step_efficiency"] for r in rows), 3),
        "cost_p50_usd": round(statistics.median(costs), 6),
        "cost_max_usd": round(max(costs), 6),
        "cost_per_question_usd": round(sum(costs) / n, 6),
        "p50_latency_s": round(statistics.median(latencies), 3),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "mode_counts": mode_counts,
    }

    print("\n" + "=" * 72)
    print("OPTIMIZED AGENT (mitigated + parallel_hint + compact_history)")
    print("=" * 72)
    for k, v in summary.items():
        if k != "mode_counts":
            print(f"{k:<28}{v}")
    print("\nfailure mode counts:")
    for mode, count in summary["mode_counts"].items():
        print(f"  {mode:<28}{count}")

    # Load prior results for a direct side-by-side, if present. The two
    # trajectory_results_*.json files don't carry latency/total_tokens in
    # their "summary" block, so derive those from the raw per-question rows.
    comparisons = {}
    for tag, fname in [("agent_baseline", "trajectory_results_baseline.json"),
                       ("agent_mitigated", "trajectory_results_after.json")]:
        path = RACE_DIR / fname
        if path.exists():
            data = json.loads(path.read_text())
            s = dict(data["summary"])
            s["p50_latency_s"] = round(statistics.median(r["wall_clock_s"] for r in data["rows"]), 3)
            s["total_tokens"] = sum(r["total_tokens"] for r in data["rows"])
            comparisons[tag] = s
    workflow_path = RACE_DIR / "race_summary.json"
    if workflow_path.exists():
        comparisons["workflow (Week 7 race)"] = json.loads(workflow_path.read_text())["workflow"]

    if comparisons:
        print("\n" + "=" * 72)
        print("SIDE BY SIDE")
        print("=" * 72)
        print(f"{'':<26}{'outcome_pass':>14}{'p50_lat_s':>12}{'total_tok':>12}{'cost/q':>12}")
        print(f"{'agent_optimized':<26}{summary['outcome_pass_rate']:>14}{summary['p50_latency_s']:>12}"
              f"{summary['total_tokens']:>12}{summary['cost_per_question_usd']:>12}")
        for tag, s in comparisons.items():
            # trajectory_results_*.json uses "outcome_pass_rate" / no p50 latency or cost/question;
            # race_summary.json (Week 7) uses "pass_rate" / has both directly.
            outcome = s.get("outcome_pass_rate", s.get("pass_rate", "n/a"))
            lat = s.get("p50_latency_s", "n/a")
            tok = s.get("total_tokens", "n/a")
            cost = s.get("cost_per_question_usd", s.get("cost_p50_usd", "n/a"))
            print(f"{tag:<26}{outcome!s:>14}{lat!s:>12}{tok!s:>12}{cost!s:>12}")

    out_path = RACE_DIR / "trajectory_results_optimized.json"
    out_path.write_text(json.dumps({"rows": rows, "summary": summary}, indent=2), encoding="utf-8")
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
