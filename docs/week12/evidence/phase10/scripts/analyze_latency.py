"""Phase 10 analysis of a latency run (golden --between N). Prints JSON.
usage: python analyze_latency.py LATENCY_RESULTS.json LATENCY_TRACES_DIR FIRST_RUN_RESULTS.json"""
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, "C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine.tracequery import percentile  # noqa: E402

ALL = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
rows = [r for r in ALL if r["llm_calls"] >= 1]          # requests that reached the model: the valid sample for latency-with-model and cost
DEG = [r for r in ALL if r["outcome"] == "degraded"]      # provider quota exhausted: engine-only path
traces = Path(sys.argv[2])
first = {r["id"]: r for r in json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))}
out = {"n_all": len(ALL), "n_with_model_call": len(rows), "n_degraded_by_quota": len(DEG), "degraded_reasons": dict(Counter(r["reason"][:70] for r in DEG)),
       "degraded_engine_only_latency_ms": None, "n": len(rows), "latency_run_flag": sorted({r.get("latency_run") for r in rows}, key=str)}


def _m(xs):
    return round(statistics.mean(xs), 6) if xs else None


def dist(xs, nd=1):
    xs = sorted(xs)
    if not xs:
        return None                      # e.g. no comparison question reached the model before the quota ran out
    return {"n": len(xs), "min": round(xs[0], nd), "p50": round(percentile(xs, 50), nd), "p95": round(percentile(xs, 95), nd),
            "max": round(xs[-1], nd), "mean": round(statistics.mean(xs), nd)}


lat = [r["latency_ms"] for r in rows]
out["latency_ms_all"] = dist(lat)
out["latency_ms_by_outcome"] = {k: dist([r["latency_ms"] for r in rows if r["outcome"] == k]) for k in sorted({r["outcome"] for r in rows})}
out["latency_ms_by_shape"] = {"one search (single version)": dist([r["latency_ms"] for r in rows if r["tool_sequence"].count("search_docs") == 1]),
                              "two searches (comparison)": dist([r["latency_ms"] for r in rows if r["tool_sequence"].count("search_docs") == 2])}
out["latency_ms_by_level"] = {k: dist([r["latency_ms"] for r in rows if r["level"] == k]) for k in ("easy", "medium", "hard")}
out["slowest"] = sorted(((r["id"], round(r["latency_ms"]), r["trace_id"], r["llm_calls"]) for r in rows), key=lambda x: -x[1])[:4]
out["note_p99"] = "not reported: n=45; p95 is set by the 3rd-slowest request"

stages = defaultdict(list)
for r in rows:
    for k, v in (r.get("stage_ms") or {}).items():
        stages[k].append(v)
out["stage_ms"] = {k: {**dist(v, 2), "requests_with_stage": len(v)} for k, v in sorted(stages.items(), key=lambda kv: -statistics.median(kv[1]))}
gen_share = [r["stage_ms"].get("generate", 0) / r["latency_ms"] for r in rows if r.get("stage_ms") and r["latency_ms"]]
out["generate_share_of_request"] = {"p50": round(percentile(sorted(gen_share), 50), 3), "min": round(min(gen_share), 3), "max": round(max(gen_share), 3)}
non_gen = [r["latency_ms"] - r["stage_ms"].get("generate", 0) for r in rows if r.get("stage_ms")]
out["everything_except_generate_ms"] = dist(non_gen)

cost = [r["cost_usd"] for r in rows if r["cost_status"] == "calculated"]
out["cost_usd"] = {**dist(cost, 6), "total": round(sum(cost), 6), "priced": len(cost), "of": len(rows)}
out["cost_by_outcome"] = {k: {"n": sum(r["outcome"] == k for r in rows), "mean": _m([r["cost_usd"] for r in rows if r["outcome"] == k])} for k in ("answered", "abstain")}
out["cost_by_shape"] = {"one search": _m([r["cost_usd"] for r in rows if r["tool_sequence"].count("search_docs") == 1]),
                        "comparison": _m([r["cost_usd"] for r in rows if r["tool_sequence"].count("search_docs") == 2])}
pt, ct = [r["prompt_tokens"] for r in rows], [r["completion_tokens"] for r in rows]
out["tokens"] = {"prompt": dist(pt, 0), "completion": dist(ct, 0), "total_per_query": dist([a + b for a, b in zip(pt, ct)], 0), "sum_total": sum(pt) + sum(ct)}
out["llm_calls"] = dict(Counter(r["llm_calls"] for r in rows))
judge_tokens = [r["judge"]["tokens"] for r in rows if r.get("judge") and isinstance(r["judge"].get("tokens"), int)]
out["judge_tokens"] = {"n": len(judge_tokens), "sum": sum(judge_tokens),
                       "usd_bounds_gpt_oss_20b": [round(sum(judge_tokens) * 0.075 / 1e6, 5), round(sum(judge_tokens) * 0.30 / 1e6, 5)],
                       "note": "only the total token count is recorded, so cost is bounded between the all-input and all-output rate"}

L = re.compile("[\u2010-\u2012\u2212]")
G = re.compile("(?<=[A-Za-z0-9])[\u2013\u2014\u2212](?=[A-Za-z0-9])")
ans = [r for r in rows if r["outcome"] == "answered"]
out["lookalike_hyphens_in_delivered_answers"] = {"answered": len(ans), "with_lookalike": sum(bool(L.search(r["answer"]) or G.search(r["answer"])) for r in ans)}

out["outcomes"] = dict(Counter(r["outcome"] for r in rows))
out["judge_pass"] = {"count": sum(bool(r["outcome_pass"]) for r in rows), "of": len(rows), "failed": [r["id"] for r in rows if not r["outcome_pass"]]}
both = [r for r in rows if r["id"] in first]
out["vs_first_run"] = {"same_outcome": sum(r["outcome"] == first[r["id"]]["outcome"] for r in both), "same_judge_verdict": sum(bool(r["outcome_pass"]) == bool(first[r["id"]]["outcome_pass"]) for r in both),
                       "identical_answer_text_after_hyphen_repair": sum(L.sub("-", first[r["id"]]["answer"]) == r["answer"] for r in both), "of": len(both),
                       "judge_flipped": [(r["id"], first[r["id"]]["outcome_pass"], r["outcome_pass"]) for r in both if bool(r["outcome_pass"]) != bool(first[r["id"]]["outcome_pass"])]}
errs = [(r["id"], r["outcome"], r["reason"][:80]) for r in rows if r["outcome"] in ("degraded", "error", "clarify")]
out["non_answer_outcomes"] = errs
sizes = {p.name: p.stat().st_size for p in traces.glob("*.jsonl")}
out["trace_bytes"] = {"files": sizes, "requests_in_files": len(ALL), "per_request_total": round(sum(sizes.values()) / len(ALL)),
                      "note": "the trace files hold ALL requests of the run (model-answered and quota-degraded), so divide by len(ALL); the first version of this line divided by the 19 model-answered ones and overstated it 2.4x"}
out["degraded_engine_only_latency_ms"] = dist([r["latency_ms"] for r in DEG])
out["degraded_by_shape"] = {"one search": dist([r["latency_ms"] for r in DEG if r["tool_sequence"].count("search_docs") == 1]), "two searches": dist([r["latency_ms"] for r in DEG if r["tool_sequence"].count("search_docs") == 2])}
out["degraded_stage_ms"] = {k: dist(v, 2) for k, v in sorted({k: [r["stage_ms"][k] for r in DEG if k in r["stage_ms"]] for r in DEG for k in r["stage_ms"]}.items())}
print(json.dumps(out, indent=1, default=str))
