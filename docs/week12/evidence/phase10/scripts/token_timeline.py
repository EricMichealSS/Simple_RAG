"""From the latency run's spans: tokens used per 60-second sliding window of model calls on the answer model, and when the first RateLimitError happened."""
import json
import sys
from datetime import datetime
from pathlib import Path

d = Path(sys.argv[1])
rows = json.loads((d / "golden_results.json").read_text(encoding="utf-8"))
tid = {r["trace_id"]: r["id"] for r in rows}
spans = [json.loads(line) for line in (d / "traces" / "spans.jsonl").read_text(encoding="utf-8").splitlines()]


def ts(s):
    return datetime.fromisoformat(s["start_ts"]).timestamp()


gens = sorted((s for s in spans if s["name"] == "generate" and s["trace_id"] in tid), key=ts)
t0 = ts(gens[0])
events = []
for s in gens:
    a = s["attributes"]
    used = (a.get("prompt_tokens") or 0) + (a.get("completion_tokens") or 0)
    events.append((round(ts(s) - t0, 1), tid[s["trace_id"]], used, "FAILED(429)" if s.get("status") == "error" or a.get("error") else "ok"))
print("t_s    question     tokens  result   tokens in the previous 60 s (answer model only)")
for i, (t, q, used, res) in enumerate(events):
    window = sum(u for (tt, _, u, r) in events[:i] if t - tt < 60)
    print(f"{t:6.1f} {q:11} {used:6}  {res:11} {window}")
