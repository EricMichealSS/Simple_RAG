"""Shared fixtures. Everything here runs with no API key and no ML model."""

import hashlib
import math
import re
import sys
import uuid
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parents[1]  # developer-rag/
sys.path.insert(0, str(ENGINE_ROOT))

from sdk_engine.versioning import VersionCatalog  # noqa: E402

PAGE_METADATA = ENGINE_ROOT / "agent_race" / "page_metadata.json"


def hash_embed(text: str, dim: int = 64):
    """Deterministic bag-of-words sign-hashing embedding (similar words -> similar vectors)."""
    vec = [0.0] * dim
    for tok in re.findall(r"[a-z0-9_]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


# (id, version, endpoint, content_type, page, text)  - mirrors the real race_docs metadata schema.
SYNTH = [
    ("gh:p4:spec:create-an-issue:0", "2022-11-28", "create_issue", "spec", 4,
     "Create an issue. assignee string login for the user this issue should be assigned to. labels array"),
    ("gh:p5:spec:create-an-issue:1", "2025-06-01", "create_issue", "spec", 5,
     "Create an issue API version 2025-06-01 assignees array replaces the deprecated assignee field type field"),
    ("gh:p5:diff:previous-version:2", "2025-06-01", "create_issue", "diff", 5,
     "Previous version 2022-11-28 only accepted a single assignee string no type field assignee"),
    ("gh:p10:spec:create-a-release:3", "2022-11-28", "create_release", "spec", 10,
     "Create a release tag_name target_commitish prerelease body"),
    ("gh:p12:spec:create-a-release:4", "2025-06-01", "create_release", "spec", 12,
     "Create a release API version 2025-06-01 make_latest discussion_category_name"),
    ("gh:p13:diff:previous-version:5", "2025-06-01", "create_release", "diff", 13,
     "Previous version make_latest was an undocumented heuristic release"),
    ("sw2:p2:spec:java-generator:6", "2.x", "java_generator", "spec", 2,
     "Java generator options groupId artifactId swagger codegen 2.x library"),
    ("sw3:p3:spec:java-generator:7", "3.x", "java_generator", "spec", 3,
     "Java generator options groupId artifactId swagger codegen 3.x library"),
    ("sw3:p3:diff:compared-to-v2:8", "3.x", "java_generator", "diff", 3,
     "Compared to v2 the java generator options were renamed swagger codegen"),
]


def _meta(version, endpoint, content_type, page):
    return {
        "source_file": "github_combined_reference.pdf" if version[0] == "2" and "-" in version else "swagger_reference.pdf",
        "page": page, "anchor": "top", "content_type": content_type,
        "api_version": version, "endpoint": endpoint, "has_diff": content_type == "diff",
    }


_BUILD_INDEX = r"""
import json, sys
import chromadb
from sdk_engine.retrieval import _hash_embed
rows = json.load(open(sys.argv[2], encoding="utf-8"))
col = chromadb.PersistentClient(path=sys.argv[1]).create_collection("synth", metadata={"hnsw:space": "cosine"})
col.add(ids=[r["id"] for r in rows], documents=[r["text"] for r in rows], metadatas=[r["meta"] for r in rows],
        embeddings=[_hash_embed(r["text"]) for r in rows])
"""


@pytest.fixture(scope="session")
def mcp_index_dir(tmp_path_factory):
    """A tiny ON-DISK index (collection 'synth', deterministic 'hash-test' embedder) for tests that spawn the real
    MCP server. Built in a short-lived subprocess so no handle to the store stays open in the test process."""
    import json
    import subprocess

    base = tmp_path_factory.mktemp("mcp_index")
    rows = [{"id": r[0], "text": r[5], "meta": _meta(r[1], r[2], r[3], r[4])} for r in SYNTH]
    (base / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    env = {k: __import__("os").environ[k] for k in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME")
           if k in __import__("os").environ}
    env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    subprocess.run([sys.executable, "-c", _BUILD_INDEX, str(base / "chroma"), str(base / "rows.json")], cwd=str(ENGINE_ROOT),
                   env=env, check=True, timeout=120)
    return base


class LeakyCollection:
    """A store whose filter is broken: ignores `where` and returns everything. Nothing may trust it."""

    def __init__(self, real):
        self._r = real

    def get(self, where=None, include=None, limit=None):
        return self._r.get(include=include)

    def query(self, query_embeddings, n_results, where=None, include=None):
        return self._r.query(query_embeddings=query_embeddings, n_results=n_results, include=include)


@pytest.fixture(scope="session")
def catalog():
    return VersionCatalog.from_page_metadata(PAGE_METADATA)


@pytest.fixture()
def synth_collection():
    import chromadb

    client = chromadb.EphemeralClient()
    col = client.create_collection(name=f"t_{uuid.uuid4().hex[:12]}", metadata={"hnsw:space": "cosine"})
    ids = [r[0] for r in SYNTH]
    docs = [r[5] for r in SYNTH]
    metas = [_meta(r[1], r[2], r[3], r[4]) for r in SYNTH]
    # Two chunks that must NEVER satisfy a version filter: missing and non-matching version metadata.
    ids += ["bad:no-version", "bad:unknown-version"]
    docs += ["Create an issue assignee orphan chunk without version metadata", "Create an issue assignee unknown version chunk"]
    metas += [{"source_file": "orphan.pdf", "page": 1, "anchor": "top", "content_type": "spec"},
              {"source_file": "orphan.pdf", "page": 2, "anchor": "top", "content_type": "spec", "api_version": "unknown"}]
    col.add(ids=ids, documents=docs, metadatas=metas, embeddings=[hash_embed(d) for d in docs])
    return col


@pytest.fixture(autouse=True)
def _never_load_the_real_dotenv(monkeypatch):
    """`cli.main` loads developer-rag/.env (and the real API key in it) into os.environ. A test must never do that: it would leak the key into every
    later test in the process, and a later test that builds an engine could then make a real, paid model call."""
    from sdk_engine import cli

    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)
