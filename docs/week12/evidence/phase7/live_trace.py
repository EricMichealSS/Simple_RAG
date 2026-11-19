"""LIVE end-to-end trace for Phase 7: the documented CLI entry point -> LangGraph -> real MCP server -> real model.
The question deliberately contains an email address and a token-shaped value (both FAKE) to show what is and is not persisted.
Never prints the API key. Writes sample_trace.txt / sample_trace.json into the Phase 7 evidence folder."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
SCR = Path(r"C:/Users/ERICMI~1/AppData/Local/Temp/claude/C--Projects-AI-Learning-Simple-RAG/e3881073-bc5d-46d8-a353-4c8e72ce7215/scratchpad")
EV = Path(r"C:/Projects/AI Learning/Simple_RAG/docs/week12/evidence/phase7")
EV.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)
from sdk_engine.config import get_api_key
from sdk_engine.redaction import find_secrets

REAL_KEY = get_api_key() or ""
assert REAL_KEY, "no key"
trace_dir = SCR / "live_trace_dir"
shutil.rmtree(trace_dir, ignore_errors=True)
trace_dir.mkdir(parents=True)

env = {k: v for k, v in os.environ.items() if k not in ("GROK_API_KEY", "GROQ_API_KEY")}
env.update({"SDK_ENGINE_TRACE_DIR": str(trace_dir), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
FAKE_TOKEN = "ghp_" + "ExampleFakeTokenNotReal0123456789ab"
EMAIL = "jane.doe@example.com"
QUESTION = (f"My email is {EMAIL} and my token is {FAKE_TOKEN}. On API version 2022-11-28, how do I set the assignee "
            "when creating an issue? Show a curl example.")


def cli(*args, timeout=300):
    return subprocess.run([sys.executable, "-m", "sdk_engine", *args], cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


t0 = time.time()
q = cli("query", QUESTION, "--json")
print(f"query exit={q.returncode} wall={time.time() - t0:.1f}s")
res = json.loads(q.stdout)
trace_id = res["trace_id"]
print("outcome:", res["outcome"], "| trace_id:", trace_id, "| llm_calls:", res["usage"]["llm_calls"], "| cost:", res["usage"]["cost_usd"],
      "| retrieval_via:", res["retrieval_via"])

show = cli("trace", "--dir", str(trace_dir), "show", trace_id[:12])
(EV / "sample_trace.txt").write_text(show.stdout, encoding="utf-8")
showj = cli("trace", "--dir", str(trace_dir), "show", trace_id, "--json")
(EV / "sample_trace.json").write_text(showj.stdout, encoding="utf-8")
find = cli("trace", "--dir", str(trace_dir), "find")
print("\n--- trace find ---\n" + find.stdout)
print("--- trace show (first 40 lines) ---")
print("\n".join(show.stdout.splitlines()[:40]))

print("\n--- what was persisted (scan of EVERY file in the trace directory) ---")
print(f"{'file':32} {'bytes':>9} {'real key':>9} {'fake token':>11} {'email':>6} {'question text':>14} {'secret shapes':>14}")
for f in sorted(trace_dir.rglob("*")):
    if not f.is_file():
        continue
    text = f.read_text(encoding="utf-8", errors="replace")
    print(f"{f.name:32} {f.stat().st_size:>9,} {str(REAL_KEY in text):>9} {str(FAKE_TOKEN in text):>11} {str(EMAIL in text):>6} "
          f"{str('assignee when creating' in text):>14} {len(find_secrets(text)):>14}")
for name in ("sample_trace.txt", "sample_trace.json"):
    text = (EV / name).read_text(encoding="utf-8")
    print(f"evidence/{name}: real key present={REAL_KEY in text} fake token present={FAKE_TOKEN in text} email present={EMAIL in text} "
          f"secret shapes={len(find_secrets(text))}")

spans = json.loads(showj.stdout)["spans"]
v = next(s for s in spans if s["name"] == "validate_request")["attributes"]
print("\nvalidate_request span attributes:", json.dumps(v))
