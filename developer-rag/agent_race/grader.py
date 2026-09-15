"""Grades one system's answer against the golden-set answer. Same discipline
as Week 6's judge.py: a small, separate model gives a binary PASS/FAIL,
never the model that produced the answer.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import GROK_API_KEY  # noqa: E402
from openai import OpenAI  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0)
JUDGE_MODEL = "openai/gpt-oss-20b"

GRADE_PROMPT = """Grade whether SYSTEM_ANSWER is factually consistent with GOLDEN_ANSWER for the
given QUESTION. PASS if it states the same key fact(s), even in different words. If
GOLDEN_ANSWER says the information is not available/not provided in context, PASS only if
SYSTEM_ANSWER also declines to answer or says it doesn't know -- a confident, specific answer
in that case is FAIL (a hallucination on a question that should have been refused).

QUESTION: {question}
GOLDEN_ANSWER: {golden_answer}
SYSTEM_ANSWER: {system_answer}

Respond with exactly one line: VERDICT: PASS or VERDICT: FAIL, then one sentence why.
"""


def grade(question: str, golden_answer: str, system_answer: str) -> dict:
    if not system_answer:
        return {"verdict": "FAIL", "raw": "no answer produced (budget exceeded or empty)"}
    prompt = GRADE_PROMPT.format(question=question, golden_answer=golden_answer, system_answer=system_answer)
    resp = _client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[{"role": "system", "content": "You are a strict, careful grading assistant."},
                   {"role": "user", "content": prompt}],
        temperature=0.0, max_tokens=300, reasoning_effort="low",
    )
    text = resp.choices[0].message.content.strip()
    m = re.search(r"VERDICT:\s*(PASS|FAIL)", text, re.IGNORECASE)
    verdict = m.group(1).upper() if m else "UNPARSED"
    return {"verdict": verdict, "raw": text}
