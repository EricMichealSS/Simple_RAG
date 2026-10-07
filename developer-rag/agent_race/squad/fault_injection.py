"""Failure test (protocol.md): make the version/deprecation worker return an
HTTP 500 on hard_04 and record what the unchanged manager does.

    persistent : every version_worker call returns 500
    transient  : only the first version_worker call returns 500

The manager has no failure-handling code or prompt. Results go to
failure_runs.json; the retry/degrade/lie call is made by reading the answer
against the definitions in protocol.md (failure_case.md).
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import workers  # noqa: E402
from grader import grade  # noqa: E402
from orchestrator import run_squad  # noqa: E402
from questions import get_race_questions  # noqa: E402

CASE = "hard_04"


def main():
    q = next(x for x in get_race_questions() if x["race_id"] == CASE)
    results = []
    for mode in ("persistent", "transient"):
        workers.set_fault("version_worker", mode)
        r = run_squad(q["question"], race_id=CASE, repeat=0, sink=HERE / "traces_failure.jsonl")
        workers.set_fault(None, None)
        g = grade(q["question"], q["golden_answer"], r["answer"])
        vcalls = [e for e in r["log"] if e["worker"] == "version_worker"]
        results.append({
            "mode": mode, "status": r["status"], "answer": r["answer"], "verdict_vs_golden": g["verdict"],
            "version_worker_calls": len(vcalls),
            "version_worker_statuses": [e["status"] for e in vcalls],
            "other_delegations": [(e["worker"], e["status"]) for e in r["log"] if e["worker"] != "version_worker"],
            "retried_after_500": len(vcalls) > 1 and vcalls[0]["status"] >= 500,
            "total_tokens": r["total_tokens"], "wall_clock_s": r["wall_clock_s"],
            "delegation_log": r["log"],
        })
        print(json.dumps({k: v for k, v in results[-1].items() if k != "delegation_log"}, indent=2))
    (HERE / "failure_runs.json").write_text(json.dumps({"case": CASE, "question": q["question"],
                                                        "golden": q["golden_answer"], "runs": results}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
