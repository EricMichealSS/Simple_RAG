"""Working set of the engine process and its MCP server process after warm-up and a few queries (no model call)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, "C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine.factory import build_engine  # noqa: E402


def ps(filter_cmd):
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '" + filter_cmd + "' } | "
           "Select-Object ProcessId, @{n='MB';e={[math]::Round($_.WorkingSetSize/1MB)}}, @{n='cmd';e={$_.CommandLine.Substring(0, [math]::Min(90, $_.CommandLine.Length))}} | ConvertTo-Json -Compress")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
    return r.stdout.strip()


def mb(pid):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", f"[math]::Round((Get-Process -Id {pid}).WorkingSet64/1MB)"], capture_output=True, text=True)
    return r.stdout.strip()


out = {"engine_pid": os.getpid()}
engine = build_engine(use_llm=False)
with engine:
    out["after_start_engine_MB"] = mb(os.getpid())
    for q in ("What is the default mode for the markdown endpoint in GitHub API 2025-06-01?", "How do I create an issue in 2022-11-28?",
              "Which codegen flag generates markdown docs in Swagger Codegen 3.x?"):
        engine.ask(q)
    out["after_3_queries_engine_MB"] = mb(os.getpid())
    out["mcp_server_process"] = json.loads(ps("sdk_engine.mcp_server") or "null")
print(json.dumps(out, indent=1))
