"""Run the 45-question Stack B golden set through the integrated engine (real index, real MCP server, real model) and record, per question,
everything Phase 9 needs: the outcome, the answer, the tool path (from the trace), whether the gold page was retrieved, and a golden-answer grade.

One run feeds both Phase 9 deliverables: the 10 race questions are the Week 8 trajectory sample, and all 45 are the denominator for the taxonomy
re-score. Version handling follows a caller who knows their version: `explicit_version` is the golden metadata's version when it has exactly one;
a question with no version anywhere is asked as-is (the engine then asks which version, which is itself a result)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sdk_engine import tracequery
from sdk_engine.config import ENGINE_ROOT, get_api_key

from . import trajectory
from .harness import Env

GOLDEN_DIR = ENGINE_ROOT / "agent_race" / "golden_sets"
_CHUNK_RE = re.compile(r"^(github_combined|swagger_v2|swagger_v3)_p(\d+)#")
_NON_ASCII_DATE = re.compile(r"\d{4}[‐-―−]\d{2}[‐-―−]\d{2}")
GRADER_MODEL = "openai/gpt-oss-20b"
# The Week 7 grader's prompt (agent_race/grader.py), reused so outcome grading here is comparable with Week 7/8.
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


def load_golden() -> List[Dict[str, Any]]:
    rows = []
    for level in ("easy", "medium", "hard"):
        for r in json.loads((GOLDEN_DIR / f"gs_{level}.json").read_text(encoding="utf-8")):
            meta = r.get("metadata", {})
            rows.append({"id": f"{level}_{r['id']}", "level": level, "question": r["question"], "gold_answer": r["answer"],
                         "version": meta.get("api_version"), "versions": meta.get("api_versions"), "expected_chunk_id": r.get("expected_chunk_id"),
                         "answerable": r.get("expected_chunk_id") is not None})
    return rows


def expected_pages(expected_chunk_id: Optional[str]) -> List[str]:
    """Gold chunk ids are page-level labels from another id scheme ('github_combined_p5#create_issue_v2025'); map them to the index's id prefix
    ('github_combined_reference:p5:'). A match is therefore PAGE-level: a coarse proxy for 'the right chunk was retrieved'."""
    out = []
    for part in (expected_chunk_id or "").split(";"):
        m = _CHUNK_RE.match(part.strip())
        if m:
            out.append(f"{m.group(1)}_reference:p{m.group(2)}:")
    return out


def grade(client, question: str, gold: str, answer: str) -> Dict[str, Any]:
    if not answer:
        return {"verdict": "FAIL", "raw": "no answer"}
    reply = client.chat.completions.create(
        model=GRADER_MODEL, temperature=0.0, max_tokens=300, reasoning_effort="low",
        messages=[{"role": "system", "content": "You are a strict, careful grading assistant."},
                  {"role": "user", "content": GRADE_PROMPT.format(question=question, golden_answer=gold, system_answer=answer)}])
    text = (reply.choices[0].message.content or "").strip()
    m = re.search(r"VERDICT:\s*(PASS|FAIL)", text, re.IGNORECASE)
    return {"verdict": m.group(1).upper() if m else "UNPARSED", "raw": text[:300],
            "tokens": reply.usage.prompt_tokens + reply.usage.completion_tokens}


def stage_ms(spans: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Inclusive time per stage for one request (the same stage names `trace show` prints); nested stages overlap, so do not add them up."""
    out: Dict[str, float] = {}
    for s in spans:
        d = s.get("duration_ms")
        if isinstance(d, (int, float)):
            key = s["name"] if s.get("service") == "sdk_engine" else f"server:{s['name']}"
            out[key] = round(out.get(key, 0.0) + d, 2)
    return out


