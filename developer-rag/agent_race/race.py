"""THE race: run both systems over the same 10 questions, grade both against
the golden answers, and report the 4 comparable numbers per system.

    python3 agent_race/race.py
"""

import csv
import json
import statistics
import time
from pathlib import Path

from agent import run_agent
from workflow import run_workflow
from grader import grade
from questions import get_race_questions

RACE_DIR = Path(__file__).parent


def run_system(name, run_fn, questions):
    rows = []
    for i, q in enumerate(questions, 1):
        print(f"  [{name}] [{i}/{len(questions)}] {q['race_id']}...", flush=True)
        result = run_fn(q["question"])
        verdict = grade(q["question"], q["golden_answer"], result.get("answer"))
        rows.append({
            "race_id": q["race_id"], "level": q["level"], "dependency_case": q["dependency_case"],
            "is_negative_case": q["is_negative_case"], "system": name,
            "status": result["status"], "budget_hit": result.get("budget_hit"),
            "answer": result.get("answer"), "verdict": verdict["verdict"], "grader_reason": verdict["raw"],
            "iterations": result["iterations"], "total_tokens": result["total_tokens"],
            "total_cost_usd": result["total_cost_usd"], "wall_clock_s": result["wall_clock_s"],
        })
        time.sleep(1.0)
    return rows


def summarize(rows):
    n = len(rows)
    passes = sum(1 for r in rows if r["verdict"] == "PASS")
    latencies = sorted(r["wall_clock_s"] for r in rows)
    p50 = statistics.median(latencies)
    total_tokens = sum(r["total_tokens"] for r in rows)
    total_cost = sum(r["total_cost_usd"] for r in rows)
    return {
        "pass_rate": round(passes / n, 3),
        "p50_latency_s": round(p50, 3),
        "total_tokens": total_tokens,
        "cost_per_question_usd": round(total_cost / n, 6),
    }


def main():
    questions = get_race_questions()
    print(f"Racing over {len(questions)} questions "
          f"({sum(1 for q in questions if q['dependency_case'])} dependency cases, "
          f"{sum(1 for q in questions if q['is_negative_case'])} negative cases)\n")

    print("Running AGENT...")
    agent_rows = run_system("agent", run_agent, questions)
    print("\nRunning WORKFLOW...")
    workflow_rows = run_system("workflow", run_workflow, questions)

    all_rows = agent_rows + workflow_rows
    (RACE_DIR / "race_results.json").write_text(json.dumps(all_rows, indent=2), encoding="utf-8")

    with open(RACE_DIR / "race.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    agent_summary = summarize(agent_rows)
    workflow_summary = summarize(workflow_rows)

    print("\n" + "=" * 70)
    print(f"{'metric':<28}{'agent':>18}{'workflow':>18}")
    print("-" * 70)
    for key, label in [
        ("pass_rate", "pass rate"), ("p50_latency_s", "p50 latency (s)"),
        ("total_tokens", "total tokens"), ("cost_per_question_usd", "cost/question ($)"),
    ]:
        print(f"{label:<28}{agent_summary[key]:>18}{workflow_summary[key]:>18}")

    summary_path = RACE_DIR / "race_summary.json"
    summary_path.write_text(json.dumps({"agent": agent_summary, "workflow": workflow_summary}, indent=2), encoding="utf-8")
    print(f"\nWrote race.csv, race_results.json, {summary_path.name}")


if __name__ == "__main__":
    main()
