"""The 3 tools available to both the agent and the fixed workflow.

Deliberately three different retrieval strategies, matched to three different
jobs, so no two tools ever compete to answer the same call:

  search_docs        -- fuzzy, semantic, no filter. "I don't know where this
                         lives yet." Returns candidates + their metadata so a
                         caller can find the (api_version, endpoint) pair.
  get_openapi_spec    -- exact metadata lookup. "I already know the version
                         and endpoint; give me its CURRENT parameter table."
                         Never returns the deprecation/diff text.
  check_deprecation   -- exact metadata lookup, has_diff=True only. "What
                         changed / was deprecated for this endpoint between
                         versions." Never returns the current parameter table
                         on its own -- only the diff paragraph.

Each tool's JSON schema (TOOL_SCHEMAS below) is what gets handed to the LLM
so it can choose and call one; api_version and endpoint are both enums, not
free text, so the model can't call get_openapi_spec("teh new one") and get a
silent no-match.
"""

import sys
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).parent.parent))

CHROMA_PATH = str(Path(__file__).parent.parent / "data" / "chroma")
COLLECTION_NAME = "race_docs"

_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
_collection = _chroma.get_collection(COLLECTION_NAME)
_embedding_model = SentenceTransformer("BAAI/bge-small-en-v1.5")

KNOWN_ENDPOINTS = [
    "render_markdown", "create_issue", "create_pull_request", "create_repository", "create_release",
    "cli_general", "java_generator", "python_generator", "ignore_file", "config_file",
]
KNOWN_API_VERSIONS = ["2022-11-28", "2025-06-01", "2.x", "3.x"]


# ---------------------------------------------------------------------------
# Tool 1 — search_docs
# ---------------------------------------------------------------------------

def search_docs(query: str, top_k: int = 5) -> list:
    """Semantic search across all 3 docs, no metadata filter."""
    vec = _embedding_model.encode([query], normalize_embeddings=True)[0].tolist()
    results = _collection.query(query_embeddings=[vec], n_results=top_k, include=["documents", "metadatas", "distances"])
    out = []
    for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
        out.append({
            "text": doc, "source_file": meta["source_file"], "page": meta["page"],
            "api_version": meta["api_version"], "endpoint": meta["endpoint"],
            "has_diff": meta["has_diff"], "distance": round(dist, 4),
        })
    return out


# ---------------------------------------------------------------------------
# Tool 2 — get_openapi_spec
# ---------------------------------------------------------------------------

def get_openapi_spec(api_version: str, endpoint: str) -> dict:
    """Exact lookup: the CURRENT parameter table for this (version, endpoint).
    Never includes the deprecation/diff paragraph -- use check_deprecation for that.
    """
    if api_version not in KNOWN_API_VERSIONS:
        return {"error": f"api_version must be one of {KNOWN_API_VERSIONS}, got {api_version!r}"}
    if endpoint not in KNOWN_ENDPOINTS:
        return {"error": f"endpoint must be one of {KNOWN_ENDPOINTS}, got {endpoint!r}"}

    got = _collection.get(
        where={"$and": [{"api_version": api_version}, {"endpoint": endpoint}, {"content_type": "spec"}]},
        include=["documents", "metadatas"],
    )
    if not got["ids"]:
        return {"found": False, "text": None}

    # Sort by page then chunk order (ids encode :p{page}: and trailing index)
    rows = sorted(zip(got["ids"], got["documents"]), key=lambda r: r[0])
    return {"found": True, "text": "\n\n".join(text for _id, text in rows)}


# ---------------------------------------------------------------------------
# Tool 3 — check_deprecation (the new tool this week)
# ---------------------------------------------------------------------------

def check_deprecation(api_version: str, endpoint: str) -> dict:
    """Exact lookup: ONLY the 'what changed / was deprecated' text for this
    (version, endpoint), i.e. chunks where has_diff=True. Never returns the
    current parameter table -- use get_openapi_spec for that.
    """
    if api_version not in KNOWN_API_VERSIONS:
        return {"error": f"api_version must be one of {KNOWN_API_VERSIONS}, got {api_version!r}"}
    if endpoint not in KNOWN_ENDPOINTS:
        return {"error": f"endpoint must be one of {KNOWN_ENDPOINTS}, got {endpoint!r}"}

    got = _collection.get(
        where={"$and": [{"api_version": api_version}, {"endpoint": endpoint}, {"has_diff": True}]},
        include=["documents"],
    )
    if not got["ids"]:
        return {"found": False, "text": None, "note": "No recorded change/deprecation for this version+endpoint."}

    rows = sorted(zip(got["ids"], got["documents"]), key=lambda r: r[0])
    return {"found": True, "text": "\n\n".join(text for _id, text in rows)}


# ---------------------------------------------------------------------------
# Tool schemas handed to the LLM (OpenAI-style function-calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": (
                "Semantically search across all migration docs (GitHub REST API and Swagger "
                "Codegen references) with no version/endpoint filter. Use this FIRST when you "
                "don't yet know which api_version or endpoint a question is about -- it returns "
                "candidate chunks along with their api_version/endpoint metadata so you can then "
                "call get_openapi_spec or check_deprecation precisely. Does not return a "
                "structured parameter table or a changelog -- just ranked passages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_openapi_spec",
            "description": (
                "Return the CURRENT parameter/config table for one specific (api_version, "
                "endpoint) pair -- field names, types, defaults, requiredness. Does NOT say what "
                "changed or was deprecated versus another version; call check_deprecation for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "api_version": {"type": "string", "enum": KNOWN_API_VERSIONS},
                    "endpoint": {"type": "string", "enum": KNOWN_ENDPOINTS},
                },
                "required": ["api_version", "endpoint"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_deprecation",
            "description": (
                "Return ONLY what changed, was added, or was deprecated for one specific "
                "(api_version, endpoint) pair, relative to its prior version. Does NOT return "
                "the current parameter table itself -- call get_openapi_spec for that. Use this "
                "when a question asks how something differs between versions, or whether a "
                "field is deprecated/replaced."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "api_version": {"type": "string", "enum": KNOWN_API_VERSIONS},
                    "endpoint": {"type": "string", "enum": KNOWN_ENDPOINTS},
                },
                "required": ["api_version", "endpoint"],
            },
        },
    },
]

TOOL_IMPLS = {
    "search_docs": search_docs,
    "get_openapi_spec": get_openapi_spec,
    "check_deprecation": check_deprecation,
}
