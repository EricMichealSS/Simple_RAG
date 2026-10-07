"""Metering harness: every model call in the squad goes through `metered_chat`,
which records one JSON line (trace_id, arm, caller, hand-off label, prompt and
completion tokens, latency, status). Handoffs.log, the race table and the
multiplier are all generated from these lines, so they cannot disagree.

Provider hiccups (429 / connection errors) are retried here and counted in
`retries`; they are infrastructure noise, not a property of the pattern.
"""

import json
import re
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from openai import OpenAI, RateLimitError, APIConnectionError, APITimeoutError, InternalServerError  # noqa: E402

from config import GROK_API_KEY  # noqa: E402
from pricing import MODEL, cost_usd  # noqa: E402

_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=60.0, max_retries=0)
_TRANSIENT = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
MAX_PROVIDER_RETRIES = 4


_WAITED = [0.0]  # seconds spent sleeping on provider rate limits (not pattern cost)
MAX_SINGLE_WAIT_S = 2400  # a longer wait means the DAILY quota is gone for hours: stop, do not hang


class QuotaExhausted(Exception):
    pass


def active_now():
    """Wall clock minus time spent waiting on rate limits. All latencies use this clock."""
    return time.perf_counter() - _WAITED[0]


def _wait_seconds(err):
    """Parse Groq's 'try again in 1m5.6s' / '8m58.2s' / '75ms' hint into seconds (+1s margin)."""
    m = re.search(r"try again in ((?:\d+h)?(?:\d+m(?!s))?(?:[\d.]+s)?(?:[\d.]+ms)?)", str(err))
    if not m or not m.group(1):
        return None
    txt = m.group(1)
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)(ms|h|m|s)", txt):
        total += float(num) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total + 1.0


def call_with_retry(fn, *args, **kwargs):
    """Run fn, retrying provider hiccups. Rate-limit sleeps are recorded in _WAITED so they are
    excluded from latency. Returns (result, retries)."""
    retries = 0
    while True:
        try:
            return fn(*args, **kwargs), retries
        except _TRANSIENT as e:
            retries += 1
            if retries > MAX_PROVIDER_RETRIES * 4:
                raise
            w = _wait_seconds(e) if isinstance(e, RateLimitError) else None
            if w is not None and w > MAX_SINGLE_WAIT_S:
                raise QuotaExhausted(str(e)[:300])
            w = w if w is not None else min(2 ** retries, 15)
            time.sleep(w)
            _WAITED[0] += w


class Tracer:
    """One per (arm, question, repeat) run."""

    def __init__(self, arm, race_id, repeat, sink=None):
        self.trace_id = uuid.uuid4().hex[:12]
        self.arm, self.race_id, self.repeat = arm, race_id, repeat
        self.sink = Path(sink) if sink else None
        self.calls = []
        self.start = active_now()

    def record(self, caller, handoff, prompt_tokens, completion_tokens, latency_s, status="ok", retries=0):
        row = {
            "trace_id": self.trace_id, "arm": self.arm, "race_id": self.race_id, "repeat": self.repeat,
            "seq": len(self.calls) + 1, "caller": caller, "handoff": handoff,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": round(cost_usd(MODEL, prompt_tokens, completion_tokens), 8),
            "latency_s": None if latency_s is None else round(latency_s, 3),
            "status": status, "provider_retries": retries,
        }
        self.calls.append(row)
        if self.sink:
            with open(self.sink, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        return row

    def totals(self):
        return {
            "total_tokens": sum(c["total_tokens"] for c in self.calls),
            "total_cost_usd": round(sum(c["cost_usd"] for c in self.calls), 6),
            "model_calls": len(self.calls),
        }

    def elapsed(self):
        return active_now() - self.start


def metered_chat(tracer, caller, handoff, messages, tools=None):
    """One model call, recorded. Same sampling settings in every role."""
    kwargs = dict(model=MODEL, messages=messages, temperature=0.0, max_tokens=1024)
    if tools:
        kwargs.update(tools=tools, tool_choice="auto")
    t0 = active_now()
    try:
        resp, retries = call_with_retry(_client.chat.completions.create, **kwargs)
    except Exception as e:
        tracer.record(caller, handoff, 0, 0, active_now() - t0, status=f"provider_error:{type(e).__name__}")
        raise
    u = resp.usage
    tracer.record(caller, handoff, u.prompt_tokens, u.completion_tokens, active_now() - t0, retries=retries)
    return resp


def trace_from_single(result, race_id, repeat, sink=None):
    """Build a Tracer for the single agent from the log run_agent already returns.
    agent.py is not modified, so per-call latency is unknown (None); tokens are exact."""
    t = Tracer("single", race_id, repeat, sink=sink)
    for e in result["log"]:
        if e["event"] == "llm_call":
            t.record("single_agent", f"single: lap {e['iteration']}", e["prompt_tokens"], e["completion_tokens"], None)
    return t
