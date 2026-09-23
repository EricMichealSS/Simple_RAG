"""Answer generation for eval cases — mirrors app.py's stream_answer()/_build_prompt()
exactly (same prompt template, same MAX_TOTAL_CHARS truncation, no retry) so that
regression cases faithfully reproduce today's production behavior, including its
known failure modes (Week 5 Mode 1: Groq 413/TPM errors). Do not "fix" anything
here — that would defeat the point of a regression test.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag_hybrid import retrieve, is_relevant  # noqa: E402
from config import GROK_API_KEY, GENERATION_MODEL  # noqa: E402
from openai import OpenAI  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)


def build_prompt(question, results):
    """Verbatim copy of app.py's _build_prompt()."""
    MAX_CHUNK_CHARS = 600
    MAX_TOTAL_CHARS = 1800

    context_parts = []
    total_chars = 0
    for i, result in enumerate(results, start=1):
        chunk_id = result.get("chunk_id", "unknown")
        source_file = result.get("source_file", result.get("source", "unknown"))
        anchor = result.get("anchor", "")

        text = result["text"][:MAX_CHUNK_CHARS]
        if total_chars + len(text) > MAX_TOTAL_CHARS:
            break
        total_chars += len(text)

        context_parts.append(
            f"SOURCE {i} [chunk_id: {chunk_id} | {source_file}#{anchor}]\n{text}"
        )

    context = "\n\n".join(context_parts)
    return f"""You are a document question-answering assistant.

Your ONLY source of truth is the documentation supplied in CONTEXT.

STRICT RULES:
1. Answer ONLY from the supplied context.
2. Do not use your general knowledge.
3. Do not guess or invent facts.
4. If the answer cannot be supported by the context, respond exactly:
   "I don't know based on the provided documents."
5. For EVERY factual claim, cite the source chunk_id in square brackets,
   e.g. [my-doc:p1:introduction:0].
6. Keep the answer concise and accurate.

CONTEXT:
{context}

USER QUESTION:
{question}

ANSWER (with [chunk_id] citations):"""


def generate_answer(question: str, top_k: int = 5) -> dict:
    """Run retrieval + generation for one question, exactly as app.py does on
    a live "Ask" click. Returns retrieved chunks, the raw answer text, and timings.
    """
    t0 = time.perf_counter()
    results = retrieve(question, top_k=top_k, use_hybrid=True, use_rerank=True)
    retrieval_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    if not results or not is_relevant(results):
        answer = "I don't know based on the provided documents."
    else:
        prompt = build_prompt(question, results)
        try:
            resp = _client.chat.completions.create(
                model=GENERATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a document QA assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=512,
            )
            answer = resp.choices[0].message.content
        except Exception as e:
            chunks_md = "\n\n---\n\n".join(
                f"**Source {i}** `[{r.get('chunk_id', '?')}]` ({r.get('source_file', '?')})\n{r['text'][:300]}..."
                for i, r in enumerate(results, 1)
            )
            answer = (
                f"⚠️ **Generation unavailable ({type(e).__name__}).**\n\n"
                f"Error: {str(e)}\n\n"
                f"Relevant chunks:\n\n{chunks_md}"
            )
    gen_ms = (time.perf_counter() - t1) * 1000

    return {
        "question": question,
        "retrieved_chunks": [
            {
                "chunk_id": r.get("chunk_id"),
                "source_file": r.get("source_file"),
                "distance": round(r.get("distance", 0), 4),
                "rerank_score": round(r["rerank_score"], 4) if r.get("rerank_score") is not None else None,
                "text_snippet": r.get("text", "")[:300],
            }
            for r in results
        ],
        "answer": answer,
        "retrieval_ms": round(retrieval_ms, 1),
        "gen_ms": round(gen_ms, 1),
        "model": GENERATION_MODEL,
    }
