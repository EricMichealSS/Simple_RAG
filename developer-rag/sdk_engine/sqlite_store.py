"""A tiny SQLite-backed vector store for PRIVATE uploads, exposing the small Chroma-like surface the upload code and
`ChromaRetriever` use (`get_or_create_collection`, `get_collection`, `delete_collection`, collection `add / get / query /
delete / count`).

Why not Chroma for uploads: measured (docs/week12/phase4.md), Chroma's persisted HNSW files routinely contain stale
document-text residue, and after a delete the deleted text was still on disk in 6 of 40 realistic cycles (2 of 40 even
after a later sweep). Private uploads need deletion that is checkable. Here the ONLY persistent file is one SQLite
database with `secure_delete=ON` (deleted content is overwritten), no WAL (`journal_mode=DELETE`), and a `VACUUM` after
deletes; `tests/test_sqlite_store.py` scans the whole directory for residue over many cycles and expects none.

Search is brute-force cosine distance over the (filtered) rows with numpy. Uploads are small (<= max_docs_per_owner x
max_chunks_per_doc chunks per owner), so this is milliseconds; it is NOT meant for the large official corpus.

Fails closed: only equality filters on `owner_id` / `doc_id` (optionally under `$and`) are supported; any other `where`
raises, so a filter can never be silently ignored.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import numpy as np

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")
_FILTER_COLUMNS = ("owner_id", "doc_id")


class _CollectionInfo:
    def __init__(self, name: str):
        self.name = name


def _clauses(where: Optional[Dict[str, Any]]) -> "tuple[str, List[Any]]":
    """Translate the supported `where` shapes to a parametrised SQL fragment. Unsupported shapes raise (fail closed)."""
    if not where:
        return "", []
    items = where["$and"] if set(where) == {"$and"} else [where]
    sql, params = [], []
    for item in items:
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError("unsupported where filter")
        (key, value), = item.items()
        if key not in _FILTER_COLUMNS or not isinstance(value, str):
            raise ValueError(f"unsupported where filter on {key!r}")
        sql.append(f"{key} = ?")
        params.append(value)
    return " AND " + " AND ".join(sql), params


class SqliteCollection:
    supports_row_delete = True

    def __init__(self, client: "SqliteUploadClient", name: str):
        self._c, self.name = client, name

    def add(self, ids: Sequence[str], documents: Sequence[str], metadatas: Sequence[Dict[str, Any]],
            embeddings: Sequence[Sequence[float]]) -> None:
        if not (len(ids) == len(documents) == len(metadatas) == len(embeddings)):
            raise ValueError("ids, documents, metadatas and embeddings must have the same length")
        rows = []
        for i, d, m, e in zip(ids, documents, metadatas, embeddings):
            m = dict(m or {})
            rows.append((self.name, str(i), d, json.dumps(m, ensure_ascii=False), np.asarray(e, dtype=np.float32).tobytes(),
                         m.get("owner_id"), m.get("doc_id")))
        with self._c._tx() as con:
            con.executemany("INSERT INTO chunks (collection, id, document, metadata, embedding, owner_id, doc_id) VALUES (?,?,?,?,?,?,?)", rows)

    def _rows(self, where: Optional[Dict[str, Any]], ids: Optional[Sequence[str]], limit: Optional[int]) -> List[tuple]:
        frag, params = _clauses(where)
        sql = f"SELECT id, document, metadata, embedding FROM chunks WHERE collection = ?{frag}"
        args: List[Any] = [self.name, *params]
        if ids is not None:
            ids = list(ids)
            if not ids:
                return []
            sql += " AND id IN (" + ",".join("?" * len(ids)) + ")"
            args += ids
        sql += " ORDER BY rowid"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._c._tx() as con:
            return con.execute(sql, args).fetchall()

    def get(self, ids: Optional[Sequence[str]] = None, where: Optional[Dict[str, Any]] = None,
            include: Optional[Sequence[str]] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        include = list(include or ["documents", "metadatas"])
        rows = self._rows(where, ids, limit)
        out: Dict[str, Any] = {"ids": [r[0] for r in rows]}
        if "documents" in include:
            out["documents"] = [r[1] for r in rows]
        if "metadatas" in include:
            out["metadatas"] = [json.loads(r[2]) for r in rows]
        if "embeddings" in include:
            out["embeddings"] = [np.frombuffer(r[3], dtype=np.float32).tolist() for r in rows]
        return out

    def query(self, query_embeddings: Sequence[Sequence[float]], n_results: int, where: Optional[Dict[str, Any]] = None,
              include: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        include = list(include or ["documents", "metadatas", "distances"])
        rows = self._rows(where, None, None)
        res: Dict[str, List[Any]] = {"ids": [], "documents": [], "metadatas": [], "distances": []}
        for q in query_embeddings:
            if not rows:
                for k in res:
                    res[k].append([])
                continue
            mat = np.stack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
            qv = np.asarray(q, dtype=np.float32)
            denom = np.linalg.norm(mat, axis=1) * (np.linalg.norm(qv) or 1.0)
            dist = 1.0 - (mat @ qv) / np.where(denom == 0, 1.0, denom)        # cosine distance, as Chroma reports it
            order = np.argsort(dist, kind="stable")[: max(1, int(n_results))]
            res["ids"].append([rows[i][0] for i in order])
            res["documents"].append([rows[i][1] for i in order])
            res["metadatas"].append([json.loads(rows[i][2]) for i in order])
            res["distances"].append([float(dist[i]) for i in order])
        return {k: v for k, v in res.items() if k == "ids" or k in include}

    def delete(self, ids: Optional[Sequence[str]] = None, where: Optional[Dict[str, Any]] = None) -> None:
        if ids is None and not where:
            raise ValueError("delete needs ids or a where filter")
        frag, params = _clauses(where)
        sql, args = f"DELETE FROM chunks WHERE collection = ?{frag}", [self.name, *params]
        if ids is not None:
            ids = list(ids)
            if not ids:
                return
            sql += " AND id IN (" + ",".join("?" * len(ids)) + ")"
            args += ids
        with self._c._tx() as con:
            con.execute(sql, args)

    def count(self) -> int:
        with self._c._tx() as con:
            return con.execute("SELECT COUNT(*) FROM chunks WHERE collection = ?", (self.name,)).fetchone()[0]


class SqliteUploadClient:
    """Minimal client over one SQLite file. Thread-safe (one lock); a new connection per operation, so another process
    can use the same file between operations."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._tx() as con:
            con.execute("CREATE TABLE IF NOT EXISTS collections (name TEXT PRIMARY KEY)")
            con.execute("""CREATE TABLE IF NOT EXISTS chunks (
                collection TEXT NOT NULL, id TEXT NOT NULL, document TEXT NOT NULL, metadata TEXT NOT NULL,
                embedding BLOB NOT NULL, owner_id TEXT, doc_id TEXT, PRIMARY KEY (collection, id))""")
            con.execute("CREATE INDEX IF NOT EXISTS chunks_doc ON chunks (collection, doc_id)")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = sqlite3.connect(str(self.path), timeout=15)
            try:
                con.execute("PRAGMA secure_delete = ON")       # deleted content is overwritten with zeros
                con.execute("PRAGMA journal_mode = DELETE")    # no WAL file that could keep copies of deleted pages
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise
            finally:
                con.close()

    @staticmethod
    def _check(name: str) -> str:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise ValueError("invalid collection name")
        return name

    def get_or_create_collection(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> SqliteCollection:
        self._check(name)
        with self._tx() as con:
            con.execute("INSERT OR IGNORE INTO collections (name) VALUES (?)", (name,))
        return SqliteCollection(self, name)

    def get_collection(self, name: str) -> SqliteCollection:
        self._check(name)
        with self._tx() as con:
            if con.execute("SELECT 1 FROM collections WHERE name = ?", (name,)).fetchone() is None:
                raise ValueError(f"collection {name!r} does not exist")
        return SqliteCollection(self, name)

    def list_collections(self) -> List[_CollectionInfo]:
        with self._tx() as con:
            return [_CollectionInfo(r[0]) for r in con.execute("SELECT name FROM collections ORDER BY name")]

    def delete_collection(self, name: str) -> None:
        self._check(name)
        with self._tx() as con:
            con.execute("DELETE FROM chunks WHERE collection = ?", (name,))
            con.execute("DELETE FROM collections WHERE name = ?", (name,))

    def compact(self) -> bool:
        """Rewrite the file so freed pages are discarded (secure_delete already zeroed their content). Best effort."""
        try:
            with self._lock:
                con = sqlite3.connect(str(self.path), timeout=15)
                try:
                    con.execute("PRAGMA secure_delete = ON")
                    con.execute("VACUUM")
                finally:
                    con.close()
            return True
        except sqlite3.Error:
            return False
