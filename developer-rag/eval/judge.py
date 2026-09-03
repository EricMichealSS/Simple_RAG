"""LLM-as-judge runner. Loads a fixed answer set (eval/answers_25.json), grades
each answer against a judge prompt template, and (optionally) computes
agreement against a hand-label file.

Usage:
    python3 eval/judge.py judge_v1.txt --out judge_v1_verdicts.json --labels labels_25.json
    python3 eval/judge.py judge_v2.txt --out judge_v2_verdicts.json --labels labels_25.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import GROK_API_KEY  # noqa: E402
from openai import OpenAI  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)

# A small, fast Groq model for judging — separate from the heavier answer-
# generation model (groq/compound) to keep judge calls cheap and to avoid
# competing for the same TPM budget that Week 5 Mode 1 found gets exhausted.
JUDGE_MODEL = "openai/gpt-oss-20b"

EVAL_DIR = Path(__file__).parent

import chromadb  # noqa: E402
from config import CHROMA_PATH, COLLECTION_NAME  # noqa: E402

_chroma = chromadb.PersistentClient(path=str(Path(__file__).parent.parent / CHROMA_PATH.lstrip("./")))
_collection = _chroma.get_or_create_collection(name=COLLECTION_NAME)

# Same per-chunk budget as app.py's _build_prompt() MAX_CHUNK_CHARS — the judge
# must see exactly what the generator saw, not a shorter display-only snippet.
# (Week 6 finding: judging against the 300-char text_snippet instead of the
# real 600-char generation context produced false FAILs on facts that were
# genuinely in the generator's context but past char 300 — see report.md.)
MAX_CHUNK_CHARS = 600


def build_context(retrieved_chunks):
    parts = []
    ids = [c["chunk_id"] for c in retrieved_chunks if c.get("chunk_id")]
    full_text = {}
    if ids:
        got = _collection.get(ids=ids, include=["documents"])
        full_text = dict(zip(got["ids"], got["documents"]))
    for c in retrieved_chunks:
        text = full_text.get(c["chunk_id"], c.get("text_snippet", ""))[:MAX_CHUNK_CHARS]
        parts.append(f"[{c['chunk_id']}] ({c.get('source_file', '?')})\n{text}")
    return "\n\n".join(parts)


def judge_one(template: str, question: str, context: str, answer: str) -> dict:
    prompt = template.format(question=question, context=context, answer=answer)
    resp = _client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[
            {"role": "system", "content": "You are a strict, careful grading assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=600,
        reasoning_effort="low",
    )
    text = resp.choices[0].message.content.strip()
    m = re.search(r"VERDICT:\s*(PASS|FAIL)", text, re.IGNORECASE)
    verdict = m.group(1).upper() if m else "UNPARSED"
    return {"verdict": verdict, "raw": text}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("judge_prompt_file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--labels", default=None)
    ap.add_argument("--answers", default="answers_25.json")
    args = ap.parse_args()

    template = (EVAL_DIR / args.judge_prompt_file).read_text(encoding="utf-8")
    answers = json.loads((EVAL_DIR / args.answers).read_text(encoding="utf-8"))

    results = []
    for i, a in enumerate(answers, 1):
        context = build_context(a["retrieved_chunks"])
        print(f"[{i}/{len(answers)}] judging {a['id']} ({a['mode']})...")
        verdict = judge_one(template, a["question"], context, a["answer"])
        results.append({"id": a["id"], "mode": a["mode"], **verdict})
        time.sleep(1.5)

    out_path = EVAL_DIR / args.out
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} verdicts -> {out_path}")

    if args.labels:
        labels = json.loads((EVAL_DIR / args.labels).read_text(encoding="utf-8"))
        label_map = {row["id"]: row["label"] for row in labels}
        agree = 0
        disagreements = []
        for r in results:
            human = label_map.get(r["id"])
            judge_verdict = r["verdict"]
            if human is None:
                continue
            if human == judge_verdict:
                agree += 1
            else:
                disagreements.append({"id": r["id"], "human": human, "judge": judge_verdict, "judge_reasoning": r["raw"]})
        total = sum(1 for r in results if r["id"] in label_map)
        pct = round(100 * agree / total, 1) if total else 0.0
        print(f"\nAgreement vs {args.labels}: {agree}/{total} = {pct}%")
        print(f"Disagreements ({len(disagreements)}):")
        for d in disagreements:
            print(f"  {d['id']}: human={d['human']} judge={d['judge']}")
        (EVAL_DIR / f"agreement_{Path(args.out).stem}.json").write_text(
            json.dumps({"agreement_pct": pct, "agree": agree, "total": total, "disagreements": disagreements}, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
