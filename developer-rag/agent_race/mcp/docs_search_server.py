"""Our own MCP server, "docs-search". Exposes the same 3 capabilities that
lived in agent_race/tools.py as plain Python functions -- but now over the
Model Context Protocol, so any MCP-speaking agent (not just ours) can
discover and call them without ever reading this file.

Run standalone for testing:
    python3 docs_search_server.py
It speaks stdio (the default transport) -- it expects to be launched as a
subprocess by an MCP client, not run as a long-lived server by hand.

Points to note for the write-up:
  - This process NEVER calls an LLM. It only ever answers "what tools do I
    have" and "here's the result of running one." The model call happens
    entirely on the host/client side (mcp_agent.py), not here.
  - check_deprecation's docstring below is the Week 9 "docstring-as-prompt"
    rewrite (Requirement 5) -- richer and example-driven, not just a one-line
    description.
  - Both get_openapi_spec and check_deprecation's validation errors are the
    Week 9 "recoverable error" rewrite (Requirement 5) -- see
    _version_error()/_endpoint_error() below, and PHASE6_error_recoverable.md
    for the before/after transcript this produced.
"""

import sys
from pathlib import Path
from typing import Literal

import chromadb
from mcp.server.fastmcp import FastMCP
from sentence_transformers import SentenceTransformer

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # .../developer-rag
CHROMA_PATH = str(REPO_ROOT / "data" / "chroma")
COLLECTION_NAME = "race_docs"

ApiVersion = Literal["2022-11-28", "2025-06-01", "2.x", "3.x"]
Endpoint = Literal[
    "render_markdown", "create_issue", "create_pull_request", "create_repository", "create_release",
    "cli_general", "java_generator", "python_generator", "ignore_file", "config_file",
]
KNOWN_API_VERSIONS = ["2022-11-28", "2025-06-01", "2.x", "3.x"]
KNOWN_ENDPOINTS = [
    "render_markdown", "create_issue", "create_pull_request", "create_repository", "create_release",
    "cli_general", "java_generator", "python_generator", "ignore_file", "config_file",
]

_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
_collection = _chroma.get_collection(COLLECTION_NAME)
_embedding_model = SentenceTransformer("BAAI/bge-small-en-v1.5")

mcp = FastMCP(name="docs-search")


# ---------------------------------------------------------------------------
# Recoverable error helpers -- KEPT AS DEFENSIVE CODE, BUT NOT THE OFFICIAL
# Week 9 "recoverable error" EVIDENCE. Real finding from testing this server
# standalone (see PHASE1_docs_search_server.md): because api_version/endpoint
# are typed as Literal[...] below, FastMCP validates arguments against that
# enum via pydantic BEFORE this function body ever runs. A bad api_version
# never reaches _version_error() at all -- it's intercepted at the protocol
# layer with a generic pydantic message instead ("Input should be
# '2022-11-28', '2025-06-01', '2.x' or '3.x'..."). That's arguably a stronger
# guarantee than an app-level recoverable message (the bad call is prevented,
# not just gracefully handled) but it means these two helpers are currently
# unreachable through the real MCP protocol path, only through calling the
# undecorated Python function directly. Kept for that defensive case; see
# check_deprecation()'s wrong-direction path below for the error rewrite that
# actually is reachable and IS this week's official before/after evidence.

def _version_error(bad_version: str) -> dict:
    return {
        "found": False,
        "error": (
            f"no docs for API/tool version '{bad_version}'. "
            f"Known versions are: {', '.join(KNOWN_API_VERSIONS)} "
            f"(2022-11-28/2025-06-01 = GitHub REST API; 2.x/3.x = Swagger Codegen). "
            f"If you were given a version outside this list, say so explicitly in your "
            f"answer instead of guessing -- do not substitute a nearby known version silently."
        ),
    }


def _endpoint_error(bad_endpoint: str) -> dict:
    return {
        "found": False,
        "error": (
            f"'{bad_endpoint}' is not one of the endpoints/generators this server has docs for. "
            f"Known ones are: {', '.join(KNOWN_ENDPOINTS)}. "
            f"Call search_docs first if you're not sure which of these matches the question."
        ),
    }