def run(env: Env, rows: Sequence[Dict[str, Any]], out_path: Path, *, grade_pace_s: float = 6.0, between_s: Optional[float] = None,
        progress=print) -> List[Dict[str, Any]]:
    """`between_s` set: a LATENCY run. The model is NOT paced inside the call; whole requests are spaced `between_s` apart instead (to stay under the
    key's token limit), so each request's latency is its own. Unset: a quality run with the model paced inside the call (latency then includes waits)."""
    from openai import OpenAI

    engine = env.engine(llm=env.raw_llm() if between_s else env.live_llm(), tools=env.mcp())
    client = OpenAI(api_key=get_api_key(), base_url=env.cfg.llm_base_url, timeout=60.0, max_retries=0)
    results: List[Dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        explicit = row["version"] if row["version"] else None
        t0 = time.perf_counter()
        res = engine.ask(row["question"], explicit_version=explicit)
        asked_s = time.perf_counter() - t0
        spans = tracequery.load_trace(env.trace_dir, res.trace_id)
        calls = trajectory.extract_calls(spans)
        retrieved = res.retrieved_chunk_ids
        pages = expected_pages(row["expected_chunk_id"])
        graded = None
        if res.outcome in ("answered", "abstain", "degraded"):
            for attempt in range(2):
                try:
                    graded = grade(client, row["question"], row["gold_answer"], res.answer.split("\n\nCode check")[0])
                    break
                except Exception as exc:                                  # class name only; one retry after a rate limit
                    graded = {"verdict": "ERROR", "raw": type(exc).__name__}
                    if attempt == 0 and "RateLimit" in type(exc).__name__:
                        time.sleep(45)
                        continue
                    break
            time.sleep(grade_pace_s)
        allowed = list(res.version.comparison_versions) if res.version and res.version.is_comparison else [res.version.version] if res.version and res.version.version else []
        wrong = [c for c in retrieved if env.chunk_versions.get(c) not in allowed] if allowed else []
        out = {
            "id": row["id"], "level": row["level"], "question": row["question"], "answerable": row["answerable"], "explicit_version": explicit,
            "trace_id": res.trace_id, "outcome": res.outcome, "reason": (res.reason or "")[:200], "answer": res.answer,
            "version_status": res.version.status.value if res.version else None, "resolved": allowed, "comparison": bool(res.version and res.version.is_comparison),
            "retrieved": retrieved, "wrong_version_chunks": wrong,
            "gold_page_retrieved": (any(any(c.startswith(p) for c in retrieved) for p in pages) if pages else None),
            "judge": graded, "outcome_pass": (graded["verdict"] == "PASS") if graded and graded["verdict"] in ("PASS", "FAIL") else None,
            "llm_calls": res.usage.llm_calls, "prompt_tokens": res.usage.prompt_tokens, "completion_tokens": res.usage.completion_tokens,
            "cost_usd": res.usage.cost_usd, "cost_status": res.usage.cost_status, "latency_ms": res.latency_ms, "wall_s": round(time.perf_counter() - t0, 2),
            "citations": len(res.citations), "code_blocks": len(res.code_validation.blocks) if res.code_validation else 0,
            "non_ascii_date_in_answer": bool(_NON_ASCII_DATE.search(res.answer)),
            "deprecation_notes": len(res.deprecation_notes), "security_events": len(res.security), "stage_ms": stage_ms(spans),
            "latency_run": bool(between_s),
        }
        if row["id"] in trajectory.SPECS:
            out["score"] = trajectory.score(trajectory.SPECS[row["id"]], calls, outcome=res.outcome, outcome_pass=out["outcome_pass"])
        out["tool_sequence"] = [c.tool for c in calls]
        results.append(out)
        out_path.write_text(json.dumps(results, indent=1), encoding="utf-8")        # incremental: a crash keeps what was measured
        progress(f"[{i:>2}/{len(rows)}] {row['id']:10} {res.outcome:9} judge={graded['verdict'] if graded else '-':6} tools={out['tool_sequence']} {res.latency_ms:.0f}ms")
        if between_s:
            time.sleep(max(0.0, between_s - (time.perf_counter() - t0)))          # space whole requests apart (the judge call above counts toward the gap)
    return results
