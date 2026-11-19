"""Central configuration for the SDK docs answer engine.

Deliberately independent of the legacy ``developer-rag/config.py``, which (a) raises at import
when ``GROK_API_KEY`` is unset and (b) uses cwd-relative paths (a different working directory
silently opens an empty vector store). Here:

* paths are anchored to this file, never to the current working directory;
* nothing secret is read at import time; the API key is fetched lazily and never stored on the
  config object, so ``repr(config)`` and logs cannot leak it;
* every limit is explicit and bounded, and a bad setting is refused at start-up with a message that
  NAMES the environment variable (``ConfigError``), never a bare traceback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError, field_validator

ENGINE_ROOT = Path(__file__).resolve().parents[1]  # .../developer-rag

# Environment variable names (documented in sdk_engine/README.md and .env.example).
ENV_CHROMA_PATH = "SDK_ENGINE_CHROMA_PATH"
ENV_COLLECTION = "SDK_ENGINE_COLLECTION"
ENV_TRACE_DIR = "SDK_ENGINE_TRACE_DIR"
ENV_PAGE_METADATA = "SDK_ENGINE_PAGE_METADATA"
ENV_DEFAULT_VERSION_PREFIX = "SDK_ENGINE_DEFAULT_VERSION_"  # + PRODUCT, e.g. ..._GITHUB_REST
ENV_MODEL = "SDK_ENGINE_MODEL"
ENV_USE_RERANKER = "SDK_ENGINE_USE_RERANKER"
ENV_HYBRID = "SDK_ENGINE_HYBRID"
ENV_UPLOADS_PATH = "SDK_ENGINE_UPLOADS_PATH"
ENV_ANSWER_HOSTS = "SDK_ENGINE_ANSWER_HOSTS"
# Hosts an answer may link to or embed. Operator-owned: the documents are untrusted, so "the host appears in a source" is not a reason to allow it.
DEFAULT_ANSWER_HOSTS = ("api.github.com", "github.com", "docs.github.com", "repo1.maven.org", "petstore.swagger.io", "swagger.io",
                        "localhost", "127.0.0.1")
ENV_MAX_UPLOAD_BYTES = "SDK_ENGINE_MAX_UPLOAD_BYTES"
ENV_MAX_DISTANCE = "SDK_ENGINE_MAX_DISTANCE"
ENV_UPLOAD_MAX_DISTANCE = "SDK_ENGINE_UPLOAD_MAX_DISTANCE"
ENV_EMBEDDING_MODEL = "SDK_ENGINE_EMBEDDING_MODEL"
ENV_MAX_OUTPUT_CHARS = "SDK_ENGINE_MAX_TOOL_OUTPUT_CHARS"
ENV_TOOL_TIMEOUT = "SDK_ENGINE_TOOL_TIMEOUT_S"
ENV_LLM_BASE_URL = "SDK_ENGINE_LLM_BASE_URL"
ENV_LLM_MAX_TOKENS = "SDK_ENGINE_LLM_MAX_TOKENS"
ENV_LLM_TIMEOUT = "SDK_ENGINE_LLM_TIMEOUT_S"
ENV_TOP_K = "SDK_ENGINE_TOP_K"
ENV_API_KEY = "GROK_API_KEY"  # name kept for continuity with the existing project

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(ValueError):
    """A setting is missing, malformed or out of range. The message names the environment variable. (A ValueError, so older handlers still catch it.)"""


class EngineConfig(BaseModel):
    """Validated, immutable-by-convention engine settings."""

    chroma_path: Path = ENGINE_ROOT / "data" / "chroma"
    official_collection: str = "race_docs"
    upload_collection_prefix: str = "uploads_"
    # Uploads live in a physically SEPARATE store from the official index: no accidental mixing, and the MCP server
    # process (which has the official index open) never has the private uploads open.
    uploads_path: Path = ENGINE_ROOT / "data" / "uploads"
    trace_dir: Path = ENGINE_ROOT / "data" / "traces"
    page_metadata_path: Path = ENGINE_ROOT / "agent_race" / "page_metadata.json"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    # Retrieval defaults chosen from the golden-set comparison in docs/week12/phase2.md (n=31, tuned on
    # the same questions it was scored on, so optimistic): dense-only beat hybrid on this small corpus,
    # and the cross-encoder cost ~1.1 s p50 on CPU for no top-1 gain. Both stay switchable.
    hybrid_retrieval: bool = False
    use_reranker: bool = False
    generation_model: str = "openai/gpt-oss-120b"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    # gpt-oss models spend part of max_tokens on hidden reasoning; 700 truncated a migration answer mid-citation in the first live run.
    llm_max_tokens: int = Field(default=1500, ge=16, le=4096)
    llm_timeout_s: float = Field(default=45.0, gt=0, le=300)
    # Placeholder sufficiency threshold on cosine distance when no endpoint can be inferred from the
    # question. NOT tuned (the legacy 0.60 was also "do not assume optimal"); Phase 8 decides it.
    max_dense_distance: float = Field(default=0.65, gt=0, le=2.0)
    # Uploads get their OWN gate. Measured with bge-small on 10 answerable + 10 unanswerable authored questions over three
    # small authored documents (docs/week12/phase4.md): answerable top-1 distance 0.105-0.373, unanswerable 0.481-0.611.
    # The 0.65 default would have admitted 10/10 unanswerable questions; 0.50 keeps 10/10 answerable (0.127 margin) and
    # passes 2/10 unanswerable to the model, whose prompt tells it to abstain. Small authored sample: re-tune on real data.
    upload_max_dense_distance: float = Field(default=0.50, gt=0, le=2.0)
    max_prompt_chars: int = Field(default=6000, ge=500, le=50000)
    answer_allowed_hosts: Tuple[str, ...] = DEFAULT_ANSWER_HOSTS

    # Only versions the operator explicitly configured. We never invent a default.
    default_versions: dict[str, str] = Field(default_factory=dict)

    # Bounded limits.
    top_k: int = Field(default=5, ge=1, le=20)
    max_top_k: int = Field(default=20, ge=1, le=50)
    max_question_chars: int = Field(default=2000, ge=1, le=20000)
    max_graph_steps: int = Field(default=25, ge=1, le=100)
    max_retrieval_retries: int = Field(default=1, ge=0, le=3)
    max_generation_retries: int = Field(default=1, ge=0, le=3)
    tool_timeout_s: float = Field(default=20.0, gt=0, le=300)
    max_tool_output_chars: int = Field(default=20000, ge=100, le=200000)

    # Upload limits (Phase 4). Files are processed in memory and never written to disk or executed.
    max_upload_bytes: int = Field(default=5_000_000, ge=1_000, le=50_000_000)
    max_pdf_pages: int = Field(default=200, ge=1, le=2000)
    max_extracted_chars: int = Field(default=1_000_000, ge=1_000, le=20_000_000)
    max_chunks_per_doc: int = Field(default=500, ge=1, le=5000)
    max_docs_per_owner: int = Field(default=25, ge=1, le=1000)

    @field_validator("official_collection", "upload_collection_prefix")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        # Chroma collection names: keep to a conservative charset so a name can never carry a path.
        if not v or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in v):
            raise ValueError("collection names may only contain letters, digits, '_' and '-'")
        return v

    @field_validator("llm_base_url")
    @classmethod
    def _safe_llm_url(cls, v: str) -> str:
        """The API key is sent to this address, so it must not travel in clear text or carry credentials of its own."""
        parts = urlsplit(v)
        host = (parts.hostname or "").lower()
        if parts.username or parts.password:
            raise ValueError("must not contain credentials")
        if (parts.scheme == "https" and host) or (parts.scheme == "http" and host in _LOCAL_HOSTS):
            return v
        raise ValueError("must be an https URL (plain http is allowed only for localhost, because the API key is sent to this address)")

    def public_view(self) -> Dict[str, Any]:
        """The effective settings as JSON-friendly values for the health check. There is no key field to leak: the key is never stored here."""
        out: Dict[str, Any] = {}
        for k, v in self.model_dump().items():
            out[k] = str(v) if isinstance(v, Path) else list(v) if isinstance(v, tuple) else [f"{a}={b}" for a, b in v.items()] if isinstance(v, dict) else v
        return out

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "EngineConfig":
        env = os.environ if environ is None else environ

        def value(name: str) -> Optional[str]:
            raw = env.get(name)
            return raw.strip() if isinstance(raw, str) and raw.strip() else None      # a blank value counts as unset

        def number(name: str, kind) -> Optional[Any]:
            raw = value(name)
            if raw is None:
                return None
            try:
                return kind(raw)
            except ValueError:
                raise ConfigError(f"{name} must be {'an integer' if kind is int else 'a number'}, got {raw!r}") from None

        kwargs: Dict[str, Any] = {}
        for name, field in _PATHS:
            if value(name):
                kwargs[field] = Path(value(name)).expanduser().resolve()
        for name, field in _STRINGS:
            if value(name):
                kwargs[field] = value(name)
        for name, field in _FLAGS:
            if value(name):
                kwargs[field] = value(name).lower() in {"1", "true", "yes", "on"}
        for name, field, kind in _NUMBERS:
            parsed = number(name, kind)
            if parsed is not None:
                kwargs[field] = parsed
        if value(ENV_ANSWER_HOSTS):
            extra = tuple(h.strip().lower() for h in value(ENV_ANSWER_HOSTS).split(",") if h.strip())
            kwargs["answer_allowed_hosts"] = tuple(dict.fromkeys(DEFAULT_ANSWER_HOSTS + extra))
        defaults = {
            key[len(ENV_DEFAULT_VERSION_PREFIX):].lower(): v.strip()
            for key, v in env.items()
            if key.startswith(ENV_DEFAULT_VERSION_PREFIX) and isinstance(v, str) and v.strip()
        }
        if defaults:
            kwargs["default_versions"] = defaults
        try:
            return cls(**kwargs)
        except ValidationError as exc:
            problems = []
            for err in exc.errors():
                field = str(err["loc"][0]) if err.get("loc") else "?"
                problems.append(f"{_FIELD_ENV.get(field, field)}: {err['msg']}")       # never echo `input`: a URL could carry a credential
            raise ConfigError("invalid configuration: " + "; ".join(problems)) from None


_PATHS = ((ENV_CHROMA_PATH, "chroma_path"), (ENV_TRACE_DIR, "trace_dir"), (ENV_PAGE_METADATA, "page_metadata_path"), (ENV_UPLOADS_PATH, "uploads_path"))
_STRINGS = ((ENV_COLLECTION, "official_collection"), (ENV_MODEL, "generation_model"), (ENV_EMBEDDING_MODEL, "embedding_model"),
            (ENV_LLM_BASE_URL, "llm_base_url"))
_FLAGS = ((ENV_USE_RERANKER, "use_reranker"), (ENV_HYBRID, "hybrid_retrieval"))
_NUMBERS = ((ENV_UPLOAD_MAX_DISTANCE, "upload_max_dense_distance", float), (ENV_MAX_DISTANCE, "max_dense_distance", float),
            (ENV_MAX_UPLOAD_BYTES, "max_upload_bytes", int), (ENV_MAX_OUTPUT_CHARS, "max_tool_output_chars", int),
            (ENV_TOOL_TIMEOUT, "tool_timeout_s", float), (ENV_LLM_MAX_TOKENS, "llm_max_tokens", int),
            (ENV_LLM_TIMEOUT, "llm_timeout_s", float), (ENV_TOP_K, "top_k", int))
_FIELD_ENV: Dict[str, str] = {field: name for name, field in (*_PATHS, *_STRINGS, *_FLAGS)} | {field: name for name, field, _ in _NUMBERS} | {
    "answer_allowed_hosts": ENV_ANSWER_HOSTS, "default_versions": ENV_DEFAULT_VERSION_PREFIX + "<PRODUCT>"}


ENV_API_KEY_NAMES = ("GROQ_API_KEY", ENV_API_KEY)   # the provider's real name first, then the project's legacy spelling


def get_api_key(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Return the provider key or None. Read lazily, never cached, never logged.

    Accepts `GROQ_API_KEY` (the provider's name) and the legacy `GROK_API_KEY` that the older scripts require."""
    env = os.environ if environ is None else environ
    for name in ENV_API_KEY_NAMES:
        value = env.get(name, "").strip()
        if value:
            return value
    return None
