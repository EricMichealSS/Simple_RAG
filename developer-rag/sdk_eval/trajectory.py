"""Week 8 trajectory evaluation, adapted to the integrated engine.

THE SPECS BELOW WERE WRITTEN BEFORE THE FIRST RUN (the Week 8 discipline: define a correct path first so classification cannot be bent to
whatever happened). They cover the same 10 race questions as Week 7/8, rewritten for the integrated tool vocabulary:

    Week 8 agent                         Integrated engine
    get_openapi_spec(version, endpoint)  search_docs(version)      version-scoped retrieval; one call per version in scope
    check_deprecation(version, endpoint) lookup_changes(version)   the changelog / evidence-backed deprecations
    (none)                               check_symbol(version, s)  only when an answer contains code with uncited identifiers

WHAT THIS MEASURES, honestly: tool selection in this engine is RULE-BASED (the graph decides; a model never picks a tool), so a high tool-choice
accuracy here means the rules do the right thing for these questions. It is not evidence about an LLM's tool judgement, which is what Week 8 measured
on its ReAct agent. What it can still show: unnecessary calls, missing calls, wrong version arguments, failed calls, and the gap between a correct
answer and a correct path.

A spec states, per question: the version scope a correct run uses, the minimum number of calls per tool ("required"), and the most calls per tool
that are justified ("allowed_max"; anything beyond is unnecessary, any tool not listed is a wrong tool), and whether the right outcome is a refusal.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

V22, V25, S3 = "2022-11-28", "2025-06-01", "3.x"


@dataclass(frozen=True)
class TrajectorySpec:
    versions: Tuple[str, ...]                  # the version scope a correct run uses (two = an explicit comparison)
    required: Dict[str, int]                   # minimum calls per tool
    allowed_max: Dict[str, int]                # justified maximum calls per tool (a tool not listed here is a wrong tool)
    negative: bool = False                     # the correct outcome is a refusal
    why: str = ""


SPECS: Dict[str, TrajectorySpec] = {
    "easy_01": TrajectorySpec((V25,), {"search_docs": 1}, {"search_docs": 1, "lookup_changes": 1},
                              why="single-hop lookup, version stated; an endpoint (render_markdown) is detected, so a proactive changelog check is justified"),
    "easy_02": TrajectorySpec((S3,), {"search_docs": 1}, {"search_docs": 1},
                              why="single-hop lookup in the Swagger corpus; no endpoint, nothing changed to look up: lookup_changes would be unnecessary"),
    "easy_13": TrajectorySpec((V25,), {"search_docs": 1}, {"search_docs": 1}, negative=True,
                              why="GraphQL rate limit is not in the corpus: search once, then refuse"),
    "medium_01": TrajectorySpec((V25,), {"search_docs": 1, "lookup_changes": 1}, {"search_docs": 1, "lookup_changes": 1},
                                why="a deprecation question: the changelog evidence is essential, not optional"),
    "medium_07": TrajectorySpec((V25,), {"search_docs": 1}, {"search_docs": 1, "lookup_changes": 1},
                                why="new enum values for create_repository; the changelog check is justified but not required"),
    "medium_13": TrajectorySpec((S3,), {"search_docs": 1}, {"search_docs": 1}, negative=True,
                                why="a fabricated formatter flag: search once, then refuse"),
    "hard_01": TrajectorySpec((V22, V25), {"search_docs": 2, "lookup_changes": 1}, {"search_docs": 2, "lookup_changes": 1},
                              why="an explicit comparison: one strictly-scoped search per version, plus the changelog"),
    "hard_04": TrajectorySpec((V22, V25), {"search_docs": 2, "lookup_changes": 1}, {"search_docs": 2, "lookup_changes": 1}, why="comparison, as hard_01"),
    "hard_06": TrajectorySpec((V22, V25), {"search_docs": 2, "lookup_changes": 1}, {"search_docs": 2, "lookup_changes": 1}, why="comparison, as hard_01"),
    "hard_11": TrajectorySpec((V22, V25), {"search_docs": 2, "lookup_changes": 1}, {"search_docs": 2, "lookup_changes": 1}, why="comparison, as hard_01"),
}


@dataclass
class Call:
    tool: str
    ok: bool
    version: Optional[str] = None              # the version the SERVER saw (from its span), None if the call never reached it
    error_category: Optional[str] = None


def extract_calls(spans: Sequence[Dict[str, Any]]) -> List[Call]:
    """The tool calls of one request, in order, from its spans (client `mcp.call` joined to the server span that hangs under it)."""
    server_version = {s["parent_span_id"]: s["attributes"].get("requested_version") for s in spans
                      if s.get("service") == "mcp_server" and s.get("name", "").startswith("mcp.tool.")}
    calls = [s for s in spans if s.get("name") == "mcp.call"]
    calls.sort(key=lambda s: s.get("start_ts") or "")
    return [Call(tool=str(s["attributes"].get("tool")), ok=bool(s["attributes"].get("ok")), version=server_version.get(s["span_id"]),
                 error_category=s["attributes"].get("error_category")) for s in calls]


def score(spec: TrajectorySpec, calls: Sequence[Call], *, outcome: str, outcome_pass: Optional[bool]) -> Dict[str, Any]:
    """Score one path against its spec. `outcome_pass` is the answer-level verdict (judge against the golden answer) or None if ungraded."""
    n = Counter(c.tool for c in calls)
    wrong_tool = [c.tool for c in calls if c.tool not in spec.allowed_max]
    unnecessary = sum(max(0, n[t] - spec.allowed_max.get(t, 0)) for t in n)
    missing = {t: need - n[t] for t, need in spec.required.items() if n[t] < need}
    failed = sum(not c.ok for c in calls)
    searched = Counter(c.version for c in calls if c.tool == "search_docs" and c.ok)
    version_ok = (all(c.version in spec.versions for c in calls if c.ok and c.version is not None)
                  and all(searched[v] >= 1 for v in spec.versions if "search_docs" in spec.required))
    expected_outcome = "abstain" if spec.negative else "answered"
    path_ok = not missing and not unnecessary and not wrong_tool and failed == 0 and version_ok
    return {"tool_sequence": [c.tool for c in calls], "versions_seen": [c.version for c in calls], "calls": len(calls),
            "valid_tool_calls": sum(c.tool in spec.allowed_max for c in calls), "wrong_tool_calls": wrong_tool, "unnecessary_calls": unnecessary,
            "missing_required": missing, "failed_calls": failed, "version_args_ok": version_ok, "path_ok": path_ok,
            "outcome": outcome, "expected_outcome": expected_outcome, "outcome_as_expected": outcome == expected_outcome,
            "outcome_pass": outcome_pass,
            "gap": None if outcome_pass is None else ("correct_answer_unreliable_path" if outcome_pass and not path_ok
                                                      else "right_path_wrong_answer" if path_ok and not outcome_pass else "agree")}


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate over scored rows (each has `score`). Every figure carries its denominator."""
    s = [r["score"] for r in rows]
    n = len(s)
    calls = sum(x["calls"] for x in s)
    graded = [x for x in s if x["outcome_pass"] is not None]
    gap = Counter(x["gap"] for x in graded)
    table = {"outcome_pass&path_ok": sum(x["outcome_pass"] and x["path_ok"] for x in graded),
             "outcome_pass&path_not_ok": sum(x["outcome_pass"] and not x["path_ok"] for x in graded),
             "outcome_fail&path_ok": sum((not x["outcome_pass"]) and x["path_ok"] for x in graded),
             "outcome_fail&path_not_ok": sum((not x["outcome_pass"]) and not x["path_ok"] for x in graded)}
    return {
        "questions": n, "tool_calls": calls,
        "tool_choice_accuracy": {"valid": sum(x["valid_tool_calls"] for x in s), "of": calls, "rate": round(sum(x["valid_tool_calls"] for x in s) / calls, 3) if calls else None},
        "path_ok": {"count": sum(x["path_ok"] for x in s), "of": n}, "outcome_as_expected": {"count": sum(x["outcome_as_expected"] for x in s), "of": n},
        "outcome_pass_judge": {"count": sum(x["outcome_pass"] for x in graded), "of": len(graded)},
        "unnecessary_calls": sum(x["unnecessary_calls"] for x in s), "failed_calls": sum(x["failed_calls"] for x in s),
        "wrong_tool_calls": sum(len(x["wrong_tool_calls"]) for x in s), "questions_missing_a_required_call": sum(bool(x["missing_required"]) for x in s),
        "version_args_ok": {"count": sum(x["version_args_ok"] for x in s), "of": n},
        "outcome_vs_path": table, "gap": dict(gap),
    }
