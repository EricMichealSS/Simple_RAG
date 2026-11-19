"""Index integrity check (read-only) and a guarded rebuild of the official collection.

The version filter is only as good as the version tag on each chunk, so the check compares what the vector store
holds against the hand-authored ``agent_race/page_metadata.json`` (the ground truth the legacy ingest also reads):

* every indexed chunk carries an ``api_version``, and it equals the version the metadata gives for that page;
* every page the metadata lists has at least one chunk (a page with no extractable text is legitimately absent: it is
  reported as missing, not called a defect);
* nothing is indexed that the metadata does not know about.

The rebuild does NOT re-implement ingestion. It runs the existing ``agent_race/ingest.py`` (which deletes and
re-creates the collection), and only when the engine is configured for the one location that script writes to.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import ENGINE_ROOT, ENV_CHROMA_PATH, ENV_COLLECTION, ENV_EMBEDDING_MODEL, EngineConfig

LEGACY_INGEST = ENGINE_ROOT / "agent_race" / "ingest.py"
LEGACY_CHROMA_PATH = ENGINE_ROOT / "data" / "chroma"      # hard-coded in the ingest script
LEGACY_COLLECTION = "race_docs"                           # likewise


class RebuildRefused(RuntimeError):
    """The engine is configured for a location the legacy ingest script does not write to."""


class RebuildFailed(RuntimeError):
    """The ingest script exited non-zero."""


def expected_pages(page_metadata_path: Path) -> Dict[Tuple[str, int], str]:
    """{(source file stem, page number): api_version} from the hand-authored page metadata."""
    raw = json.loads(Path(page_metadata_path).read_text(encoding="utf-8"))
    out: Dict[Tuple[str, int], str] = {}
    for filename, pages in raw.items():
        if not filename.endswith(".pdf") or not isinstance(pages, dict):
            continue                                                          # `_comment`, `known_endpoints`
        for page, meta in pages.items():
            out[(Path(filename).stem, int(page))] = meta["api_version"]
    return out


def check_metadatas(expected: Mapping[Tuple[str, int], str], metadatas: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_version: Counter = Counter()
    indexed_pages: Dict[Tuple[str, int], set] = {}
    untagged = 0
    for m in metadatas:
        source, page, version = Path(str(m.get("source_file", ""))).stem, m.get("page"), m.get("api_version")
        if not version:
            untagged += 1
        else:
            by_version[version] += 1
        indexed_pages.setdefault((source, page), set()).add(version)
    mismatches: List[Dict[str, Any]] = []
    unexpected: List[Dict[str, Any]] = []
    for (source, page), versions in sorted(indexed_pages.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        want = expected.get((source, page))
        if want is None:
            unexpected.append({"source": source, "page": page})
            continue
        for v in sorted(x for x in versions if x and x != want):
            mismatches.append({"source": source, "page": page, "indexed_as": v, "expected": want})
    missing = [{"source": s, "page": p} for (s, p) in sorted(expected) if (s, p) not in indexed_pages]
    problems: List[str] = []
    if not metadatas:
        problems.append("the collection is empty")
    if mismatches:
        problems.append(f"{len(mismatches)} page(s) carry a version that differs from page_metadata.json (the version filter would be wrong for them)")
    if untagged:
        problems.append(f"{untagged} chunk(s) have no api_version (the version filter cannot place them)")
    if unexpected:
        problems.append(f"{len(unexpected)} indexed page(s) are not listed in page_metadata.json")
    if missing and metadatas:
        problems.append(f"{len(missing)} page(s) listed in page_metadata.json have no chunk (blank pages are skipped by the ingest; otherwise re-index)")
    return {"ok": not problems, "chunks": len(metadatas), "by_version": dict(sorted(by_version.items())), "pages_expected": len(expected),
            "pages_indexed": len(indexed_pages), "version_mismatches": mismatches, "untagged_chunks": untagged, "missing_pages": missing,
            "unexpected_pages": unexpected, "problems": problems}


def check_index(cfg: EngineConfig) -> Dict[str, Any]:
    """Read-only. Raises IndexMissing (from retrieval.open_collection) when there is no collection to check."""
    from .retrieval import open_collection

    got = open_collection(cfg).get(include=["metadatas"])
    return check_metadatas(expected_pages(cfg.page_metadata_path), got.get("metadatas") or [])


LEGACY_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"          # hard-coded in the ingest script
_PLACEHOLDER_KEY = "placeholder-not-a-key"


def _legacy_ingest_env(environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """The ingest script imports the legacy `chunkers`, which imports the legacy `config.py`, which raises at import when `GROK_API_KEY` is unset
    (found by review: a stranger with only `GROQ_API_KEY`, or no key, got "ingest exited with status 1"). Ingestion never calls a model, so the child
    process gets a placeholder when no key is set; a real key is passed through untouched and is never printed."""
    env = dict(os.environ if environ is None else environ)
    if not env.get("GROK_API_KEY", "").strip():
        env["GROK_API_KEY"] = env.get("GROQ_API_KEY", "").strip() or _PLACEHOLDER_KEY
    return env


def _run_legacy_ingest() -> int:
    # A child process: the ingest script loads the embedding model and builds the collection at import time.
    return subprocess.run([sys.executable, str(LEGACY_INGEST)], cwd=str(ENGINE_ROOT), env=_legacy_ingest_env()).returncode


def rebuild(cfg: EngineConfig, runner: Optional[Callable[[], int]] = None) -> None:
    """Delete and re-create the official collection by running the legacy ingest. Refuses any other location."""
    if Path(cfg.chroma_path).resolve() != LEGACY_CHROMA_PATH.resolve():
        raise RebuildRefused(f"the ingest script writes to {LEGACY_CHROMA_PATH}, but {ENV_CHROMA_PATH} points to {cfg.chroma_path}; "
                             f"unset {ENV_CHROMA_PATH} (or build the index there yourself) rather than rebuild the wrong store")
    if cfg.official_collection != LEGACY_COLLECTION:
        raise RebuildRefused(f"the ingest script writes the collection {LEGACY_COLLECTION!r}, but {ENV_COLLECTION} is {cfg.official_collection!r}")
    if cfg.embedding_model != LEGACY_EMBEDDING_MODEL:
        raise RebuildRefused(f"the ingest script embeds with {LEGACY_EMBEDDING_MODEL!r}, but {ENV_EMBEDDING_MODEL} is {cfg.embedding_model!r}: "
                             "queries would then be embedded by a different model than the index (unset it, or build the index yourself)")
    code = (runner or _run_legacy_ingest)()
    if code != 0:
        raise RebuildFailed(f"agent_race/ingest.py exited with status {code}")
