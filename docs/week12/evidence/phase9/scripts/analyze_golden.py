"""Phase 9 analysis of a saved golden run. Reads results (+ traces dir for the distance re-score); writes nothing but prints JSON.
usage: python analyze_golden.py RESULTS.json TRACES_DIR"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, "C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_eval import golden, trajectory  # noqa: E402

rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
traces = Path(sys.argv[2])
gold = {r["id"]: r for r in golden.load_golden()}
out = {"n": len(rows)}

out["outcomes"] = dict(Counter(r["outcome"] for r in rows))
out["by_level"] = {lvl: dict(Counter(r["outcome"] for r in rows if r["level"] == lvl)) for lvl in ("easy", "medium", "hard")}
ans = [r for r in rows if r["answerable"]]
unans = [r for r in rows if not r["answerable"]]
out["answerable"] = {"n": len(ans), "judge_pass": sum(bool(r["outcome_pass"]) for r in ans),
                     "failed": [(r["id"], r["outcome"], r["trace_id"]) for r in ans if not r["outcome_pass"]]}
out["unanswerable"] = {"n": len(unans), "correct_refusals": sum(r["outcome"] == "abstain" and bool(r["outcome_pass"]) for r in unans),
                       "answered_anyway": [(r["id"], r["trace_id"]) for r in unans if r["outcome"] == "answered"]}
out["overall_judge_pass"] = {"count": sum(bool(r["outcome_pass"]) for r in rows), "of": len(rows)}
out["by_level_judge_pass"] = {lvl: [sum(bool(r["outcome_pass"]) for r in rows if r["level"] == lvl), sum(r["level"] == lvl for r in rows)] for lvl in ("easy", "medium", "hard")}
out["false_refusals_on_answerable"] = [(r["id"], r["reason"][:60], r["trace_id"]) for r in ans if r["outcome"] == "abstain"]
out["degraded"] = [(r["id"], r["reason"][:80]) for r in rows if r["outcome"] == "degraded"]
out["errors"] = [(r["id"], r["reason"][:80]) for r in rows if r["outcome"] == "error"]
out["clarify"] = [r["id"] for r in rows if r["outcome"] == "clarify"]

# retrieval
with_gold = [r for r in ans if r["gold_page_retrieved"] is not None]
out["gold_page_retrieved"] = {"count": sum(bool(r["gold_page_retrieved"]) for r in with_gold), "of": len(with_gold),
                              "missed": [(r["id"], r["trace_id"]) for r in with_gold if not r["gold_page_retrieved"]],
                              "note": "page-level proxy: a hit means a chunk from the gold PAGE was retrieved, not necessarily the answer-bearing chunk"}
out["wrong_version_chunks_total"] = sum(len(r["wrong_version_chunks"]) for r in rows)
out["version_status"] = dict(Counter(r["version_status"] for r in rows))

# comparison questions: were both versions retrieved?
import chromadb  # noqa: E402
col = chromadb.PersistentClient(path="C:/Projects/AI Learning/Simple_RAG/developer-rag/data/chroma").get_collection("race_docs")
meta = dict(zip(*[(lambda g: (g["ids"], [m["api_version"] for m in g["metadatas"]]))(col.get(include=["metadatas"]))[i] for i in (0, 1)]))
comp = [r for r in rows if r["comparison"]]
both = [r for r in comp if {meta.get(c) for c in r["retrieved"]} >= set(r["resolved"])]
out["comparison_questions"] = {"n": len(comp), "both_versions_retrieved": len(both), "missing_a_side": [(r["id"], r["trace_id"]) for r in comp if r not in both]}

# citations / answers
answered = [r for r in rows if r["outcome"] == "answered"]
out["citations"] = {"answered": len(answered), "answered_with_zero_citations": [r["id"] for r in answered if not r["citations"]],
                    "mean_per_answered": round(sum(r["citations"] for r in answered) / len(answered), 2)}
out["code_blocks"] = {"answers_with_code": sum(r["code_blocks"] > 0 for r in rows)}
out["non_ascii_date_in_answer"] = {"count": sum(r["non_ascii_date_in_answer"] for r in rows), "of": len(rows),
                                   "ids": [r["id"] for r in rows if r["non_ascii_date_in_answer"]],
                                   "harmed_judge_fail": [r["id"] for r in rows if r["non_ascii_date_in_answer"] and not r["outcome_pass"]]}
out["deprecation_notes"] = {"answers_with_notes": sum(r["deprecation_notes"] > 0 for r in rows)}
out["security_events"] = {"total": sum(r["security_events"] for r in rows), "ids": [r["id"] for r in rows if r["security_events"]]}

# cost
costs = [r["cost_usd"] for r in rows if r["cost_status"] == "calculated"]
out["cost"] = {"priced": len(costs), "of": len(rows), "total_usd": round(sum(costs), 6), "mean_usd": round(sum(costs) / len(costs), 6),
               "max_usd": round(max(costs), 6), "prompt_tokens": sum(r["prompt_tokens"] for r in rows), "completion_tokens": sum(r["completion_tokens"] for r in rows),
               "mean_tokens_per_query": round(sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows) / len(rows)),
               "llm_calls_per_query": dict(Counter(r["llm_calls"] for r in rows))}

# trajectory
scored = [r for r in rows if r.get("score")]
out["trajectory"] = {"ids": [r["id"] for r in scored], **trajectory.summarize(scored)}
out["trajectory_rows"] = [{"id": r["id"], "trace_id": r["trace_id"], "tools": r["score"]["tool_sequence"], "versions": r["score"]["versions_seen"], "path_ok": r["score"]["path_ok"],
                           "unnecessary": r["score"]["unnecessary_calls"], "missing": r["score"]["missing_required"], "outcome": r["outcome"],
                           "outcome_pass": r["outcome_pass"], "gap": r["score"]["gap"]} for r in scored]
tool_counts = Counter(tuple(r["tool_sequence"]) for r in rows)
out["tool_sequences_all_45"] = {" > ".join(k): v for k, v in tool_counts.most_common()}

# Week 5 mode 5 re-score: do retrieve spans log a real distance?
dist = []
for f in traces.glob("*.jsonl"):
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            s = json.loads(line)
        except ValueError:
            continue
        a = s.get("attributes") or {}
        if s.get("name") in ("retrieve", "mcp.tool.search_docs") and "top_distance" in a:
            dist.append(a["top_distance"])
out["distance_logged"] = {"spans_with_top_distance": len(dist), "exactly_1.0": sum(d == 1.0 for d in dist),
                          "min": round(min(dist), 3) if dist else None, "max": round(max(dist), 3) if dist else None}
print(json.dumps(out, indent=1, default=str))
