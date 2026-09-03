"""Evaluate hybrid BM25+RRF retrieval on the 12 golden questions."""

import json
import time
import statistics

import sys
sys.path.insert(0, "/Users/softsuave/Sam_RAG/developer-rag")

from rag_hybrid import retrieve


def load_golden_set():
    with open("golden_set.jsonl", "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def evaluate_hit_rate_at_k(questions, k=3):
    results = []
    latencies = []

    for q in questions:
        question = q["question"]
        expected_chunk = q["expected_chunk_id"]

        start = time.perf_counter()
        retrieved = retrieve(question, top_k=k)
        latency_ms = (time.perf_counter() - start) * 1000
        latencies.append(latency_ms)

        hit = False
        hit_rank = None
        retrieved_chunks = []

        for i, r in enumerate(retrieved, start=1):
            retrieved_chunks.append({
                "rank": i,
                "chunk_id": r["chunk_id"],
                "distance": round(r.get("distance", 0), 4),
                "preview": r["text"][:100]
            })
            if r["chunk_id"] == expected_chunk:
                hit = True
                hit_rank = i

        results.append({
            "id": q["id"],
            "question": question,
            "expected_chunk_id": expected_chunk,
            "hit": hit,
            "hit_rank": hit_rank,
            "latency_ms": round(latency_ms, 2),
            "retrieved": retrieved_chunks
        })

        status = "HIT" if hit else "MISS"
        rank_str = f"rank {hit_rank}" if hit else "—"
        print(f"{q['id']}: {status} ({rank_str}) | {latency_ms:.1f}ms | {question[:80]}...")

    return results, latencies


def main():
    print("=" * 80)
    print("HYBRID BM25+RRF HIT-RATE@3 EVALUATION")
    print("=" * 80)

    questions = load_golden_set()
    print(f"Loaded {len(questions)} questions\n")

    results, latencies = evaluate_hit_rate_at_k(questions, k=3)

    hits = sum(1 for r in results if r["hit"])
    total = len(results)
    hit_rate = hits / total * 100
    p50_latency = round(statistics.median(latencies), 2)

    print(f"\n{'='*80}")
    print(f"HYBRID HIT-RATE@3: {hits}/{total} = {hit_rate:.1f}%")
    print(f"HYBRID p50 LATENCY: {p50_latency}ms")
    print(f"{'='*80}")

    # Load baseline for comparison
    with open("baseline_results.json", "r") as f:
        baseline = json.load(f)

    print(f"\n{'='*80}")
    print("COMPARISON: BASELINE vs HYBRID")
    print(f"{'='*80}")
    print(f"{'Metric':<25} {'Baseline':>12} {'Hybrid':>12} {'Delta':>10}")
    print(f"{'-'*60}")
    print(f"{'Hit-rate@3':<25} {baseline['hit_rate_at_3']:>11.1f}% {hit_rate:>11.1f}% {hit_rate - baseline['hit_rate_at_3']:>+9.1f}%")
    print(f"{'p50 Latency (ms)':<25} {baseline['p50_latency_ms']:>11.1f} {p50_latency:>11.1f} {p50_latency - baseline['p50_latency_ms']:>+9.1f}")
    print(f"{'-'*60}")

    # Per-question comparison
    with open("baseline_results.json", "r") as f:
        baseline_results = json.load(f)["per_question"]

    print(f"\n{'='*80}")
    print("PER-QUESTION COMPARISON")
    print(f"{'='*80}")
    print(f"{'ID':<4} {'Baseline':<10} {'Hybrid':<10} {'Fixed?'}")
    print(f"{'-'*40}")
    
    for br, hr in zip(baseline_results, results):
        b_hit = "HIT" if br["hit"] else "MISS"
        h_hit = "HIT" if hr["hit"] else "MISS"
        fixed = "✓" if (not br["hit"] and hr["hit"]) else ("—" if br["hit"] == hr["hit"] else "✗")
        print(f"{br['id']:<4} {b_hit:<10} {h_hit:<10} {fixed}")

    # Save hybrid results
    with open("hybrid_results.json", "w", encoding="utf-8") as f:
        json.dump({
            "hit_rate_at_3": hit_rate,
            "hits": sum(1 for r in results if r["hit"]),
            "total": len(results),
            "p50_latency_ms": round(statistics.median(latencies), 2),
            "per_question": results
        }, f, indent=2)

    print(f"\nSaved to hybrid_results.json")


if __name__ == "__main__":
    main()