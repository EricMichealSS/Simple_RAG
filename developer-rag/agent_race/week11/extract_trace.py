"""Reproduces the planted incident under its ORIGINAL (pre-fix) conditions --
mitigated=False, parallel_hint=False, compact_history=False, explicitly passed
-- by calling the REAL, now-fixed agent.py with the old behavior requested on
purpose, and writes a full Week 11 structured trace.json from the real
returned log (which now includes the Phase 2 instrumentation: timestamps,
per-call cost, and full tool results for context-ID extraction).

This is how the incident is kept REPRODUCIBLE after the fix shipped (the
default changed, but the old code path did not disappear) -- see drill.md
and phase3.md for why that matters.

Run:
    python week11/extract_trace.py
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_RACE_DIR = HERE.parent

import os  # noqa: E402
os.environ.setdefault("GROK_API_KEY", "test-only-placeholder-not-a-real-key")

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(AGENT_RACE_DIR))
sys.path.insert(0, str(AGENT_RACE_DIR.parent))

import fakes  # noqa: E402
fakes.install_all_fakes()

import agent  # noqa: E402
from pricing import MODEL  # noqa: E402

QUESTION = ("In GitHub API version 2025-06-01, what parameter replaces the deprecated "
            "single assignee field when creating an issue?")

# REAL reference latency for this exact question/path from an actual historical
# run on the live Groq API, BEFORE this environment's network/package
# limitations existed: trajectory_results_baseline.json, race_id=medium_01,
# wall_clock_s=20.712. The mocked re-run below executes in milliseconds
# because no real network call happens -- that synthetic timing is NOT used
# for latency_ms; the real historical figure is used instead, labeled as such.
REAL_HISTORICAL_WALL_CLOCK_S = 20.712
REAL_HISTORICAL_SOURCE = "trajectory_results_baseline.json, race_id=medium_01 (actual prior Groq run)"


def build_trace() -> dict:
    result = agent.run_agent(QUESTION, mitigated=False, parallel_hint=False, compact_history=False)

    trace_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()

    spans = []
    context_ids = []
    cumulative_input_tokens = 0
    cumulative_output_tokens = 0

    # Distribute the REAL historical total latency across spans proportionally
    # to each span's REAL token count, since the original trace did not record
    # per-span latency (see phase4.md, "Known limitation" -- this trace is the
    # reason Phase 2 now timestamps every event going forward). This is an
    # explicitly labeled ESTIMATE, not a measurement.
    llm_events = [e for e in result["log"] if e["event"] == "llm_call"]
    total_llm_tokens = sum(e["prompt_tokens"] + e["completion_tokens"] for e in llm_events) or 1

    for i, entry in enumerate(result["log"]):
        span_id = f"{trace_id}-s{i}"
        if entry["event"] == "llm_call":
            tok = entry["prompt_tokens"] + entry["completion_tokens"]
            est_latency_ms = round(REAL_HISTORICAL_WALL_CLOCK_S * 1000 * (tok / total_llm_tokens), 1)
            cumulative_input_tokens += entry["prompt_tokens"]
            cumulative_output_tokens += entry["completion_tokens"]
            spans.append({
                "span_id": span_id, "parent_span_id": trace_id, "trace_id": trace_id,
                "span_name": f"generation:llm_call_{entry['iteration']}",
                "start_time": entry.get("timestamp", now),
                "duration_ms": est_latency_ms,
                "duration_ms_provenance": "ESTIMATED -- proportional share of REAL historical total "
                                           f"wall_clock_s ({REAL_HISTORICAL_WALL_CLOCK_S}s, source: "
                                           f"{REAL_HISTORICAL_SOURCE}); original trace did not log per-call "
                                           "latency. Phase 2 instrumentation (this run) now timestamps every "
                                           "event going forward so future traces do not need this estimate.",
                "status": "ok",
                "input_tokens": entry["prompt_tokens"], "output_tokens": entry["completion_tokens"],
                "cost_usd": entry.get("cost_usd"),
                "context_ids": [],
            })
        elif entry["event"] == "tool_call":
            result_full = entry.get("result_full")
            span_context_ids = []
            if isinstance(result_full, list):
                for item in result_full:
                    cid = item.get("source_file") and f"{item.get('source_file')}:p{item.get('page')}"
                    if cid:
                        span_context_ids.append(cid)
            if not span_context_ids:
                span_context_ids = ["unknown -- tool result did not carry a chunk/page id"]
            context_ids.extend(span_context_ids)
            spans.append({
                "span_id": span_id, "parent_span_id": trace_id, "trace_id": trace_id,
                "span_name": f"retrieval:{entry['tool']}",
                "start_time": entry.get("timestamp", now),
                "duration_ms": None,
                "duration_ms_provenance": "UNAVAILABLE -- tool execution time was not separately logged "
                                           "in this run; only the LLM call latency around it was estimated.",
                "status": "ok" if not (isinstance(result_full, dict) and "error" in result_full) else "error",
                "input_tokens": None, "output_tokens": None, "cost_usd": 0.0,
                "context_ids": span_context_ids,
                "args": entry.get("args"),
            })

    trace = {
        "trace_id": trace_id,
        "timestamp": now,
        "timestamp_note": "generation time of THIS extracted trace.json (a reproduction run), "
                           "not the original incident's wall-clock time (no real timestamp was ever "
                           "logged for the original incident -- see drill.md, 'missing log field').",
        "input_type": "developer_question",
        "status": result["status"],
        "prompt_version": "agent-v1-unmitigated-default (PRE-FIX; reproduced on purpose via explicit "
                           "mitigated=False, parallel_hint=False, compact_history=False -- the fixed "
                           "code's new default is agent-v2-mitigated-default, see PROMPT_VERSION in agent.py)",
        "model": MODEL,
        "application_version": "pre-Week-11-fix (agent.py as of commit 91c1f03, before PROMPT_VERSION/"
                                "default changes in this Week 11 branch)",
        "latency_ms": round(REAL_HISTORICAL_WALL_CLOCK_S * 1000, 1),
        "latency_ms_provenance": f"REAL -- {REAL_HISTORICAL_SOURCE}",
        "input_tokens": cumulative_input_tokens,
        "output_tokens": cumulative_output_tokens,
        "total_tokens": result["total_tokens"],
        "total_tokens_provenance": "REAL -- reproduced via the actual agent.py loop logic; token counts in "
                                    "the scripted test double are copied verbatim from the real historical "
                                    "trace for this exact question/path (see week11/fakes.py).",
        "cost_usd": result["total_cost_usd"],
        "cost_usd_provenance": "CALCULATED -- pricing.cost_usd() applied to the real token counts above, "
                                "using the published Groq rate table in agent_race/pricing.py.",
        "context_ids": context_ids,
        "error_type": None,
        "answer": result["answer"],
        "incident_evidence": {
            "forbidden_term_present": "assignee" in (result["answer"] or "").lower(),
            "required_replacement_present": "assignees" in (result["answer"] or "").lower(),
            "verdict": "INCIDENT CONFIRMED -- forbidden term present without the required replacement"
                       if "assignee" in (result["answer"] or "").lower()
                       and "assignees" not in (result["answer"] or "").lower()
                       else "not reproduced in this run",
        },
        "spans": spans,
    }
    return trace


if __name__ == "__main__":
    trace = build_trace()
    out_path = HERE / "trace.json"
    out_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(json.dumps(trace, indent=2))
