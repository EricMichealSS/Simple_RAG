"""A BOUNDED local load test of the engine's own path: version resolution, retrieval (real local embeddings + vector store), the real MCP server
subprocess, verification. The answer model is an instant SCRIPTED one, on purpose: it isolates what the engine itself costs under concurrency and
sends nothing to a provider. It therefore says NOTHING about provider latency, quotas or rate limits (measured separately from response headers and
projected in cost_by_stage.md / tenx.md), and it runs on one developer machine with one MCP server process.

Limits of the method, repeated in the report: one machine; one MCP subprocess shared by every thread; a fixed set of 36 answerable questions cycled;
threads (not processes), so Python's GIL applies; short runs (tens of requests per level), so p95 is a rough figure and p99 is not reported.
"""

from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Sequence, Tuple

from sdk_engine.tracequery import percentile


def run_level(ask: Callable[[str, Any], Any], questions: Sequence[Tuple[str, Any]], concurrency: int) -> Dict[str, Any]:
    """Fire `questions` through `ask(question, explicit_version)` with `concurrency` worker threads."""
    latencies: List[float] = []
    outcomes: Counter = Counter()
    errors: List[str] = []

    def one(item):
        q, version = item
        t0 = time.perf_counter()
        try:
            res = ask(q, version)
        except Exception as exc:                          # an exception under load is a finding, not something to hide
            return (time.perf_counter() - t0) * 1000, f"exception:{type(exc).__name__}", str(exc)[:120]
        return (time.perf_counter() - t0) * 1000, res.outcome, (res.reason or "")[:120]

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for ms, outcome, reason in pool.map(one, questions):
            latencies.append(ms)
            outcomes[outcome] += 1
            if outcome not in ("answered", "abstain", "clarify"):
                errors.append(f"{outcome}: {reason}")
    wall = time.perf_counter() - t_start
    n = len(questions)
    return {"concurrency": concurrency, "requests": n, "wall_s": round(wall, 2), "throughput_rps": round(n / wall, 2) if wall else None,
            "latency_ms": {"p50": round(percentile(latencies, 50), 1), "p95": round(percentile(latencies, 95), 1), "max": round(max(latencies), 1)},
            "outcomes": dict(outcomes), "failures": len(errors), "failure_samples": sorted(set(errors))[:3]}


def run(engine, questions: Sequence[Tuple[str, Any]], levels: Sequence[int], progress=print) -> List[Dict[str, Any]]:
    rows = []
    for c in levels:
        row = run_level(lambda q, v: engine.ask(q, explicit_version=v), questions, c)
        rows.append(row)
        progress(f"concurrency {c:>2}: {row['requests']} requests in {row['wall_s']:>6.2f}s = {row['throughput_rps']} req/s | p50 {row['latency_ms']['p50']} ms "
                 f"p95 {row['latency_ms']['p95']} ms max {row['latency_ms']['max']} ms | {row['outcomes']}" + (f" | FAILURES {row['failure_samples']}" if row['failures'] else ""))
    return rows
