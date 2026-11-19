"""Phase 10: how does ChromaRetriever.search() scale with the number of chunks in the requested version?

SYNTHETIC corpora (random vocabulary text, deterministic hash embeddings, in-memory Chroma): this isolates the retriever's own cost, in particular the
`collection.get(where=..., include=[documents, metadatas])` that search() performs on every query to build its candidate pool. It says nothing about
recall, and nothing about a real embedding model's cost (measured separately below, with the real model, on the real queries).

usage: python scale_retrieval.py OUT.json
"""
import json
import random
import statistics
import sys
import time

sys.path.insert(0, "C:/Projects/AI Learning/Simple_RAG/developer-rag")
import chromadb  # noqa: E402

from sdk_engine.retrieval import ChromaRetriever, RetrievalScope, _hash_embed, make_embed_fn  # noqa: E402

rng = random.Random(12)
VOCAB = [f"w{n}" for n in range(3000)]
VERSION = "2025-06-01"


def build(n):
    client = chromadb.EphemeralClient()
    try:
        client.delete_collection("scale")
    except Exception:
        pass
    col = client.create_collection(name="scale", metadata={"hnsw:space": "cosine"})
    for start in range(0, n, 2000):
        ids, docs, metas = [], [], []
        for i in range(start, min(n, start + 2000)):
            text = " ".join(rng.choice(VOCAB) for _ in range(120))      # ~120 words, a chunk-sized text
            ids.append(f"c{i}")
            docs.append(text)
            metas.append({"api_version": VERSION, "source_file": "synthetic.pdf", "page": i % 50 + 1, "anchor": "top", "content_type": "spec",
                          "endpoint": "create_issue", "has_diff": False})
        col.add(ids=ids, documents=docs, metadatas=metas, embeddings=[_hash_embed(d) for d in docs])
    return col


def timed(fn, reps):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return {"p50_ms": round(statistics.median(ts), 2), "max_ms": round(ts[-1], 2), "n": len(ts)}


out = {"synthetic_search": {}, "note": "synthetic text, hash embeddings, in-memory Chroma, dense-only (the engine default), one version scope"}
queries = [" ".join(rng.choice(VOCAB) for _ in range(8)) for _ in range(30)]
for n in (39, 1000, 5000, 20000):
    col = build(n)
    r = ChromaRetriever(col, _hash_embed, hybrid=False)
    scope = RetrievalScope(versions=(VERSION,))
    r.search(queries[0], scope, 5)                                     # warm
    qi = iter(queries * 3)
    out["synthetic_search"][n] = timed(lambda: r.search(next(qi), scope, 5), 30)
    pool_only = timed(lambda: col.get(where={"api_version": VERSION}, include=["documents", "metadatas"]), 15)
    out["synthetic_search"][n]["of_which_pool_fetch_p50_ms"] = pool_only["p50_ms"]
    print(n, out["synthetic_search"][n], flush=True)

# the real embedding model, on the real questions (one query at a time, as the engine does)
t0 = time.perf_counter()
embed = make_embed_fn("BAAI/bge-small-en-v1.5")
embed("warm up")
out["embedding_model_load_s"] = round(time.perf_counter() - t0, 2)
sys.path.insert(0, "C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_eval import golden  # noqa: E402

qs = [r["question"] for r in golden.load_golden()]
out["real_query_embedding"] = timed(lambda: embed(qs[len(out) % len(qs)]), 40)
ts = []
for q in qs:
    t0 = time.perf_counter()
    embed(q)
    ts.append((time.perf_counter() - t0) * 1000)
ts.sort()
out["real_query_embedding_45_questions"] = {"p50_ms": round(statistics.median(ts), 1), "p95_ms": round(ts[int(0.95 * (len(ts) - 1))], 1), "max_ms": round(ts[-1], 1), "n": len(ts)}
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "synthetic_search"}, indent=1))