# ---------------------------------------------------------------------------
# Tool 1 — search_docs
# ---------------------------------------------------------------------------

@mcp.tool()
def search_docs(query: str, top_k: int = 5) -> list:
    """Semantically search across all migration docs (GitHub REST API and Swagger
    Codegen references) with no version/endpoint filter.

    Use this FIRST when you don't yet know which api_version or endpoint a
    question is about -- it returns candidate chunks along with their
    api_version/endpoint metadata so you can then call get_openapi_spec or
    check_deprecation precisely. Does not return a structured parameter
    table or a changelog -- just ranked passages.
    """
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

@mcp.tool()
def get_openapi_spec(api_version: ApiVersion, endpoint: Endpoint) -> dict:
    """Return the CURRENT parameter/config table for one specific
    (api_version, endpoint) pair -- field names, types, defaults, requiredness.

    Does NOT say what changed or was deprecated versus another version;
    call check_deprecation for that.
    """
    if api_version not in KNOWN_API_VERSIONS:
        return _version_error(api_version)
    if endpoint not in KNOWN_ENDPOINTS:
        return _endpoint_error(endpoint)

    got = _collection.get(
        where={"$and": [{"api_version": api_version}, {"endpoint": endpoint}, {"content_type": "spec"}]},
        include=["documents"],
    )
    if not got["ids"]:
        return {"found": False, "text": None}
    rows = sorted(zip(got["ids"], got["documents"]), key=lambda r: r[0])
    return {"found": True, "text": "\n\n".join(text for _id, text in rows)}


# ---------------------------------------------------------------------------
# Tool 3 — check_deprecation
# ---------------------------------------------------------------------------
# Week 9 Requirement 5, docstring-as-prompt half: this description is
# deliberately richer than a one-line summary -- it reads like guidance you'd
# give a new team member, with a worked example of when to reach for this
# tool vs. get_openapi_spec, because that description IS what the model
# reads to decide whether to call it. Compare against the terser version
# still in agent_race/tools.py's TOOL_SCHEMAS to see the "before."

@mcp.tool()
def check_deprecation(api_version: ApiVersion, endpoint: Endpoint) -> dict:
    """Find out what changed, was added, or was deprecated for one
    (api_version, endpoint) pair, relative to whatever came before it.

    Call this whenever a question uses words like "changed", "deprecated",
    "replaces", "used to be", or asks you to compare two versions of the
    same endpoint. Do NOT call this just to learn the current parameter
    list -- that's get_openapi_spec's job, and it will not include the
    deprecation history.

    Worked example: for the question "what parameter replaces the deprecated
    single assignee field when creating an issue in the 2025-06-01 API?",
    call check_deprecation(api_version="2025-06-01", endpoint="create_issue")
    -- always pass the NEWER of the two versions being compared. The older
    version's page never carries its own diff text (there is nothing to
    compare it against), so calling this with the older version reliably
    returns "not found" -- that is not a bug, it means you have the
    direction backwards, not that nothing changed.

    Returns only the "what changed" text -- never the full current spec.
    """
    if api_version not in KNOWN_API_VERSIONS:
        return _version_error(api_version)
    if endpoint not in KNOWN_ENDPOINTS:
        return _endpoint_error(endpoint)

    got = _collection.get(
        where={"$and": [{"api_version": api_version}, {"endpoint": endpoint}, {"has_diff": True}]},
        include=["documents"],
    )
    if not got["ids"]:
        return {
            "found": False,
            "note": (
                f"No recorded change for ({api_version}, {endpoint}). This usually means either "
                f"(a) you passed the OLDER of two versions being compared -- try the newer one instead, or "
                f"(b) nothing changed for this endpoint between versions."
            ),
        }
    rows = sorted(zip(got["ids"], got["documents"]), key=lambda r: r[0])
    return {"found": True, "text": "\n\n".join(text for _id, text in rows)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
