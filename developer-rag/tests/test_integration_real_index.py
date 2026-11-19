"""Integration: the REAL race_docs index and REAL local embedding model. No API key, no LLM.

Skipped automatically when the index has not been built. Build it with:
    python agent_race/ingest.py        (see docs/week12/baseline_results.md §5a)

This is the permanent form of the Phase 2 measurement: the pre-Week-12 retriever returned a
different-version chunk for 51% of returned chunks on these same 20 queries.
"""

import json

import pytest

from sdk_engine.config import EngineConfig
from sdk_engine.retrieval import ChromaRetriever, IndexMissing, RetrievalScope, make_embed_fn, open_collection

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def retriever():
    cfg = EngineConfig.from_env()
    try:
        col = open_collection(cfg)
    except IndexMissing:
        pytest.skip("race_docs index not built")
    return ChromaRetriever(col, make_embed_fn(cfg.embedding_model))


@pytest.fixture(scope="module")
def pairs():
    cfg = EngineConfig.from_env()
    meta = json.loads(cfg.page_metadata_path.read_text(encoding="utf-8"))
    found = set()
    for fname, pages in meta.items():
        if fname.startswith("_") or fname.startswith("known_"):
            continue
        for pm in pages.values():
            found.add((pm["api_version"], pm["endpoint"]))
    return sorted(found)


def test_no_wrong_version_chunk_for_any_corpus_pair(retriever, pairs):
    assert len(pairs) == 20
    wrong = returned = empty = 0
    for version, endpoint in pairs:
        q = f"{endpoint.replace('_', ' ')} API version {version} parameters"
        out = retriever.search(q, RetrievalScope(versions=(version,)), top_k=5)
        returned += len(out.passages)
        empty += not out.passages
        wrong += sum(p.api_version != version for p in out.passages)
    assert returned > 0 and empty == 0, "a filter that returns nothing is trivially safe; it must also retrieve"
    assert wrong == 0


def test_the_right_endpoint_is_retrieved_within_the_version(retriever, pairs):
    """Safety is not enough: the in-scope result must actually contain the endpoint asked about."""
    hits = 0
    for version, endpoint in pairs:
        q = f"{endpoint.replace('_', ' ')} API version {version} parameters"
        out = retriever.search(q, RetrievalScope(versions=(version,)), top_k=5)
        hits += any(p.endpoint == endpoint for p in out.passages)
    assert hits / len(pairs) >= 0.9, f"endpoint recall within version fell to {hits}/{len(pairs)}"


def test_deprecation_text_for_a_newer_version_is_reachable_only_through_explicit_scope(retriever):
    # strict scope for the OLD version must not return the 2025-06-01 'what changed' paragraph ...
    old = retriever.search("assignee deprecated replaced by assignees", RetrievalScope(versions=("2022-11-28",)), top_k=5)
    assert all(p.api_version == "2022-11-28" for p in old.passages)
    assert not any("deprecated in favor of assignees" in p.text for p in old.passages)
    # ... but an explicit, labelled comparison scope can reach it.
    cmp_ = retriever.search(
        "assignee deprecated replaced by assignees",
        RetrievalScope(mode="comparison", versions=("2022-11-28", "2025-06-01")), top_k=5)
    new_side = [p for p in cmp_.passages if p.api_version == "2025-06-01"]
    assert new_side and all(p.cross_version for p in new_side)


# ------------------------------------------------------------------ Phase 6: code samples checked against the REAL corpus
@pytest.fixture(scope="module")
def code_check(retriever):
    from sdk_engine import codecheck
    from sdk_engine.symbols import SymbolIndex
    from sdk_engine.versioning import VersionCatalog

    cfg = EngineConfig.from_env()
    idx = SymbolIndex.from_collection(open_collection(cfg), VersionCatalog.from_page_metadata(cfg.page_metadata_path))

    def run(version, endpoint, question, code, lang="json"):
        passages = retriever.search(question, RetrievalScope(versions=(version,)), top_k=5).passages
        return codecheck.validate_code(f"```{lang}\n{code}\n```", version=version, passages=passages,
                                       lookup=lambda v, s, e: idx.check(v, s, e), question_endpoints=[endpoint] if endpoint else [])
    return run


def _tier(rep, symbol):
    return next(f for f in rep.blocks[0].findings if f.symbol == symbol)


def test_real_corpus_a_2025_field_in_a_2022_sample_is_blocked_and_the_2022_fields_are_not(code_check):
    rep = code_check("2022-11-28", "create_issue", "how do I set the assignee when creating an issue",
                     '{"title": "Found a bug", "assignee": "octocat", "assignees": ["octocat"]}')
    wrong = _tier(rep, "assignees")
    assert wrong.tier == "other_version_only" and wrong.severity == "hard" and wrong.other_versions == ["2025-06-01"]
    assert _tier(rep, "title").severity == "info" and _tier(rep, "assignee").severity == "info"


def test_real_corpus_a_field_the_next_version_says_did_not_exist_is_blocked_with_a_citation(code_check):
    rep = code_check("2022-11-28", "create_release", "create a release", '{"tag_name": "v1", "make_latest": "true"}')
    f = _tier(rep, "make_latest")
    assert f.tier == "absent_per_docs" and f.severity == "hard"
    assert f.chunk_id and f.chunk_id.startswith("github_combined_reference:p13:diff")   # the 2025-06-01 'previous version' page
    assert _tier(rep, "tag_name").severity == "info"


def test_real_corpus_the_version_header_is_documented_for_2022_11_28_and_is_not_an_error(code_check):
    """The case that started Phase 6: the model added X-GitHub-Api-Version although the create-issue passage does not show it.
    The index documents it for 2022-11-28, so it must be reported as supported, never as a failure."""
    rep = code_check("2022-11-28", "create_issue", "create an issue curl example",
                     'curl -X POST -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com/repos/octocat/Hello-World/issues '
                     '-d \'{"title": "Found a bug"}\'', lang="bash")
    f = _tier(rep, "X-GitHub-Api-Version")
    assert f.severity == "info" and f.tier in ("cited", "elsewhere_in_version")
    assert rep.hard == 0


def test_real_corpus_the_docs_own_curl_example_validates_clean_including_its_route(code_check):
    rep = code_check("2022-11-28", "create_issue", "create an issue curl example",
                     'curl -L -X POST -H "Accept: application/vnd.github+json" https://api.github.com/repos/octocat/Hello-World/issues '
                     '-d \'{"title": "Found a bug", "body": "I\'\'m having a problem."}\'', lang="bash")
    route = next(f for f in rep.blocks[0].findings if f.kind == "route")
    assert route.tier == "cited" and route.severity == "info" and rep.hard == 0
    assert not [f for f in rep.blocks[0].findings if f.tier == "missing_required"]


def test_real_corpus_a_cli_flag_is_blocked_for_the_version_that_lacked_it_and_fine_for_the_one_that_has_it(code_check):
    cmd = "java -jar swagger-codegen-cli.jar generate -i petstore.yaml -l java -o out --generate-markdown"
    old = code_check("2.x", None, "swagger codegen generate command line", cmd, lang="bash")
    assert _tier(old, "--generate-markdown").tier == "absent_per_docs" and old.hard == 1
    new = code_check("3.x", None, "swagger codegen generate command line", cmd, lang="bash")
    assert _tier(new, "--generate-markdown").severity == "info" and new.hard == 0
