"""How does the trace query scale? SYNTHETIC logs (see make_synthetic_log.py) at three sizes; the real tracequery functions are timed.
`find` = scan_requests + select (parses only root spans). `naive` = what a query would cost if it parsed every line.
`show` = load_trace of one trace id (substring pre-filter). Warm file cache (best of 3); the first, cold read is slower."""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/docs/week12/evidence/phase7")
sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
from make_synthetic_log import build
from sdk_engine import tracequery as tq

BASE = Path(sys.argv[1])
rows = []
for n in (1_000, 10_000, 30_000):
    d = BASE / f"n{n}"
    shutil.rmtree(d, ignore_errors=True)
    t = time.perf_counter()
    build(d, n)
    gen_s = time.perf_counter() - t
    size = (d / "spans.jsonl").stat().st_size + (d / "server_spans.jsonl").stat().st_size
    lines = sum(1 for _ in open(d / "spans.jsonl", encoding="utf-8"))

    def best(fn):
        out = []
        for _ in range(3):
            t0 = time.perf_counter()
            r = fn()
            out.append(time.perf_counter() - t0)
        return min(out), r

    find_s, scan = best(lambda: tq.select(tq.scan_requests(d).requests, tq.Query(version="2025-06-01", code_tier="deprecated"))[0])
    naive_s, _ = best(lambda: [json.loads(line) for line in open(d / "spans.jsonl", encoding="utf-8")])
    one = tq.scan_requests(d).requests[n // 2].trace_id
    show_s, spans = best(lambda: tq.load_trace(d, one))
    rows.append(dict(requests=n, client_spans=lines, mb=round(size / 1e6, 1), generate_s=round(gen_s, 1), find_s=round(find_s, 3),
                     naive_parse_all_s=round(naive_s, 3), show_s=round(show_s, 3), show_spans=len(spans), matched=len(scan)))
    print(rows[-1], flush=True)
(BASE / "scale_results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
