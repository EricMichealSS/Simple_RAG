import json

import pytest
from pydantic import ValidationError

from conftest import SYNTH, LeakyCollection, hash_embed
from sdk_engine.retrieval import (
    ChromaRetriever, RetrievalScope, ScopeViolation, build_where, enforce_scope, owner_key, upload_collection_name,
)
from sdk_engine.schemas import Passage, Product, SourceType

V22, V25 = "2022-11-28", "2025-06-01"
QUERIES = [
    "create an issue assignee", "assignees array replaces deprecated assignee 2025-06-01",
    "what did the previous version accept", "create a release make_latest", "java generator options groupId",
    "swagger codegen 3.x compared to v2", "unrelated words entirely",
]


def strict(version, **kw):
    return RetrievalScope(versions=(version,), **kw)


def retriever(col, **kw):
    return ChromaRetriever(col, hash_embed, **kw)


# ---- the filter is real: it changes what the vector store returns -------------------------
def test_unfiltered_query_does_leak_other_versions_so_the_filter_is_doing_real_work(synth_collection):
    res = synth_collection.query(query_embeddings=[hash_embed("create an issue assignee")], n_results=9)
    versions = {m.get("api_version") for m in res["metadatas"][0]}
    assert len(versions - {V22}) >= 1  # sanity: without `where`, the 2022-11-28 caller sees other versions


@pytest.mark.parametrize("version", [V22, V25, "2.x", "3.x"])
@pytest.mark.parametrize("hybrid", [True, False])
def test_strict_scope_never_returns_another_version(synth_collection, version, hybrid):
    r = retriever(synth_collection, hybrid=hybrid)
    for q in QUERIES:
        out = r.search(q, strict(version), top_k=9)
        assert all(p.api_version == version for p in out.passages), (q, [p.api_version for p in out.passages])
        assert all(not p.cross_version for p in out.passages)


def test_query_text_naming_another_version_cannot_pull_it_in(synth_collection):
    out = retriever(synth_collection).search("assignees array 2025-06-01 type field", strict(V22), top_k=9)
    assert out.passages and {p.api_version for p in out.passages} == {V22}


def test_chunks_with_missing_or_unknown_version_are_never_returned(synth_collection):
    for version in (V22, V25, "2.x", "3.x"):
        out = retriever(synth_collection).search("create an issue assignee orphan unknown", strict(version), top_k=9)
        assert not {p.chunk_id for p in out.passages} & {"bad:no-version", "bad:unknown-version"}


def test_where_filter_is_pushed_into_both_legs_via_stats(synth_collection):
    out = retriever(synth_collection).search("create an issue", strict(V22), top_k=5)
    assert out.stats["where"] == {"api_version": {"$in": [V22]}}
    assert out.stats["filtered_pool"] == 2           # only the two 2022-11-28 chunks even exist in the pool
    assert out.stats["sparse_candidates"] <= 2        # BM25 ran over the FILTERED pool, not the whole collection


# ---- comparison mode: the only authorised cross-version path -----------------------------
def test_comparison_scope_returns_both_versions_with_labels(synth_collection):
    scope = RetrievalScope(mode="comparison", versions=(V22, V25))
    out = retriever(synth_collection).search("assignee single versus assignees array", scope, top_k=9)
    versions = {p.api_version for p in out.passages}
    assert versions == {V22, V25}
    for p in out.passages:
        assert p.cross_version == (p.api_version != V22)  # labelled relative to the caller's (first) version
        assert p.api_version is not None


def test_content_type_filter_restricts_to_changelog_text(synth_collection):
    out = retriever(synth_collection).search("previous version", strict(V25, content_types=("diff",)), top_k=9)
    assert out.passages
    assert all(p.content_type == "diff" and p.source_type is SourceType.CHANGELOG for p in out.passages)


def test_passages_carry_citable_provenance(synth_collection):
    p = retriever(synth_collection).search("create an issue", strict(V25), top_k=3).passages[0]
    assert p.source_name and p.page is not None and p.api_version == V25 and p.product is Product.GITHUB_REST
    assert p.chunk_id and p.location.startswith(p.source_name)


# ---- scope validation ----------------------------------------------------------------------
@pytest.mark.parametrize("kwargs", [
    dict(versions=()), dict(versions=(V22, V25)), dict(mode="comparison", versions=(V22,)),
    dict(versions=(V22,), owner_id="u1"), dict(versions=(V22,), content_types=("spec", "bogus")),
    dict(versions=(V22,), content_types=()), dict(versions=("",)),
    dict(corpus="upload"), dict(corpus="upload", owner_id="  "), dict(corpus="upload", owner_id="u1", versions=(V22,)),
    dict(corpus="upload", owner_id="u1", mode="comparison"),
])
def test_invalid_scopes_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        RetrievalScope(**kwargs)


def test_scope_is_immutable():
    s = strict(V22)
    with pytest.raises(ValidationError):
        s.versions = (V25,)


def test_build_where_shapes():
    assert build_where(strict(V22)) == {"api_version": {"$in": [V22]}}
    assert build_where(strict(V22, content_types=("diff",))) == {
        "$and": [{"api_version": {"$in": [V22]}}, {"content_type": {"$in": ["diff"]}}]}
    assert build_where(RetrievalScope(corpus="upload", owner_id="alice")) == {"owner_id": owner_key("alice")}
    assert "alice" not in json.dumps(build_where(RetrievalScope(corpus="upload", owner_id="alice")))  # raw owner id never reaches the store


