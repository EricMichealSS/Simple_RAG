"""Reuse the project's existing, measured structure-aware chunker WITHOUT modifying it.

`chunkers.chunk_structured` has six direct callers across all four ingest flows (GitNexus impact: HIGH), so
it is not edited. Its module does `from config import CHUNK_SIZE, ...` and the legacy `config.py` raises at
import when GROK_API_KEY is unset, which would make everything that chunks a document unimportable in tests
and keyless environments.

Workaround, kept small and contained:
  1. the three size constants are read from the legacy `config.py` SOURCE with `ast` (no import, no key, one
     source of truth: if someone retunes them there, this follows);
  2. the UNMODIFIED `chunkers.py` is executed once under a stand-in `config` module, and `sys.modules` is
     restored immediately afterwards (guarded by a lock).
`tests/test_chunking.py` proves parity with the real legacy import (run in a subprocess with a placeholder key).
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import threading
import types
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import ENGINE_ROOT

_LEGACY_CONFIG = ENGINE_ROOT / "config.py"
_LEGACY_CHUNKERS = ENGINE_ROOT / "chunkers.py"
_NEEDED = ("CHUNK_SIZE", "CHUNK_OVERLAP", "STRUCTURED_CHUNK_SIZE")
_lock = threading.Lock()
MAX_CHUNK_CHARS = 3000  # hard cap per stored chunk, applied after the legacy chunker


def legacy_chunk_constants(path: Path = _LEGACY_CONFIG) -> Dict[str, int]:
    """Literal integer assignments from the legacy config, read without executing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: Dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in _NEEDED and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                found[name] = node.value.value
    missing = [n for n in _NEEDED if n not in found]
    if missing:
        raise RuntimeError(f"legacy config.py no longer defines literal ints for {missing}; update sdk_engine/chunking.py")
    return found


@lru_cache(maxsize=1)
def _legacy_module() -> types.ModuleType:
    consts = legacy_chunk_constants()
    stand_in = types.ModuleType("config")
    for k, v in consts.items():
        setattr(stand_in, k, v)
    spec = importlib.util.spec_from_file_location("sdk_engine._legacy_chunkers", _LEGACY_CHUNKERS)
    module = importlib.util.module_from_spec(spec)
    with _lock:
        had, previous = "config" in sys.modules, sys.modules.get("config")
        sys.modules["config"] = stand_in
        try:
            spec.loader.exec_module(module)  # the unmodified file; its `from config import ...` hits the stand-in
        finally:
            if had:
                sys.modules["config"] = previous
            else:
                sys.modules.pop("config", None)
    return module


def chunk_structured(text: str, *, normalize: bool) -> List[Dict[str, Any]]:
    """The legacy structure-aware chunker (tables and fenced code kept whole, header-led chunks), plus a hard
    per-chunk size cap so one giant paragraph can never become one giant stored chunk."""
    out: List[Dict[str, Any]] = []
    for ch in _legacy_module().chunk_structured(text, normalize=normalize):
        body = ch["text"]
        for i in range(0, max(len(body), 1), MAX_CHUNK_CHARS):
            piece = body[i:i + MAX_CHUNK_CHARS].strip()
            if piece:
                out.append({"text": piece, "section": ch.get("section")})
    return out


def anchor_for_section(section: Optional[str]) -> str:
    return _legacy_module().anchor_for_section(section)
