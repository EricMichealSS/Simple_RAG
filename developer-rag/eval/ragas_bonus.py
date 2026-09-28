"""Bonus challenge (W6-Task-Set-E.md section 5) — RAGAS faithfulness and
context precision on the docs-backed cases.

Not the `ragas` pip package (avoided to keep this Python 3.9 venv's pinned
ML stack — numpy/torch/sentence-transformers — from getting dependency-
resolved into something that breaks chromadb/rag_hybrid.py). Implements the
same two metric definitions by hand, using the same Groq-backed LLM calls
`judge.py` already relies on:

Faithfulness = (# claims in the answer supported by the retrieved context)
               / (# claims in the answer)

Context precision@K (RAGAS's definition) = for each rank k in the retrieved
list, precision@k = (# relevant chunks in the top k) / k; then take the
mean of precision@k over only the ranks where that chunk IS relevant. A
chunk's relevance is itself an LLM judgment ("does this chunk help answer
the question").

Goal: find a case that scores high faithfulness (every claim is genuinely
grounded in *some* retrieved chunk) while still being the wrong answer to
the question asked (a version-confusion / relevancy failure, not a
faithfulness failure) — the "confidently, faithfully wrong" case the bonus
asks for — and show why the average across cases hides it.
"""

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import GROK_API_KEY  # noqa: E402
from openai import OpenAI  # noqa: E402
from judge import build_context, _collection, MAX_CHUNK_CHARS  # noqa: E402 (reuse the fixed full-chunk lookup)

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)
MODEL = "openai/gpt-oss-20b"

EVAL_DIR = Path(__file__).parent

FAITHFULNESS_PROMPT = """You are computing a faithfulness score for a RAG answer.

QUESTION:
{question}

CONTEXT (the chunks the assistant actually had available):
{context}

ANSWER:
{answer}

Step 1: List every distinct factual claim the ANSWER makes, as short standalone statements.
Step 2: For each claim, decide if it is DIRECTLY supported by the CONTEXT (true) or not
supported / not checkable from the CONTEXT (false). A claim about a v3 SDK method counts as
supported only if that specific method's behavior is in the CONTEXT -- do not use outside
knowledge of GitHub's real API.

Respond with ONLY a JSON object, no other text:
{{"claims": [{{"claim": "...", "supported": true}}, {{"claim": "...", "supported": false}}]}}
"""

CONTEXT_PRECISION_PROMPT = """You are labeling retrieved chunks for relevance, to compute
Context Precision.

QUESTION:
{question}

Chunks in retrieval rank order (rank 1 = top):
{numbered_chunks}

For EACH chunk (in order), decide if it is relevant to answering the QUESTION (true) or not (false).

Respond with ONLY a JSON object, no other text, one boolean per chunk in order:
{{"relevance": [true, false, ...]}}
"""


def _call_json(prompt: str) -> dict:
    resp = _client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You output only valid JSON, nothing else."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=800,
        reasoning_effort="low",
    )
    text = resp.choices[0].message.content.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def faithfulness(question: str, context: str, answer: str) -> dict:
    data = _call_json(FAITHFULNESS_PROMPT.format(question=question, context=context, answer=answer))
    claims = data.get("claims", [])
    if not claims:
        return {"score": None, "claims": []}
    supported = sum(1 for c in claims if c.get("supported"))
    return {"score": supported / len(claims), "claims": claims}


def context_precision(question: str, retrieved_chunks: list) -> dict:
    ids = [c["chunk_id"] for c in retrieved_chunks if c.get("chunk_id")]
    got = _collection.get(ids=ids, include=["documents"]) if ids else {"ids": [], "documents": []}
    full_text = dict(zip(got["ids"], got["documents"]))
    numbered = "\n\n".join(
        f"{i}. [{c['chunk_id']}] {full_text.get(c['chunk_id'], c.get('text_snippet', ''))[:MAX_CHUNK_CHARS]}"
        for i, c in enumerate(retrieved_chunks, 1)
    )
    data = _call_json(CONTEXT_PRECISION_PROMPT.format(question=question, numbered_chunks=numbered))
    relevance = data.get("relevance", [])
    n = len(retrieved_chunks)
    if len(relevance) != n or not any(relevance):
        return {"score": 0.0 if relevance else None, "relevance": relevance}

    precisions_at_k = []
    relevant_so_far = 0
    for k in range(1, n + 1):
        if relevance[k - 1]:
            relevant_so_far += 1
            precisions_at_k.append(relevant_so_far / k)
    score = sum(precisions_at_k) / sum(1 for r in relevance if r)
    return {"score": score, "relevance": relevance}


def main():
    answers = json.loads((EVAL_DIR / "answers_25.json").read_text(encoding="utf-8"))
    # Docs-backed cases only: real generated answers, not "I don't know" and not
    # a Groq-error fallback block — faithfulness/context-precision are undefined
    # for a refusal (there are no claims to check).
    docs_backed = [
        a for a in answers
        if "I don't know" not in a["answer"] and "Generation unavailable" not in a["answer"]
    ]
    print(f"Docs-backed cases (real, claim-bearing answers): {len(docs_backed)}/{len(answers)}\n")

    rows = []
    for i, a in enumerate(docs_backed, 1):
        print(f"[{i}/{len(docs_backed)}] {a['id']}...", flush=True)
        context = build_context(a["retrieved_chunks"])
        faith = faithfulness(a["question"], context, a["answer"])
        time.sleep(1.2)
        prec = context_precision(a["question"], a["retrieved_chunks"])
        time.sleep(1.2)
        rows.append({
            "id": a["id"], "mode": a["mode"], "question": a["question"],
            "faithfulness": faith["score"], "context_precision": prec["score"],
            "claims": faith["claims"], "relevance": prec["relevance"],
        })

    valid_faith = [r["faithfulness"] for r in rows if r["faithfulness"] is not None]
    valid_prec = [r["context_precision"] for r in rows if r["context_precision"] is not None]
    avg_faith = sum(valid_faith) / len(valid_faith) if valid_faith else 0
    avg_prec = sum(valid_prec) / len(valid_prec) if valid_prec else 0

    print("\n" + "=" * 70)
    print(f"{'id':<6}{'mode':<32}{'faithfulness':>14}{'ctx_precision':>15}")
    for r in rows:
        f = f"{r['faithfulness']:.2f}" if r["faithfulness"] is not None else "n/a"
        p = f"{r['context_precision']:.2f}" if r["context_precision"] is not None else "n/a"
        print(f"{r['id']:<6}{r['mode']:<32}{f:>14}{p:>15}")
    print("-" * 70)
    print(f"{'AVERAGE':<38}{avg_faith:>14.2f}{avg_prec:>15.2f}")

    out_path = EVAL_DIR / "ragas_bonus_results.json"
    out_path.write_text(json.dumps({"rows": rows, "avg_faithfulness": avg_faith, "avg_context_precision": avg_prec}, indent=2), encoding="utf-8")
    print(f"\nFull results -> {out_path}")


if __name__ == "__main__":
    main()