# ---- fail closed ---------------------------------------------------------------------------
def test_a_broken_store_filter_is_caught_by_the_post_check(synth_collection):
    with pytest.raises(ScopeViolation):
        retriever(LeakyCollection(synth_collection)).search("create an issue assignee", strict(V22), top_k=5)


def test_enforce_scope_rejects_wrong_corpus_and_wrong_owner():
    official = Passage(chunk_id="a", text="t", source_name="s", api_version=V22)
    with pytest.raises(ScopeViolation):
        enforce_scope([official], RetrievalScope(corpus="upload", owner_id="alice"))
    mine = Passage(chunk_id="u", text="t", source_name="s", corpus="upload", owner_id=owner_key("bob"))
    with pytest.raises(ScopeViolation):
        enforce_scope([mine], RetrievalScope(corpus="upload", owner_id="alice"))
    with pytest.raises(ScopeViolation):   # a chunk stamped with the RAW id is not a valid owner stamp either
        enforce_scope([Passage(chunk_id="u", text="t", source_name="s", corpus="upload", owner_id="alice")],
                      RetrievalScope(corpus="upload", owner_id="alice"))
    enforce_scope([Passage(chunk_id="u", text="t", source_name="s", corpus="upload", owner_id=owner_key("alice"))],
                  RetrievalScope(corpus="upload", owner_id="alice"))


# ---- limits and honesty --------------------------------------------------------------------
def test_top_k_is_clamped(synth_collection):
    out = retriever(synth_collection, max_top_k=3).search("create an issue", strict(V25), top_k=10_000)
    assert out.stats["clamped"] and out.stats["top_k"] == 3 and len(out.passages) <= 3
    assert retriever(synth_collection).search("create an issue", strict(V25), top_k=0).stats["top_k"] == 1


def test_empty_pool_returns_nothing_and_never_embeds(synth_collection):
    def boom(_):
        raise AssertionError("embedder must not be called for an empty filtered pool")
    out = ChromaRetriever(synth_collection, boom).search("anything", strict("9.x"), top_k=3)
    assert out.passages == [] and out.stats["filtered_pool"] == 0


class StubCollection:
    """Dense leg returns only chunk A; chunk B is findable only by BM25 -> its dense_distance must be None, not 1.0."""

    def get(self, where=None, include=None):
        return {"ids": ["A", "B"], "documents": ["alpha dense hit text", "zebra unique sparse token"],
                "metadatas": [{"api_version": V22, "source_file": "f", "page": 1}, {"api_version": V22, "source_file": "f", "page": 2}]}

    def query(self, query_embeddings, n_results, where=None, include=None):
        return {"ids": [["A"]], "documents": [["alpha dense hit text"]], "distances": [[0.123]],
                "metadatas": [[{"api_version": V22, "source_file": "f", "page": 1}]]}


def test_sparse_only_hits_have_no_fake_distance():
    out = ChromaRetriever(StubCollection(), lambda q: [0.0, 1.0]).search("zebra alpha", strict(V22), top_k=5)
    by_id = {p.chunk_id: p for p in out.passages}
    assert by_id["A"].dense_distance == 0.123
    assert by_id["B"].dense_distance is None  # the legacy path would have reported a placeholder 1.0 here


def test_reranker_reorders_and_is_reported(synth_collection):
    seen = {}

    def rerank(q, passages):
        seen["n"] = len(passages)
        return [float(len(p.text)) for p in passages]  # longest text first

    out = retriever(synth_collection, reranker=rerank).search("create an issue", strict(V25), top_k=3)
    assert out.stats["reranked"] and seen["n"] >= 1
    lengths = [len(p.text) for p in out.passages]
    assert lengths == sorted(lengths, reverse=True)


def test_reranker_with_wrong_score_count_is_an_error(synth_collection):
    with pytest.raises(RuntimeError):
        retriever(synth_collection, reranker=lambda q, ps: [1.0]).search("create an issue", strict(V25), top_k=3)


def test_endpoint_boost_is_a_stable_partition_that_never_drops_a_passage():
    from sdk_engine.retrieval import boost_by_endpoint

    def mk(cid, ep):
        return Passage(chunk_id=cid, text="t", source_name="s", api_version=V22, endpoint=ep)

    ps = [mk("a", "create_release"), mk("b", "create_issue"), mk("c", "render_markdown"), mk("d", "create_issue")]
    out = boost_by_endpoint(ps, ["create_issue"])
    assert [p.chunk_id for p in out] == ["b", "d", "a", "c"]            # matches first, original order kept
    assert sorted(p.chunk_id for p in out) == ["a", "b", "c", "d"]      # nothing dropped
    assert [p.chunk_id for p in boost_by_endpoint(ps, [])] == ["a", "b", "c", "d"]   # no endpoints: untouched
    assert [p.chunk_id for p in boost_by_endpoint(ps, ["nonexistent"])] == ["a", "b", "c", "d"]
    assert [p.chunk_id for p in boost_by_endpoint(ps, ["create_issue", "create_release"])] == ["a", "b", "d", "c"]


def test_upload_collection_name_is_stable_distinct_and_hides_the_owner():
    a, b = upload_collection_name("uploads_", "alice@example.com"), upload_collection_name("uploads_", "bob@example.com")
    assert a == upload_collection_name("uploads_", "alice@example.com") and a != b
    assert "alice" not in a and a.startswith("uploads_") and len(a) == len("uploads_") + 24
