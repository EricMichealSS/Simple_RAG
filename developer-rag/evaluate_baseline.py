"""Baseline evaluation: measure hit-rate@3 on current retriever over 12 golden questions."""

import json
import time
import statistics

from rag import retrieve


def load_golden_set():
    with open("golden_set.jsonl", "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def evaluate_hit_rate_at_k(questions, k=3):
    """Measure hit-rate@k for each question."""
    results = []
    latencies = []

    for q in questions:
        question = q["question"]
        expected_chunk = q["expected_chunk_id"]
        token = q.get("token")

        # Measure latency
        start = time.perf_counter()
        retrieved = retrieve(question, top_k=k)
        latency_ms = (time.perf_counter() - start) * 1000
        latencies.append(latency_ms)

        # Check if expected chunk is in top-k
        hit = False
        hit_rank = None
        retrieved_chunks = []

        for i, r in enumerate(retrieved, start=1):
            retrieved_chunks.append({
                "rank": i,
                "chunk_id": r["chunk_id"],
                "distance": round(r["distance"], 4),
                "preview": r["text"][:100]
            })
            if r["chunk_id"] == expected_chunk:
                hit = True
                hit_rank = i

        results.append({
            "id": q["id"],
            "question": question,
            "expected_chunk_id": expected_chunk,
            "token": token,
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
    print("BASELINE HIT-RATE@3 EVALUATION")
    print("=" * 80)

    questions = load_golden_set()
    print(f"Loaded {len(questions)} questions\n")

    results, latencies = evaluate_hit_rate_at_k(questions, k=3)

    # Summary
    hits = sum(1 for r in results if r["hit"])
    total = len(results)
    hit_rate = hits / total * 100
    p50_latency = round(statistics.median(latencies), 2)

    print(f"\n{'='*80}")
    print(f"BASELINE HIT-RATE@3: {hits}/{total} = {hit_rate:.1f}%")
    print(f"BASELINE p50 LATENCY: {p50_latency}ms")
    print(f"{'='*80}")

    # Save detailed results
    with open("baseline_results.json", "w", encoding="utf-8") as f:
        json.dump({
            "hit_rate_at_3": hit_rate,
            "hits": hits,
            "total": total,
            "p50_latency_ms": p50_latency,
            "per_question": results
        }, f, indent=2)

    print("\nSaved to baseline_results.json")


if __name__ == "__main__":
    main()