"""MCP client with runtime tool discovery, an operator allowlist, schema-validated calls, timeouts,
output caps and trace propagation. Synchronous facade over the async MCP SDK.

Threading model: the SDK's stdio transport uses anyio task groups that must be entered and exited in the
SAME task, so every MCP interaction runs inside ONE long-lived task on a dedicated event-loop thread
(`_runner`). Callers (the synchronous LangGraph nodes) enqueue requests and wait on a Future.

Security properties (tested):
* Only servers the operator configured are started; the subprocess receives ONLY the env in
  `ServerConfig.env` plus a minimal safe default - never the parent's secrets (e.g. GROK_API_KEY).
* `tools/list` runs at start-up; discovery is not authorization: a tool is callable only if it is in the
  server's `allowed_tools` (see tool_policy.build_registry).
* Calls are validated against the DISCOVERED JSON Schema before leaving the process (tool_policy), run
  under a timeout, and their output is size-capped. Tool errors/outputs are untrusted data.
* The trace context goes in request `_meta` (protocol-native), so server spans join the same trace.
* Error messages carry the exception class or a short redacted string, never request content.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
import time
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel

from .config import ENGINE_ROOT, EngineConfig
from .redaction import redact_text
from .tool_policy import ServerConfig, ToolSpec, build_registry
from .trace import JsonlSink, TraceContext

DEFAULT_TOOLS = ("search_docs", "lookup_changes", "check_symbol")


class McpUnavailable(RuntimeError):
    """A configured server could not be started or its tools could not be listed."""


class ToolResult(BaseModel):
    ok: bool
    tool: str
    server: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    truncated: bool = False
    trace_id: Optional[str] = None


class ToolGateway(Protocol):
    def tools(self) -> List[ToolSpec]: ...

    def call(self, name: str, arguments: Mapping[str, Any], *, trace: Optional[TraceContext] = None,
             timeout_s: Optional[float] = None) -> ToolResult: ...


def default_server_config(cfg: EngineConfig, *, name: str = "sdk-docs") -> ServerConfig:
    """Our own server, launched with the current interpreter (not a hard-coded `python3`)."""
    env = {
        "SDK_ENGINE_CHROMA_PATH": str(cfg.chroma_path),
        "SDK_ENGINE_COLLECTION": cfg.official_collection,
        "SDK_ENGINE_TRACE_DIR": str(cfg.trace_dir),
        "SDK_ENGINE_PAGE_METADATA": str(cfg.page_metadata_path),
        "SDK_ENGINE_EMBEDDING_MODEL": cfg.embedding_model,
        "SDK_ENGINE_HYBRID": "1" if cfg.hybrid_retrieval else "0",
        "SDK_ENGINE_MAX_TOOL_OUTPUT_CHARS": str(cfg.max_tool_output_chars),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if cfg.embedding_model != "hash-test":
        env["HF_HUB_OFFLINE"] = "1"  # the tool server needs no network; the model is cached by the indexing step
    return ServerConfig(name=name, command=sys.executable, args=["-m", "sdk_engine.mcp_server"], env=env,
                        cwd=str(ENGINE_ROOT), allowed_tools=DEFAULT_TOOLS)


class McpToolClient:
    def __init__(
        self,
        servers: Sequence[ServerConfig],
        *,
        startup_timeout_s: float = 90.0,
        call_timeout_s: float = 20.0,
        max_output_chars: int = 20000,
        log_dir: Optional[Path] = None,
        sink: Optional[JsonlSink] = None,
    ):
        self._servers = list(servers)
        self._startup_timeout = startup_timeout_s
        self._call_timeout = call_timeout_s
        self._max_output = max_output_chars
        self._log_dir = Path(log_dir) if log_dir else None
        self._sink = sink
        self._registry: List[ToolSpec] = []
        self._sessions: Dict[str, ClientSession] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._startup_error: Optional[BaseException] = None
        self._closed = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "McpToolClient":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._thread_main, name="mcp-client", daemon=True)
        self._thread.start()
        if not self._ready.wait(self._startup_timeout * max(1, len(self._servers)) + 10):
            raise McpUnavailable("timed out waiting for MCP servers to start")
        if self._startup_error is not None:
            err = self._startup_error
            self.close()
            raise err if isinstance(err, McpUnavailable) else McpUnavailable(f"MCP start-up failed: {type(err).__name__}")
        return self

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._runner())
        finally:
            self._loop.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._loop is not None and self._queue is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=15)

    def __enter__(self) -> "McpToolClient":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ the single long-lived task
    async def _runner(self) -> None:
        self._queue = asyncio.Queue()
        try:
            async with AsyncExitStack() as stack:
                for s in self._servers:
                    errlog = None
                    if self._log_dir is not None:
                        self._log_dir.mkdir(parents=True, exist_ok=True)
                        errlog = stack.enter_context(open(self._log_dir / f"mcp_{s.name}_stderr.log", "a", encoding="utf-8"))
                    else:
                        errlog = stack.enter_context(open(os.devnull, "w"))
                    params = StdioServerParameters(command=s.command, args=s.args, env=dict(s.env), cwd=s.cwd)
                    try:
                        read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
                        session = await stack.enter_async_context(ClientSession(read, write))
                        await asyncio.wait_for(session.initialize(), self._startup_timeout)
                        listed = await asyncio.wait_for(session.list_tools(), self._startup_timeout)
                    except Exception as exc:
                        raise McpUnavailable(f"MCP server {s.name!r} failed to start or list tools: {type(exc).__name__}") from exc
                    self._sessions[s.name] = session
                    specs = build_registry(s, listed.tools)
                    taken = {t.name for t in self._registry if t.authorized}
                    for t in specs:  # a tool name offered by two servers is ambiguous: the later one is not callable
                        if t.authorized and t.name in taken:
                            t.authorized, t.rejected_reason = False, "tool name already provided by another server"
                    self._registry.extend(specs)
                self._ready.set()
                while True:
                    item = await self._queue.get()
                    if item is None:
                        break
                    asyncio.create_task(self._handle(*item))
        except BaseException as exc:
            if not self._ready.is_set():
                self._startup_error = exc
                self._ready.set()

    async def _handle(self, name: str, arguments: Dict[str, Any], meta: Dict[str, Any], timeout: float,
                      fut: "concurrent.futures.Future[ToolResult]") -> None:
        try:
            res = await self._do_call(name, arguments, meta, timeout)
        except BaseException as exc:  # never leave a caller waiting
            res = ToolResult(ok=False, tool=name, error_category="internal_error", error_message=type(exc).__name__)
        if not fut.done():
            fut.set_result(res)

    async def _do_call(self, name: str, arguments: Dict[str, Any], meta: Dict[str, Any], timeout: float) -> ToolResult:
        spec = next((t for t in self._registry if t.name == name and t.authorized), None)
        if spec is None:
            return ToolResult(ok=False, tool=name, error_category="unauthorized_tool", error_message="tool is not authorized")
        session = self._sessions[spec.server]
        t0 = time.perf_counter()

        def done(**kw: Any) -> ToolResult:
            return ToolResult(tool=name, server=spec.server, latency_ms=round((time.perf_counter() - t0) * 1000, 2), **kw)

        try:
            r = await asyncio.wait_for(
                session.call_tool(name, arguments=arguments, meta=meta, read_timeout_seconds=timedelta(seconds=timeout)), timeout)
        except asyncio.TimeoutError:
            return done(ok=False, error_category="timeout", error_message=f"no result within {timeout:g}s")
        except Exception as exc:
            return done(ok=False, error_category="transport_error", error_message=type(exc).__name__)

        texts = [c.text for c in r.content if getattr(c, "type", None) == "text"]
        if r.isError:  # validation failures and server exceptions arrive here; treat the text as untrusted
            return done(ok=False, error_category="tool_error", error_message=redact_text(" ".join(texts))[:300])
        data: Any = getattr(r, "structuredContent", None)
        if data is None and texts:
            try:
                data = json.loads(texts[0])
            except ValueError:
                return done(ok=False, error_category="malformed_result", error_message="result was not JSON")
        if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], dict):
            data = data["result"]
        if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
            # Contract: every tool returns a JSON object with a boolean `ok`. Anything else (prose that FastMCP wrapped
            # as {"result": "..."}, arrays, objects without `ok`) is malformed and is never handed to the graph.
            return done(ok=False, error_category="malformed_result", error_message="result did not follow the tool result contract")
        if len(json.dumps(data, default=str)) > self._max_output:
            return done(ok=False, error_category="output_too_large", error_message=f"result exceeds {self._max_output} characters",
                        truncated=True)
        if data.get("ok") is False:
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            return done(ok=False, data=data, error_category=str(err.get("category", "tool_error"))[:64],
                        error_message=redact_text(str(err.get("message", "")))[:300], trace_id=str(data.get("trace_id", ""))[:64] or None)
        return done(ok=True, data=data, truncated=bool(data.get("truncated")), trace_id=str(data.get("trace_id", ""))[:64] or None)

    # ------------------------------------------------------------------ public, synchronous API
    def tools(self) -> List[ToolSpec]:
        return list(self._registry)

    def call(self, name: str, arguments: Mapping[str, Any], *, trace: Optional[TraceContext] = None,
             timeout_s: Optional[float] = None) -> ToolResult:
        if self._closed or self._loop is None or self._queue is None:
            return ToolResult(ok=False, tool=name, error_category="client_closed", error_message="MCP client is not running")
        timeout = timeout_s if timeout_s is not None else self._call_timeout
        trace = trace or TraceContext(sink=self._sink)
        args = dict(arguments)
        with trace.span("mcp.call", tool=name, arg_keys=sorted(args), arg_chars=len(json.dumps(args, default=str))) as sp:
            fut: "concurrent.futures.Future[ToolResult]" = concurrent.futures.Future()
            # carrier() is read INSIDE the span so the server's spans hang under this mcp.call span
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (name, args, trace.carrier(), timeout, fut))
            try:
                res = fut.result(timeout + 5)
            except concurrent.futures.TimeoutError:
                res = ToolResult(ok=False, tool=name, error_category="timeout", error_message=f"no result within {timeout:g}s")
            sp.set(server=res.server, ok=res.ok, latency_ms=res.latency_ms, truncated=res.truncated,
                   error_category=res.error_category, response_trace_id=res.trace_id)
            if not res.ok:
                sp.set_status("error", res.error_category)
        return res
