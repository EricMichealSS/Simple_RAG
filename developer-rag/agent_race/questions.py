"""The 10 race questions, hand-picked from the 45-question golden set.

Mix: 3 Easy (single-hop lookups, incl. 1 refusal case), 3 Medium (single
version, but real deprecation content), 4 Hard (genuine cross-version
comparisons -- step 3 has to use what step 2 found, i.e. get the current
spec THEN check what changed relative to the other version named in the
question). 4 dependency cases clears the "at least 3" requirement.
"""

import json
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden_sets"


def _load(level_file):
    return {row["id"]: row for row in json.loads((GOLDEN_DIR / level_file).read_text(encoding="utf-8"))}


_easy = _load("gs_easy.json")
_medium = _load("gs_medium.json")
_hard = _load("gs_hard.json")

SELECTED = [
    ("easy", "01"),    # render markdown default mode -- single-hop
    ("easy", "02"),    # swagger v3 maven group id -- single-hop
    ("easy", "13"),    # negative case: GraphQL rate limit -- expects refusal
    ("medium", "01"),  # assignee -> assignees deprecation (single version, real dep. content)
    ("medium", "07"),  # new visibility enum values in 2025-06-01
    ("medium", "13"),  # negative case: fabricated Black-formatter CLI flag
    ("hard", "01"),    # DEPENDENCY: context param format, 2022-11-28 vs 2025-06-01
    ("hard", "04"),    # DEPENDENCY: maintainer_can_modify creation-time change
    ("hard", "06"),    # DEPENDENCY: make_latest replacing undocumented heuristic
    ("hard", "11"),    # DEPENDENCY: issue type field, 2022-11-28 vs 2025-06-01
]

_LEVEL_MAP = {"easy": _easy, "medium": _medium, "hard": _hard}


def get_race_questions():
    out = []
    for level, qid in SELECTED:
        row = _LEVEL_MAP[level][qid]
        out.append({
            "race_id": f"{level}_{qid}",
            "level": level,
            "question": row["question"],
            "golden_answer": row["answer"],
            "is_negative_case": bool(row.get("metadata", {}).get("is_negative_case", False)),
            "dependency_case": level == "hard",
        })
    return out


if __name__ == "__main__":
    for q in get_race_questions():
        print(f"[{q['race_id']}] dep={q['dependency_case']} neg={q['is_negative_case']} :: {q['question']}")
