"""Deterministic assertion checks — Week 6, Requirement 2.

These replace judge criteria that a parser / lookup table can check for free
and never get wrong: does the code sample parse, does every SDK method /
REST endpoint the answer names actually exist in the docs, and does the
answer state which API version it's describing when the question is
version-ambiguous. None of these require an LLM call.

A 4th check (`distance_not_placeholder`) is a regression guard for Week 5's
Mode 5 finding (BM25 placeholder distance overwriting real dense distance in
RRF fusion) — it runs against retrieval results, not the answer text, so it
is not part of the "moved out of the judge" count (distance metadata was
never a judge criterion; it's a new pipeline-health guard).
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_REGISTRY_PATH = Path(__file__).parent / "symbol_registry.json"
_REGISTRY = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))

_KNOWN_SDK_SYMBOLS = set(_REGISTRY["v3_sdk_symbols"])
# Docs prose often drops the "client." prefix mid-sentence (e.g. "built on
# top of `send()`"). Accept the bare method name too, not just client.X().
_KNOWN_SDK_SYMBOLS |= {
    s.split(".", 1)[1] for s in _REGISTRY["v3_sdk_symbols"] if s.startswith("client.")
}
_KNOWN_ENDPOINT_PATHS = {e["path"] for e in _REGISTRY["rest_api_endpoints"]}
_KNOWN_ENDPOINT_NAMES = set(_REGISTRY["rest_api_named_operations"])

_CODE_FENCE_RE = re.compile(r"```(?:js|javascript)?\n(.*?)```", re.DOTALL)
_SDK_CALL_RE = re.compile(r"\b(?:client\.)?[a-zA-Z][a-zA-Z0-9_]*\s*\(\)")
_HTTP_METHOD_PATH_RE = re.compile(r"\b(GET|POST|PATCH|PUT|DELETE)\s+(/[A-Za-z0-9_/{}.\-]*)")

_NODE = shutil.which("node")


# ---------------------------------------------------------------------------
# Assertion 1 — code sample parses
# ---------------------------------------------------------------------------

def code_sample_parses(answer: str) -> dict:
    """Every fenced JS code block in the answer must be syntactically valid.

    Uses `node --check` when Node is available; otherwise falls back to a
    balanced-delimiter heuristic. An answer with no code blocks trivially
    passes (nothing to check).
    """
    blocks = _CODE_FENCE_RE.findall(answer)
    if not blocks:
        return {"name": "code_sample_parses", "passed": True, "detail": "no code blocks present"}

    for i, code in enumerate(blocks):
        if _NODE:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(code)
                path = f.name
            try:
                result = subprocess.run(
                    [_NODE, "--check", path],
                    capture_output=True, text=True, timeout=5,
                )
            finally:
                Path(path).unlink(missing_ok=True)
            if result.returncode != 0:
                return {
                    "name": "code_sample_parses", "passed": False,
                    "detail": f"block {i}: node --check failed: {result.stderr.strip()[:200]}",
                }
        else:
            if code.count("{") != code.count("}") or code.count("(") != code.count(")"):
                return {
                    "name": "code_sample_parses", "passed": False,
                    "detail": f"block {i}: unbalanced braces/parens (no node on PATH, heuristic check only)",
                }
    return {"name": "code_sample_parses", "passed": True, "detail": f"{len(blocks)} block(s) parsed clean"}


# ---------------------------------------------------------------------------
# Assertion 2 — every endpoint / SDK symbol mentioned exists in the spec
# ---------------------------------------------------------------------------

def endpoints_and_symbols_exist(answer: str) -> dict:
    """Every `method()` call and `METHOD /path` mentioned must be in the registry.

    Catches the exact Week-6 problem-statement failure: an answer citing an
    SDK method or REST path that isn't real (hallucinated symbol, or a v2/v3
    name mismatch).
    """
    unknown = []

    for match in _SDK_CALL_RE.findall(answer):
        name = match.replace(" ", "")
        if name not in _KNOWN_SDK_SYMBOLS:
            unknown.append(name)

    for method, path in _HTTP_METHOD_PATH_RE.findall(answer):
        normalized = re.sub(r"\{[^}]+\}", "{param}", path)
        known_normalized = {re.sub(r"\{[^}]+\}", "{param}", p) for p in _KNOWN_ENDPOINT_PATHS}
        if normalized not in known_normalized:
            unknown.append(f"{method} {path}")

    if unknown:
        return {
            "name": "endpoints_and_symbols_exist", "passed": False,
            "detail": f"not found in spec: {unknown}",
        }
    return {"name": "endpoints_and_symbols_exist", "passed": True, "detail": "all mentioned symbols/endpoints verified"}


# ---------------------------------------------------------------------------
# Assertion 3 — API version stated when the question is version-ambiguous
# ---------------------------------------------------------------------------

_VERSION_AMBIGUOUS_MARKERS = (
    "retry", "retries", "user-agent", "user_agent", "timeout", "default",
    "rate limit", "authentication", "auth",
)
_VERSION_STATED_MARKERS = (
    "v2", "v3", "sdk", "rest api", "octoclient", "raw rest", "octokit",
)


def api_version_stated(question: str, answer: str) -> dict:
    """If the question touches a topic documented differently for the v2 REST
    guides vs. the v3 SDK reference, the answer must say which one it means.
    """
    q_lower = question.lower()
    if any(marker in q_lower for marker in _VERSION_STATED_MARKERS):
        return {"name": "api_version_stated", "passed": True, "detail": "question already names the SDK/API version"}
    is_ambiguous = any(marker in q_lower for marker in _VERSION_AMBIGUOUS_MARKERS)
    if not is_ambiguous:
        return {"name": "api_version_stated", "passed": True, "detail": "question is not version-ambiguous"}

    a_lower = answer.lower()
    if any(marker in a_lower for marker in _VERSION_STATED_MARKERS):
        return {"name": "api_version_stated", "passed": True, "detail": "answer names the SDK/API version"}
    return {
        "name": "api_version_stated", "passed": False,
        "detail": "question could refer to the v2 REST API or the v3 SDK; answer does not say which",
    }


# ---------------------------------------------------------------------------
# Regression guard — Week 5 Mode 5 (distance metadata placeholder)
# ---------------------------------------------------------------------------

def distance_not_placeholder(retrieved_chunks: list) -> dict:
    """None of the retrieved chunks should carry the BM25 placeholder distance
    of exactly 1.0 for every result (Week 5 Mode 5: RRF `.update()` overwrote
    real dense distances). A single dense-only result legitimately near 1.0
    is fine; ALL of them being exactly 1.0 is the bug signature.
    """
    if not retrieved_chunks:
        return {"name": "distance_not_placeholder", "passed": True, "detail": "no chunks retrieved"}
    distances = [c.get("distance") for c in retrieved_chunks]
    if all(d == 1.0 for d in distances):
        return {
            "name": "distance_not_placeholder", "passed": False,
            "detail": "every retrieved chunk has distance == 1.0 (placeholder overwrite bug)",
        }
    return {"name": "distance_not_placeholder", "passed": True, "detail": "distances vary as expected"}


ASSERTIONS = [code_sample_parses, endpoints_and_symbols_exist, api_version_stated]


def run_assertions(question: str, answer: str, retrieved_chunks: list) -> list:
    results = [
        code_sample_parses(answer),
        endpoints_and_symbols_exist(answer),
        api_version_stated(question, answer),
        distance_not_placeholder(retrieved_chunks),
    ]
    return results
