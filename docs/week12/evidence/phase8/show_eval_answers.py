import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
want = set(sys.argv[2:])
outs = json.loads((run / "outputs.json").read_text(encoding="utf-8"))
res = {r["id"]: r for r in json.loads((run / "results.json").read_text(encoding="utf-8"))["results"]}
for o in outs:
    if o["case"] in want:
        print("=" * 30, o["id"], "| outcome:", o["outcome"], "| versions retrieved:", sorted({c["chunk_id"].split(":")[1][:4] for c in o["context"]}))
        print("Q:", o["question"])
        print("A:", o["answer"][:1500])
        print("checks:", [(c["name"][:50], c["passed"]) for c in res[o["case"]]["checks"]])
        print()
