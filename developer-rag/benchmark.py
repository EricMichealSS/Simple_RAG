"""Benchmark: hit-in-top-5 for 8 known-answer questions.

Two chunking strategies on the SAME 6 pages:
  v3_benchmark_current      fixed-size sliding window
  v3_benchmark_structured   structure-aware (headers, tables, fences)
"""

import json

from rag import retrieve


def load_questions():
    with open("benchmark_questions.json", "r", encoding="utf-8") as file:
        data = json.load(file)
    return data["questions"]


def run_benchmark(collection_name, questions):
    results = []

    for question in questions:
        qid = question["id"]
        qtext = question["question"]
        expected_page = question["expected_page"]
        expected_section = question["expected_section"]

        hits = retrieve(
            qtext,
            top_k=5,
            collection_name=collection_name,
        )

        hit_in_top5 = False
        hit_rank = None
        hit_chunk_id = None

        for rank, hit in enumerate(hits, start=1):
            if (hit.get("page_id") == expected_page
                    and expected_section in (hit.get("anchor", "") + hit.get("section", ""))):
                hit_in_top5 = True
                hit_rank = rank
                hit_chunk_id = hit.get("chunk_id")
                break

        results.append({
            "id": qid,
            "question": qtext,
            "expected_page": expected_page,
            "expected_section": expected_section,
            "hit_in_top5": hit_in_top5,
            "hit_rank": hit_rank,
            "hit_chunk_id": hit_chunk_id,
            "retrieved": [
                {
                    "rank": i + 1,
                    "chunk_id": h.get("chunk_id"),
                    "page_id": h.get("page_id"),
                    "anchor": h.get("anchor"),
                    "section": h.get("section"),
                    "distance": round(h.get("distance", 0), 4),
                    "preview": h.get("text", "")[:120],
                }
                for i, h in enumerate(hits)
            ],
        })

    return results


def print_results(name, results):
    print(f"\n{'='*80}")
    print(f"BENCHMARK: {name}")
    print(f"{'='*80}")

    hits = sum(1 for r in results if r["hit_in_top5"])
    total = len(results)

    for r in results:
        status = "HIT" if r["hit_in_top5"] else "MISS"
        rank = f"rank {r['hit_rank']}" if r["hit_in_top5"] else "—"
        print(f"\n{r['id']}: {r['question']}")
        print(f"  Expected: {r['expected_page']}#{r['expected_section']}")
        print(f"  Result:   {status} ({rank})  chunk_id={r['hit_chunk_id']}")

        for h in r["retrieved"]:
            prefix = "→" if h["rank"] == r["hit_rank"] else " "
            print(f"    {prefix} #{h['rank']} | {h['chunk_id']} | dist={h['distance']} | {h['preview']}")

    print(f"\n{'='*80}")
    print(f"HIT-IN-TOP-5: {hits}/{total}")
    print(f"{'='*80}")

    return hits, total


def main():
    questions = load_questions()

    print("=" * 80)
    print("RUNNING BENCHMARK: 8 known-answer questions, 2 chunking strategies")
    print("=" * 80)

    results_current = run_benchmark("v3_benchmark_current", questions)
    hits_current, total = print_results("v3_benchmark_current (fixed-size)", results_current)

    results_structured = run_benchmark("v3_benchmark_structured", questions)
    hits_structured, _ = print_results("v3_benchmark_structured (structure-aware)", results_structured)

    print(f"\n{'='*80}")
    print("SUMMARY TABLE")
    print(f"{'='*80}")
    print(f"{'Strategy':<35} {'Hit-in-top-5':<15}")
    print(f"{'-'*50}")
    print(f"{'v3_benchmark_current (fixed-size)':<35} {hits_current}/{total}")
    print(f"{'v3_benchmark_structured (structured)':<35} {hits_structured}/{total}")
    print(f"{'='*80}")

    with open("benchmark_dump.json", "w", encoding="utf-8") as file:
        json.dump({
            "v3_benchmark_current": results_current,
            "v3_benchmark_structured": results_structured,
        }, file, indent=2)

    print("\nFull dump written to benchmark_dump.json")


if __name__ == "__main__":
    main()