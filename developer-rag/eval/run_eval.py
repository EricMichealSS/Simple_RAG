"""THE single eval command (Requirement 1 / submission checklist item 4).

Runs every case in cases.json through the live pipeline (fresh retrieval +
generation, exactly as a user would trigger from app.py), applies the
deterministic assertions, grades with the validated judge, and prints one
pass-rate-by-mode table.

    python3 eval/run_eval.py

judge_v1.txt (with the context-truncation bug fixed) is used here, not
judge_v2.txt -- see report.md: the v2 few-shot iteration fixed its two
targeted disagreements but introduced a net regression (80.8% -> 76.9%
agreement with the human labels), so v1 remains the validated judge in
production until a v3 iteration addresses that regression.
"""

import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from generate import generate_answer  # noqa: E402
from assertions import run_assertions  # noqa: E402
from judge import build_context, judge_one  # noqa: E402

import json  # noqa: E402

EVAL_DIR = Path(__file__).parent
CASES = json.loads((EVAL_DIR / "cases.json").read_text(encoding="utf-8"))
JUDGE_TEMPLATE = (EVAL_DIR / "judge_v1.txt").read_text(encoding="utf-8")


def main():
    rows = []
    for i, case in enumerate(CASES, 1):
        print(f"[{i}/{len(CASES)}] {case['id']} ({case['mode']})...", flush=True)
        gen = generate_answer(case["question"])
        assertion_results = run_assertions(case["question"], gen["answer"], gen["retrieved_chunks"])
        # distance_not_placeholder is a pipeline-health regression guard for the
        # still-unfixed Week 5 Mode 5 bug, not a per-answer quality check — it
        # currently fires on every case by design and must not zero out the
        # content pass rate. Report it separately (see the guard line below).
        content_results = [r for r in assertion_results if r["name"] != "distance_not_placeholder"]
        guard_result = next(r for r in assertion_results if r["name"] == "distance_not_placeholder")
        assertions_pass = all(r["passed"] for r in content_results)

        context = build_context(gen["retrieved_chunks"])
        verdict = judge_one(JUDGE_TEMPLATE, case["question"], context, gen["answer"])
        judge_pass = verdict["verdict"] == "PASS"

        overall = assertions_pass and judge_pass
        rows.append({
            "id": case["id"], "mode": case["mode"], "regression": case.get("regression", False),
            "assertions_pass": assertions_pass, "judge_pass": judge_pass, "overall_pass": overall,
            "failed_assertions": [r["name"] for r in content_results if not r["passed"]],
            "distance_guard_clean": guard_result["passed"],
        })
        time.sleep(1.5)

    # ---- pass rate by mode ----
    by_mode = defaultdict(list)
    for r in rows:
        by_mode[r["mode"]].append(r["overall_pass"])

    print("\n" + "=" * 60)
    print("PASS RATE BY MODE")
    print("=" * 60)
    print(f"{'mode':<38}{'pass':>8}{'total':>8}{'rate':>8}")
    total_pass = total_n = 0
    for mode, results in sorted(by_mode.items()):
        n_pass, n = sum(results), len(results)
        total_pass += n_pass
        total_n += n
        print(f"{mode:<38}{n_pass:>8}{n:>8}{n_pass/n:>7.0%}")
    print("-" * 60)
    print(f"{'OVERALL':<38}{total_pass:>8}{total_n:>8}{total_pass/total_n:>7.0%}")

    guard_clean = sum(1 for r in rows if r["distance_guard_clean"])
    print(f"\nRegression guard (Week 5 Mode 5, distance-metadata placeholder): "
          f"{guard_clean}/{len(rows)} clean "
          f"{'(still broken on every case — not yet fixed)' if guard_clean == 0 else ''}")

    out_path = EVAL_DIR / "run_eval_latest.json"
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nFull results -> {out_path}")


if __name__ == "__main__":
    main()
