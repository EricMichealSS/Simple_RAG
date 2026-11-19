"""A minimal MCP client written against the PROTOCOL, not the `mcp` SDK (stdlib only).

MCP over stdio is newline-delimited JSON-RPC 2.0. This client does `initialize` ->
`notifications/initialized` -> `tools/list` / `tools/call`. Because it shares no code with the SDK that
built the server, a green test here means the server is reachable by an independent implementation.

Not "external fresh-eyes verification": the same author wrote both. It is an independent *client*.

Run by hand against the real index:
    python tests/raw_mcp_client.py            (from developer-rag/; prints tool names and one search)
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2025-03-26"


def minimal_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Only what Python needs to start. In particular NOT the caller's secrets."""
    keep = ("SYSTEMROOT", "PATH", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "LANG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra or {})
    return env


class RawMcpClient:
    def __init__(self, argv: List[str], env: Dict[str, str], cwd: str, stderr_path: Path):
        self._stderr = open(stderr_path, "wb")
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, env=env, cwd=cwd)
        self._q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._next_id = 0
        self.unparsed: List[str] = []
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        for line in iter(self.proc.stdout.readline, b""):
            try:
                self._q.put(json.loads(line.decode("utf-8")))
            except ValueError:
                self.unparsed.append(line[:200].decode("utf-8", "replace"))  # stdout must carry ONLY protocol frames
        self._q.put(None)

    def _send(self, obj: Dict[str, Any]) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 120.0) -> Dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            try:
                msg = self._q.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError(f"no response to {method} within {timeout}s")
            if msg is None:
                raise EOFError("server closed stdout")
            if msg.get("id") == rid:
                return msg

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> Dict[str, Any]:
        res = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                          "clientInfo": {"name": "raw-test-client", "version": "0"}})
        self.notify("notifications/initialized")
        return res

    def list_tools(self) -> List[Dict[str, Any]]:
        return self.request("tools/list")["result"]["tools"]

    def call_tool(self, name: str, arguments: Dict[str, Any], meta: Optional[Dict[str, Any]] = None, timeout: float = 120.0) -> Dict[str, Any]:
        params: Dict[str, Any] = {"name": name, "arguments": arguments}
        if meta is not None:
            params["_meta"] = meta
        return self.request("tools/call", params, timeout=timeout)["result"]

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        finally:
            self._stderr.close()


def tool_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """The JSON object a tool returned (structuredContent if present, else the first text block)."""
    sc = result.get("structuredContent")
    if isinstance(sc, dict):
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(result["content"][0]["text"])


if __name__ == "__main__":  # manual smoke test against the real index
    root = Path(__file__).resolve().parents[1]
    c = RawMcpClient([sys.executable, "-m", "sdk_engine.mcp_server"], minimal_env(), str(root), root / "data" / "raw_client_stderr.log")
    try:
        print("server:", c.initialize()["result"]["serverInfo"])
        print("tools:", [t["name"] for t in c.list_tools()])
        out = tool_payload(c.call_tool("search_docs", {"query": "create an issue assignee", "version": "2022-11-28", "top_k": 2}))
        print([(p["chunk_id"], p["api_version"]) for p in out["passages"]])
    finally:
        c.close()
