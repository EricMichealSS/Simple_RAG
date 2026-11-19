"""One trace id per request, spans with parent ids, redacted JSONL persistence.

Why not OpenTelemetry/Phoenix/Langfuse: see docs/week12/tool_selection.md - a plain JSONL sink
carries every field the brief requires, adds no service, and keeps the redaction step in our hands.

Propagation across process boundaries (the MCP hop) uses a tiny carrier dict
``{"trace_id", "parent_span_id", "request_id"}`` produced by :meth:`TraceContext.carrier` and
consumed by :meth:`TraceContext.from_carrier`, so the server-side spans join the SAME trace.

Everything persisted passes through :func:`redaction.redact_obj` first and long strings are
truncated, so a span can never carry a secret or an unbounded prompt into the log.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .redaction import redact_obj, truncate

MAX_ATTR_CHARS = 2000

_current_span: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("sdk_engine_current_span", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonlSink:
    """Append-only, thread-safe JSONL writer. One span per line, already redacted."""

    def __init__(self, directory: Path, filename: str = "spans.jsonl"):
        self.path = Path(directory) / filename
        self._lock = threading.Lock()

    def write(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


class SpanHandle:
    """Mutable view of an in-flight span: set attributes / status, then the context manager closes it."""

    def __init__(self, record: Dict[str, Any]):
        self._record = record

    @property
    def span_id(self) -> str:
        return self._record["span_id"]

    def set(self, **attrs: Any) -> None:
        self._record["attributes"].update(attrs)

    def set_status(self, status: str, error_category: Optional[str] = None) -> None:
        self._record["status"] = status
        if error_category:
            self._record["error_category"] = error_category


class TraceContext:
    """Per-request tracing state. Cheap to create; safe to pass through a graph state."""

    def __init__(
        self,
        trace_id: Optional[str] = None,
        *,
        sink: Optional[JsonlSink] = None,
        request_id: Optional[str] = None,
        remote_parent_span_id: Optional[str] = None,
        service: str = "sdk_engine",
    ):
        self.trace_id = trace_id or new_trace_id()
        self.request_id = request_id or uuid.uuid4().hex[:12]
        self.sink = sink
        self.service = service
        self._remote_parent = remote_parent_span_id
        self.spans: List[Dict[str, Any]] = []

    # -- propagation ---------------------------------------------------------------------------
    def carrier(self) -> Dict[str, Optional[str]]:
        """Values to send across a process boundary (e.g. as MCP request metadata)."""
        return {
            "trace_id": self.trace_id,
            "parent_span_id": _current_span.get() or self._remote_parent,
            "request_id": self.request_id,
        }

    @classmethod
    def from_carrier(
        cls, carrier: Optional[Dict[str, Any]], *, sink: Optional[JsonlSink] = None, service: str = "sdk_engine"
    ) -> "TraceContext":
        """Continue a remote trace. A missing/invalid carrier starts a fresh trace (never raises)."""
        carrier = carrier or {}
        tid = carrier.get("trace_id")
        valid = isinstance(tid, str) and len(tid) == 32 and all(c in "0123456789abcdef" for c in tid)
        return cls(
            trace_id=tid if valid else None,
            sink=sink,
            request_id=carrier.get("request_id") if isinstance(carrier.get("request_id"), str) else None,
            remote_parent_span_id=carrier.get("parent_span_id") if valid and isinstance(carrier.get("parent_span_id"), str) else None,
            service=service,
        )

    # -- spans ---------------------------------------------------------------------------------
    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[SpanHandle]:
        parent = _current_span.get() or self._remote_parent
        span_id = new_span_id()
        record: Dict[str, Any] = {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "span_id": span_id,
            "parent_span_id": parent,
            "service": self.service,
            "name": name,
            "start_ts": _now_iso(),
            "end_ts": None,
            "duration_ms": None,
            "status": "ok",
            "error_category": None,
            "attributes": dict(attrs),
        }
        token = _current_span.set(span_id)
        t0 = time.perf_counter()
        handle = SpanHandle(record)
        try:
            yield handle
        except BaseException as exc:  # record then re-raise: tracing must never swallow errors
            record["status"] = "error"
            record["error_category"] = record.get("error_category") or type(exc).__name__
            record["attributes"]["error"] = str(exc)
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            record["end_ts"] = _now_iso()
            _current_span.reset(token)
            self._emit(record)

    def _emit(self, record: Dict[str, Any]) -> None:
        safe = redact_obj(record)
        attrs = safe.get("attributes", {})
        for k, v in list(attrs.items()):
            if isinstance(v, str):
                attrs[k] = truncate(v, MAX_ATTR_CHARS)
        self.spans.append(safe)
        if self.sink is not None:
            try:
                self.sink.write(safe)
            except OSError:
                # A full disk or unwritable log directory must not break answering a question.
                pass


def default_sink(trace_dir: Optional[Path] = None) -> JsonlSink:
    from .config import EngineConfig

    directory = Path(trace_dir) if trace_dir else EngineConfig.from_env().trace_dir
    return JsonlSink(directory)
