"""Generate and freeze one fixed answer set for all 26 eval cases.

This is run ONCE to produce eval/answers_25.json — the fixed snapshot used for
blind hand-labeling (Requirement 3) and for validating judge_v1 (Requirement 4).
Freezing the answers matters: if answers were regenerated on every run, the
hand labels and the judge would be scoring two different sets of text and
"agreement" would be meaningless. Run this again only to produce a fresh
snapshot for a full re-validation cycle.
"""

import json
import time
from pathlib import Path

from generate import generate_answer

CASES_PATH = Path(__file__).parent / "cases.json"
OUT_PATH = Path(__file__).parent / "answers_25.json"


def main():
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    frozen = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} ({case['mode']}): {case['question'][:70]}")
        result = generate_answer(case["question"])
        frozen.append({**case, **result})
        print(f"    -> {result['answer'][:100].replace(chr(10), ' ')}")
        time.sleep(2.5)  # stay well under Groq TPM limits (Week 5 Mode 1)

    OUT_PATH.write_text(json.dumps(frozen, indent=2), encoding="utf-8")
    print(f"\nFroze {len(frozen)} answers -> {OUT_PATH}")


if __name__ == "__main__":
    main()
