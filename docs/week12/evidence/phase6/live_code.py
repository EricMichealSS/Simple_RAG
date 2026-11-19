"""LIVE Phase 6 run: real MCP server (check_symbol), real retrieval, real model (openai/gpt-oss-120b via Groq).
Questions are written to tempt wrong-version code. Prints findings and short excerpts only; never the key."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
SCR = Path(r"C:/Users/ERICMI~1/AppData/Local/Temp/claude/C--Projects-AI-Learning-Simple-RAG/e3881073-bc5d-46d8-a353-4c8e72ce7215/scratchpad")
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)
from sdk_engine.config import get_api_key
from sdk_engine.factory import build_engine

assert get_api_key(), "no key"
QUESTIONS = [
    ("gh22_issue_curl", "gh 2022-11-28", "On API version 2022-11-28, how do I create an issue with an assignee? Show a curl example."),
    ("gh22_two_assignees", "gh 2022-11-28 (tempts `assignees`)", "On API version 2022-11-28, create an issue assigned to two users. Show the JSON request body."),
    ("gh25_two_assignees", "gh 2025-06-01", "On API version 2025-06-01, create an issue assigned to two users. Show the JSON request body."),
    ("gh22_release_latest", "gh 2022-11-28 (tempts `make_latest`)", "On API version 2022-11-28, create a release and make it the latest release. Show the JSON request body."),
    ("gh25_release_latest", "gh 2025-06-01", "On API version 2025-06-01, create a release and make it the latest release. Show the JSON request body."),
    ("gh22_python", "gh 2022-11-28 python", "On API version 2022-11-28, show a Python example using the requests library that creates an issue with a label."),
    ("sw2_markdown", "swagger 2.x (tempts --generate-markdown)", "Using Swagger Codegen 2.x, show the command line to generate a java client plus markdown documentation."),
    ("sw3_markdown", "swagger 3.x", "Using Swagger Codegen 3.x, show the command line to generate a java client plus markdown documentation."),
    # pressure: the caller explicitly asks for something their version lacks
    ("gh22_explicit_assignees", "PRESSURE gh 2022-11-28 asks for `assignees`", "On API version 2022-11-28, create an issue and set assignees to [\"octocat\", \"hubot\"]. Show the JSON request body."),
    ("gh22_explicit_make_latest", "PRESSURE gh 2022-11-28 asks for make_latest", "On API version 2022-11-28, create a release with make_latest set to true. Show the JSON request body."),
    ("sw2_explicit_flag", "PRESSURE swagger 2.x asks for --generate-markdown", "Using Swagger Codegen 2.x, show the command that uses the --generate-markdown flag."),
]
eng = build_engine()
rows = []
try:
    for qid, label, q in QUESTIONS:
        for attempt in range(2):
            res = eng.ask(q)
            if not (res.outcome == "degraded" and "RateLimit" in (res.reason or "")):
                break
            time.sleep(45)
        cv = res.code_validation
        rows.append(dict(
            id=qid, label=label, version=res.version.version if res.version else None, outcome=res.outcome, reason=(res.reason or "")[:160],
            llm_calls=res.usage.llm_calls, cost_usd=res.usage.cost_usd, latency_ms=res.latency_ms, via=res.retrieval_via, trace_id=res.trace_id,
            code=None if cv is None else dict(
                symbol_source=cv.symbol_source, lookups=cv.lookups, hard=cv.hard, soft=cv.soft,
                blocks=[dict(language=b.language, syntax=b.syntax, findings=[(f.symbol, f.kind, f.tier, f.severity) for f in b.findings],
                             unsupported=b.unsupported_checks) for b in cv.blocks]),
            answer=res.answer))
        time.sleep(10)          # the key allows 8000 tokens/minute
finally:
    eng.close()

(SCR / "live_code_results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
for r in rows:
    print(f"\n=== {r['id']}  [{r['label']}]  version={r['version']} outcome={r['outcome']} llm_calls={r['llm_calls']} "
          f"cost=${r['cost_usd']} {r['latency_ms']:.0f}ms via={r['via']}")
    if r["reason"]:
        print("    reason:", r["reason"])
    if r["code"] is None:
        print("    (no code block in the answer)")
        continue
    c = r["code"]
    print(f"    code: source={c['symbol_source']} lookups={c['lookups']} hard={c['hard']} soft={c['soft']}")
    for b in c["blocks"]:
        print(f"    block {b['language']} syntax={b['syntax']}")
        for f in b["findings"]:
            print("       ", f)
print("\nTOTAL cost: $%.4f over %d questions" % (sum(r["cost_usd"] or 0 for r in rows), len(rows)))
