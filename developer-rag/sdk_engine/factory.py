"""Assemble a production Engine from configuration. Heavy imports happen here, not at module import."""

from __future__ import annotations

import sys
from typing import Any, Callable, List, Optional

from .config import EngineConfig, get_api_key
from .graph import Deps, Engine
from .llm import GroqChat
from .mcp_client import McpToolClient, McpUnavailable, default_server_config
from .retrieval import (
    ChromaRetriever, RetrievalScope, make_embed_fn, make_embed_many_fn, make_reranker_fn, open_collection,
)
from .sqlite_store import SqliteUploadClient
from .symbols import SymbolIndex
from .trace import JsonlSink
from .uploads import DeleteResult, DocInfo, UploadResult, UploadStore
from .versioning import VersionCatalog


class LazyRetriever:
    """The in-process retriever is only the FALLBACK when MCP tools are in use, so it is built on first use:
    the CLI process then never loads the embedding model unless a tool call actually failed."""

    def __init__(self, build: Callable[[], ChromaRetriever]):
        self._build = build
        self._inner: Optional[ChromaRetriever] = None

    def _get(self) -> ChromaRetriever:
        if self._inner is None:
            self._inner = self._build()
        return self._inner

    def search(self, query: str, scope: RetrievalScope, top_k: int = 5) -> Any:
        return self._get().search(query, scope, top_k)

    def fetch(self, scope: RetrievalScope, limit: int = 50) -> Any:
        return self._get().fetch(scope, limit)


class LazySymbolIndex:
    """The in-process symbol index is the FALLBACK for the `check_symbol` tool, so it is built on first use (it reads the
    collection's text once; it never loads the embedding model)."""

    def __init__(self, build: Callable[[], SymbolIndex]):
        self._build = build
        self._inner: Optional[SymbolIndex] = None

    def check(self, version: str, symbol: str, endpoint: Optional[str] = None) -> Any:
        if self._inner is None:
            self._inner = self._build()
        return self._inner.check(version, symbol, endpoint)


class LazyUploadStore:
    """Opens the private uploads store (separate on-disk Chroma directory) and loads the embedder only when an
    upload operation or an upload question actually happens."""

    def __init__(self, cfg: EngineConfig):
        self._cfg = cfg
        self._inner: Optional[UploadStore] = None

    def _get(self) -> UploadStore:
        if self._inner is None:
            # A plain SQLite file, not Chroma: its secure_delete + VACUUM give a deletion we can verify on disk
            # (Chroma's index files were measured to retain deleted text; see docs/week12/phase4.md).
            self._inner = UploadStore(SqliteUploadClient(self._cfg.uploads_path / "uploads.sqlite3"),
                                      make_embed_fn(self._cfg.embedding_model),
                                      make_embed_many_fn(self._cfg.embedding_model), self._cfg)
        return self._inner

    def ingest(self, owner_id: str, filename: str, data: bytes) -> UploadResult:
        return self._get().ingest(owner_id, filename, data)

    def list(self, owner_id: str) -> List[DocInfo]:
        return self._get().list(owner_id)

    def delete(self, owner_id: str, doc_id: str) -> DeleteResult:
        return self._get().delete(owner_id, doc_id)

    def delete_all(self, owner_id: str) -> DeleteResult:
        return self._get().delete_all(owner_id)

    def search(self, owner_id: str, query: str, top_k: int = 5) -> Any:
        return self._get().search(owner_id, query, top_k)


def build_engine(config: Optional[EngineConfig] = None, *, use_llm: bool = True, use_mcp: bool = True) -> Engine:
    cfg = config or EngineConfig.from_env()
    catalog = VersionCatalog.from_page_metadata(cfg.page_metadata_path)
    sink = JsonlSink(cfg.trace_dir)

    def build_local() -> ChromaRetriever:
        return ChromaRetriever(
            open_collection(cfg),  # raises IndexMissing with a build hint if the index is absent
            make_embed_fn(cfg.embedding_model),
            reranker=make_reranker_fn(cfg.reranker_model) if cfg.use_reranker else None,
            max_top_k=cfg.max_top_k,
            hybrid=cfg.hybrid_retrieval,
        )

    tools = None
    if use_mcp:
        try:
            tools = McpToolClient([default_server_config(cfg)], call_timeout_s=cfg.tool_timeout_s,
                                  max_output_chars=cfg.max_tool_output_chars, log_dir=cfg.trace_dir, sink=sink).start()
        except McpUnavailable as exc:
            print(f"warning: MCP tools unavailable ({exc}); using the in-process retriever", file=sys.stderr)
    # A missing official index surfaces per request as a clear `index_missing` error (an upload-only question
    # does not need it), rather than blocking start-up.
    llm = GroqChat(cfg) if (use_llm and get_api_key()) else None
    return Engine(Deps(config=cfg, catalog=catalog, retriever=LazyRetriever(build_local), llm=llm, sink=sink, tools=tools,
                       uploads=LazyUploadStore(cfg),
                       symbols=LazySymbolIndex(lambda: SymbolIndex.from_collection(open_collection(cfg), catalog))))
