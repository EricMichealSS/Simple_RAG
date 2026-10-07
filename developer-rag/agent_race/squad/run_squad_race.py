"""The race: single agent vs squad, same 10 questions, same judge, same
ceilings, 3 interleaved repeats. Writes race_runs.json (one row per run) and
traces.jsonl (one line per model call). Protocol: protocol.md. No re-runs
after seeing results; only infrastructure errors are retried (logged).

    python3 squad/run_squad_race.py
"""

import json
import os
import sys
import time
from types import SimpleNamespace

from openai import APIStatusError
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import agent as agent_module  # noqa: E402
import grader as grader_module  # noqa: E402
from agent import run_agent  # noqa: E402
from grader import grade  # noqa: E402
from questions import get_race_questions  # noqa: E402
from metering import trace_from_single, call_with_retry, active_now, QuotaExhausted  # noqa: E402
from orchestrator import run_squad, CEILINGS  # noqa: E402

REPEATS = int(os.environ.get("RACE_REPEATS", "3"))
TRACES = HERE / "traces.jsonl"
RUNS = HERE / "race_runs.json"
SINGLE_CEILINGS = dict(max_tokens=CEILINGS["max_tokens"], max_cost_usd=CEILINGS["max_cost_usd"],
                       max_wall_clock_s=CEILINGS["max_wall_clock_s"], max_iterations=CEILINGS["max_iterations"])


def _retrying(real_client):
    """Wrap an OpenAI client so the UNMODIFIED agent.py / grader.py also retry provider
    rate limits (sleeps excluded from latency via the shared active clock)."""
    real = real_client.chat.completions.create
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: call_with_retry(real, **kw)[0])))


agent_module._client = _retrying(agent_module._client)
grader_module._client = _retrying(grader_module._client)
if os.environ.get("JUDGE_MODEL"):
    grader_module.JUDGE_MODEL = os.environ["JUDGE_MODEL"]


def run_one(arm, q, repeat, infra_log):
    mark = active_now()
    for attempt in (1, 2):
        try:
            if arm == "single":
                mark = active_now()
                r = run_agent(q["question"], **SINGLE_CEILINGS)
                r["wall_clock_s"] = round(active_now() - mark, 3)  # rate-limit waits excluded
                tr = trace_from_single(r, q["race_id"], repeat, sink=TRACES)
                r["trace_id"] = tr.trace_id
                # reconcile: trace tokens must equal the agent's own total
                assert tr.totals()["total_tokens"] == r["total_tokens"], "single trace/total mismatch"
            else:
                r = run_squad(q["question"], race_id=q["race_id"], repeat=repeat, sink=TRACES)
            return r
        except QuotaExhausted:
            raise
        except APIStatusError as e:
            if e.status_code == 413 and arm == "single":  # same deterministic cap, counted as a failed run
                return {"status": "budget_exceeded", "budget_hit": "provider_413_request_too_large", "answer": None,
                        "iterations": 0, "total_tokens": 0, "total_cost_usd": 0.0,
                        "wall_clock_s": round(active_now() - mark, 3), "trace_id": None,
                        "note": "single-arm tokens lost on 413 (understates single)"}
            infra_log.append({"arm": arm, "race_id": q["race_id"], "repeat": repeat, "attempt": attempt,
                              "error": f"{type(e).__name__}: {e}"})
            time.sleep(5)
            continue
        except Exception as e:  # infrastructure error: retry once, log it
            infra_log.append({"arm": arm, "race_id": q["race_id"], "repeat": repeat,
                              "attempt": attempt, "error": f"{type(e).__name__}: {e}"})
            time.sleep(5)
    return {"status": "infra_error", "budget_hit": None, "answer": None, "iterations": 0,
            "total_tokens": 0, "total_cost_usd": 0.0, "wall_clock_s": 0.0, "trace_id": None}


def main():
    questions = get_race_questions()
    rows, infra_log = [], []
    if RUNS.exists():  # resume: keep finished runs, never re-run them
        prev = json.loads(RUNS.read_text(encoding="utf-8"))
        rows, infra_log = prev["rows"], prev["infra_errors"]
    else:
        TRACES.write_text("", encoding="utf-8")
    done = {(r["repeat"], r["race_id"], r["arm"]) for r in rows}
    for repeat in range(1, REPEATS + 1):
        # alternate which arm goes first so neither always benefits from a warm cache / quiet API
        order = ["single", "squad"] if repeat % 2 else ["squad", "single"]
        for q in questions:
            for arm in order:
                if (repeat, q["race_id"], arm) in done:
                    continue
                print(f"[repeat {repeat}] {q['race_id']} {arm}...", flush=True)
                try:
                    r = run_one(arm, q, repeat, infra_log)
                except QuotaExhausted as e:
                    print(f"STOP: daily quota exhausted, resume later. {e}", flush=True)
                    return
                if r["status"] == "infra_error":  # not saved, so a resume re-runs it
                    print("  infra_error after retry; will be re-run on resume", flush=True)
                    continue
                g = grade(q["question"], q["golden_answer"], r.get("answer"))
                rows.append({
                    "repeat": repeat, "race_id": q["race_id"], "level": q["level"], "arm": arm,
                    "trace_id": r.get("trace_id"), "status": r["status"], "budget_hit": r.get("budget_hit"),
                    "verdict": g["verdict"], "grader_reason": g["raw"], "answer": r.get("answer"),
                    "iterations": r["iterations"], "delegations": r.get("delegations"),
                    "total_tokens": r["total_tokens"], "total_cost_usd": r["total_cost_usd"],
                    "wall_clock_s": r["wall_clock_s"],
                })
                RUNS.write_text(json.dumps({"rows": rows, "infra_errors": infra_log}, indent=2), encoding="utf-8")
                time.sleep(1.0)
    print(f"done: {len(rows)} runs, {len(infra_log)} infra errors logged")


if __name__ == "__main__":
    main()
