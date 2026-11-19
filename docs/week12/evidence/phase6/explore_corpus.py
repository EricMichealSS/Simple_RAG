"""Read-only look at the real race_docs index to calibrate Phase 6 (code validation)."""
import sys
from pathlib import Path

ROOT = Path(r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
sys.path.insert(0, str(ROOT))
from sdk_engine.config import EngineConfig
from sdk_engine.retrieval import open_collection
from sdk_engine.symbols import SymbolIndex
from sdk_engine.versioning import VersionCatalog

cfg = EngineConfig.from_env()
catalog = VersionCatalog.from_page_metadata(cfg.page_metadata_path)
col = open_collection(cfg)
idx = SymbolIndex.from_collection(col, catalog)
print("versions:", {k.value: v for k, v in catalog.versions.items()})
got = col.get(include=["documents", "metadatas"])
by_version = {}
for d, m in zip(got["documents"], got["metadatas"]):
    by_version.setdefault(m.get("api_version"), []).append((m.get("endpoint"), m.get("content_type"), d))
print("chunks per version:", {k: len(v) for k, v in by_version.items()})

probes = [
    ("2022-11-28", "X-GitHub-Api-Version", None), ("2022-11-28", "assignee", "create_issue"), ("2022-11-28", "assignees", "create_issue"),
    ("2022-11-28", "title", "create_issue"), ("2022-11-28", "labels", "create_issue"), ("2022-11-28", "make_latest", "create_release"),
    ("2022-11-28", "Accept", None), ("2025-06-01", "assignees", "create_issue"), ("2025-06-01", "X-GitHub-Api-Version", None),
    ("2.x", "--generate-markdown", None), ("3.x", "--generate-markdown", None), ("3.x", "-l", None), ("3.x", "groupId", None),
]
for v, s, e in probes:
    f = idx.check(v, s, e)
    print(f"check({v!r}, {s!r}, {e!r}) -> {f.status}" + (f" repl={f.replacement}" if f.replacement else ""))

print("\n--- literal occurrences of code-ish markers in the corpus text, per version ---")
for marker in ("curl", "```", "X-GitHub-Api-Version", "-H ", "import ", "def ", "timeout", "octokit", "requests."):
    print(f"{marker!r:26}", {v: sum(marker in d for _, _, d in rows) for v, rows in by_version.items()})

print("\n--- one 2022-11-28 create_issue spec chunk (first 900 chars) ---")
for ep, ct, d in by_version.get("2022-11-28", []):
    if ep == "create_issue" and ct == "spec":
        print(" ".join(d.split())[:900])
        break
