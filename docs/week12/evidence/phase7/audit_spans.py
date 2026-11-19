"""Read-only audit of the real trace files my live runs wrote: which spans/attributes exist, per the Phase 7 field list."""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine.config import EngineConfig

d = EngineConfig.from_env().trace_dir
print("trace dir:", d)
for f in sorted(d.glob("*")):
    print(f"  {f.name}: {f.stat().st_size:,} bytes")


def load(name):
    p = d / name
    rows, bad = [], 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
    return rows, bad


client, bad1 = load("spans.jsonl")
server, bad2 = load("server_spans.jsonl")
traces = defaultdict(list)
for s in client + server:
    traces[s["trace_id"]].append(s)
print(f"client spans={len(client)} server spans={len(server)} traces={len(traces)} unreadable lines={bad1 + bad2}")
print("span names:", dict(Counter(s["name"] for s in client + server).most_common()))
print("services:", dict(Counter(s["service"] for s in client + server)))
print("time range:", min(s["start_ts"] for s in client), "->", max(s["start_ts"] for s in client))

# the newest trace that has a generate span AND server spans
pick = None
for tid, spans in traces.items():
    names = {s["name"] for s in spans}
    if "generate" in names and any(s["service"] == "mcp_server" for s in spans):
        pick = tid
print("\nnewest full trace:", pick)
spans = sorted(traces[pick], key=lambda s: s["start_ts"])
by_id = {s["span_id"]: s for s in spans}


def depth(s):
    n, p = 0, s.get("parent_span_id")
    while p and p in by_id:
        n, p = n + 1, by_id[p].get("parent_span_id")
    return n


for s in spans:
    a = s["attributes"]
    keep = {k: a[k] for k in list(a)[:9]}
    print(f"{'  ' * depth(s)}{s['name']} [{s['service']}] {s['duration_ms']}ms {s['status']} "
          f"{json.dumps(keep, default=str)[:230]}")
