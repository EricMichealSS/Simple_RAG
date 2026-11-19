"""Week 5 mode-5 re-score + sufficiency-threshold context, on live data, no model call.
For each golden question: dense retrieval inside the version scope the engine resolved; record the real distances."""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine.config import EngineConfig  # noqa: E402
from sdk_engine.retrieval import ChromaRetriever, RetrievalScope, make_embed_fn, open_collection  # noqa: E402

cfg = EngineConfig.from_env()
rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
retr = ChromaRetriever(open_collection(cfg), make_embed_fn(cfg.embedding_model), max_top_k=cfg.max_top_k, hybrid=False)
per = []
for r in rows:
    vs = tuple(r["resolved"])
    scope = RetrievalScope(versions=vs, mode="comparison" if len(vs) > 1 else "strict")
    dists = [p.dense_distance for p in retr.search(r["question"], scope, 5).passages]
    per.append({"id": r["id"], "answerable": r["answerable"], "dists": [None if d is None else round(d, 3) for d in dists]})
all_d = [d for p in per for d in p["dists"]]
top1 = {k: [min(d for d in p["dists"] if d is not None) for p in per if p["answerable"] is k and any(d is not None for d in p["dists"])] for k in (True, False)}
thr = cfg.max_dense_distance


def spread(xs):
    return {"n": len(xs), "min": round(min(xs), 3), "median": round(statistics.median(xs), 3), "max": round(max(xs), 3)}


out = {"passages": len(all_d), "none": sum(d is None for d in all_d), "exactly_1.0": sum(d == 1.0 for d in all_d),
       "range": [min(d for d in all_d if d is not None), max(d for d in all_d if d is not None)],
       "top1_answerable": spread(top1[True]), "top1_unanswerable": spread(top1[False]),
       "threshold": thr, "answerable_top1_above_threshold": sum(x > thr for x in top1[True]),
       "unanswerable_top1_below_threshold": sum(x <= thr for x in top1[False]),
       "overlap": {"max_answerable_top1": round(max(top1[True]), 3), "min_unanswerable_top1": round(min(top1[False]), 3)},
       "per_question": per}
print(json.dumps(out, indent=1))
