"""Symbol checker. Fixture texts are VERBATIM excerpts of the real corpus (PDF extraction quirks included:
broken words like 'assi\\ngnee', collapsed spaces like 'didnotexistin2.x')."""

import pytest

from conftest import hash_embed
from sdk_engine.symbols import IndexedChunk, SymbolIndex

G1, G2, S2, S3 = "2022-11-28", "2025-06-01", "2.x", "3.x"
GH, SW = "github_combined_reference.pdf", "swagger_reference.pdf"


def C(cid, version, endpoint, ctype, page, text, src=None):
    return IndexedChunk(cid, text, version, endpoint, ctype, src or (GH if "-" in version else SW), page)


CHUNKS = [
    C("gh:p4:spec", G1, "create_issue", "spec", 4,
      "Create an issue\nPOST /repos/{owner}/{repo}/issues\nParameters\nName In T ype Description\n"
      "title body string or integer Required. The title\nof the issue.\nassignee body string Login for the user\n"
      "that this issue should\nbe assigned to.\nlabels body array of strings Labels to associate\nwith this issue."),
    C("gh:p5:spec", G2, "create_issue", "spec", 5,
      "Create an issue\nAPI version 2025-06-01\nStarting in this version, assignee is deprecated in favor of assignees (array), and a new optional\n"
      "type field lets you set the issue type at creation time instead of via a separate call.\nBody parameters\n"
      "assignees array of strings No [] Logins for the\nusers assigned\nto this issue.\nReplaces the\ndeprecated assi\ngnee field.\n"
      "type string No null The name of the\nissue type to set\non creation."),
    C("gh:p5:diff", G2, "create_issue", "diff", 5,
      "Previous version (2022-11-28)\nThe prior version of this endpoint only accepted a single assignee (string) rather than an as\n"
      "signees array, so an issue could only be assigned to one user at creation time. There was also no type field; setting an issue\n"
      "type required a second request after creation."),
    C("gh:p10:spec", G1, "create_release", "spec", 10,
      "Create a release\ntag_name string Required. The name of the tag.\ntarget_commitish string Specifies the commitish value."),
    C("gh:p12:spec", G2, "create_release", "spec", 12,
      "Create a release\nAPI version 2025-06-01\nPublishes a new release. As of this version, discussion_categ\nory_name can be supplied to "
      "automatically open a linked discussion, and make_latest gives explicit control over the Latest badge."),
    C("gh:p13:diff", G2, "create_release", "diff", 13,
      "Previous version (2022-11-28)\nThe prior version had no discussion_category_name field, so linking a discussion to a release\n"
      "required a separate manual step. There was also\nno make_latest control - whether a release showed as Latest was determined purely by a heuristic."),
    C("gh:p6:spec", G1, "create_repository", "spec", 6,
      "Create a repository for the authenticated user\nname string Required. The name.\nprivate boolean Whether the repository is private."),
    C("gh:p9:spec", G2, "create_repository", "spec", 9,
      "Create a repository for the authenticated user\nAPI version 2025-06-01\nvisibility string No public, private or internal.\n"
      "private (boolean, optional, deprecated in favor of visibility) - Default: false."),
    C("gh:p9:diff", G2, "create_repository", "diff", 9,
      "Previous version (2022-11-28)\nThe prior version had no visibility field; only the private boolean existed."),
    C("sw2:p1:spec", S2, "cli_general", "spec", 1,
      "Swagger Codegen CLI general options\nSwaggerCodegen2.xisdistributedundertheMavengroupidio.swagger.\nGeneralflags:\nFlag Description\n"
      "-i LocationoftheOpenAPIspec,asURLorfile.\n-l Clientlanguagetogenerate.\n-o Outputdirectory.\n"
      "-c PathtoaJSONconfigurationfile.\n-DhideGenerationTimestamp=true Suppressesthegenerationtimestamp\ncommentinoutputfiles."),
    C("sw3:p2:diff", S3, "cli_general", "diff", 2,
      "Compared to v2\nIn Swagger Codegen 2.x, the equivalent jar was fetched from the io.swagger Maven group\n"
      "(notio.swagger.codegen.v3),andthesame-i,-l,-o,and-cflagsbehavedidenticallyforbasic\n"
      "generation. The--generate-markdownflagdidnotexistin2.x-anyonewantingstandalone\nAPI documentation in v2 had to generate it separately"),
    C("sw3:p4:spec", S3, "python_generator", "spec", 4,
      "Python generator - config options\nv3 notes\nSame core options as v2's python generator, plus library selection between urllib3 (default,\n"
      "unchanged)andasyncio(newinv3,generatesasync/awaitclientmethods).\nOption Default Notes\n"
      "packageName swagger_client Unchangedfromv2.\nlibrary urllib3 v3addsasyncioasaselectablevalue.\n{\n\"library\": \"asyncio\"\n}"),
    C("sw3:p4:diff", S3, "python_generator", "diff", 4,
      "Compared to v2\nThev2pythongeneratordocumentedpackageName,projectName,andpackageVersionthe\nsameway-asplainkey/descriptiontext "
      "ratherthanatable-buthadnolibraryoptionatall;\neverygeneratedclientusedwhatv3nowcallsurllib3behavior.\n"
      "Therewasconsequentlynoasynciochoiceandnoasync/awaitgeneratedcodepath\nin2.x."),
    C("sw2:p3:spec", S2, "python_generator", "spec", 3,
      "python generator config options\npackageName swagger_client The package name.\nprojectName derivedfrompackageName The project name."),
]


@pytest.fixture(scope="module")
def idx(catalog):
    return SymbolIndex(CHUNKS, catalog)


def test_deprecated_in_its_own_version_with_the_replacement_the_docs_name(idx):
    f = idx.check(G2, "assignee")
    assert f.status == "deprecated" and f.replacement == "assignees"
    assert f.evidence[0].kind == "deprecation_statement" and f.evidence[0].api_version == G2


def test_two_deprecation_phrasings_are_both_recognised(idx):
    dep = {(d["deprecated"], d["replacement"]) for d in idx.deprecations_for(G2)}
    assert dep == {("assignee", "assignees"), ("private", "visibility")}  # 'X is deprecated in favor of Y' and 'X (type, optional, deprecated in favor of Y)'
    assert idx.deprecations_for(G1) == []


def test_older_version_sees_the_symbol_as_present_plus_a_labelled_upgrade_note(idx):
    f = idx.check(G1, "assignee")
    assert f.status == "present_in_docs" and f.evidence[0].api_version == G1
    assert [(n.version, n.replacement) for n in f.deprecated_in_newer_versions] == [(G2, "assignees")]
    assert f.present_in_other_versions == [G2]


def test_a_new_symbol_is_not_found_in_the_older_version_without_overclaiming(idx):
    f = idx.check(G1, "assignees")
    assert f.status == "not_found" and f.present_in_other_versions == [G2]
    assert "NOT proof" in f.note
    assert idx.check(G2, "assignees").status == "present_in_docs"


@pytest.mark.parametrize("symbol", ["make_latest", "discussion_category_name", "type", "visibility"])
def test_absence_is_claimed_only_when_the_next_versions_docs_say_so(idx, symbol):
    f = idx.check(G1, symbol)
    assert f.status == "absent_per_docs"
    ev = f.evidence[0]
    assert ev.kind == "absence_statement" and ev.api_version == G2   # evidence is from the NEWER version, and labelled
    assert idx.check(G2, symbol).status in {"present_in_docs", "deprecated"}


def test_collapsed_whitespace_pdf_text_is_handled(idx):
    f = idx.check(S2, "--generate-markdown")
    assert f.status == "absent_per_docs" and f.evidence[0].api_version == S3
    f2 = idx.check(S2, "asyncio", endpoint="python_generator")
    assert f2.status == "absent_per_docs"
    f3 = idx.check(S2, "library", endpoint="python_generator")
    assert f3.status == "absent_per_docs"
    assert idx.check(S3, "library", endpoint="python_generator").status == "present_in_docs"


def test_long_identifiers_match_across_dropped_spaces_and_short_flags_do_not_false_match(idx):
    assert idx.check(S2, "hideGenerationTimestamp").status == "present_in_docs"
    assert idx.check(S2, "-i").status == "present_in_docs"          # line-initial table token
    assert idx.check(S2, "-z").status == "not_found"                 # absent flag, no squashed-substring false positive
    assert idx.check(S2, "-g").status == "not_found"


def test_an_absence_marker_in_the_next_sentence_is_not_evidence_about_this_symbol(catalog):
    """Found by the Phase 6 graph tests: 'assignee is deprecated in favor of assignees. The type field did not exist in
    2022-11-28' made `assignees` look absent in 2022-11-28, and a code check then cited that chunk as proof. Squashing keeps
    full stops, so a marker only counts inside the symbol's own sentence (in both directions)."""
    chunks = [
        C("p:spec", G1, "ep", "spec", 1, "Create an issue\nassignee body string Login."),
        C("p:diff", G2, "ep", "diff", 2,
          "Previous version\nassignee is deprecated in favor of assignees. The type field did not exist in 2022-11-28.\n"
          "There was no widget field. Separately, assignees now take an array."),
    ]
    idx = SymbolIndex(chunks, catalog)
    assert idx.check(G1, "assignees").status == "not_found"          # not claimed absent: the marker belongs to `type`
    assert idx.check(G1, "type").status == "absent_per_docs"          # ... and the claim about `type` itself is still made
    assert idx.check(G1, "widget").status == "absent_per_docs"        # 'There was no widget field': marker before, same sentence
    assert idx.check(G1, "assignees").evidence == []


def test_deprecation_wins_over_presence_and_endpoint_filter_narrows(idx):
    assert idx.check(G2, "private", endpoint="create_repository").status == "deprecated"
    assert idx.check(G2, "assignee", endpoint="create_release").status == "not_found"   # wrong endpoint: not documented there


def test_never_reports_removed_and_status_vocabulary_is_closed(idx):
    allowed = {"present_in_docs", "deprecated", "absent_per_docs", "not_found"}
    for v in (G1, G2, S2, S3):
        for s in ("assignee", "type", "library", "nope_xyz", "-i"):
            assert idx.check(v, s).status in allowed


@pytest.mark.parametrize("bad", ["", "rm -rf /", "a b", "x" * 65, "$(whoami)", "a;b", "`id`", "../etc/passwd", "a\nb"])
def test_hostile_symbol_strings_are_rejected(idx, bad):
    with pytest.raises(ValueError):
        idx.check(G1, bad)


def test_chunks_with_unknown_or_missing_version_never_participate(catalog):
    import chromadb, uuid

    col = chromadb.EphemeralClient().create_collection(f"t_{uuid.uuid4().hex[:10]}", metadata={"hnsw:space": "cosine"})
    docs = ["orphan_param mentioned here", "orphan_param mentioned here too", "orphan_param in a real version"]
    metas = [{"source_file": "x.pdf", "page": 1, "content_type": "spec", "endpoint": "create_issue"},
             {"source_file": "x.pdf", "page": 2, "content_type": "spec", "endpoint": "create_issue", "api_version": "unknown"},
             {"source_file": "x.pdf", "page": 3, "content_type": "spec", "endpoint": "create_issue", "api_version": G2}]
    col.add(ids=["a", "b", "c"], documents=docs, metadatas=metas, embeddings=[hash_embed(d) for d in docs])
    idx = SymbolIndex.from_collection(col, catalog)
    assert idx.check(G1, "orphan_param").status == "not_found"
    assert idx.check(G2, "orphan_param").status == "present_in_docs"
    assert idx.check(G1, "orphan_param").present_in_other_versions == [G2]  # label only, from the one valid version


# ---------------------------------------------------------------------------------------------
# The same facts on the REAL index (skipped when it is not built)
# ---------------------------------------------------------------------------------------------
@pytest.mark.integration
def test_real_index_matches_the_documented_facts(catalog):
    from sdk_engine.config import EngineConfig
    from sdk_engine.retrieval import IndexMissing, open_collection

    try:
        real = SymbolIndex.from_collection(open_collection(EngineConfig.from_env()), catalog)
    except IndexMissing:
        pytest.skip("race_docs index not built")
    expect = {
        (G2, "assignee"): "deprecated", (G1, "assignee"): "present_in_docs", (G2, "assignees"): "present_in_docs",
        (G1, "type"): "absent_per_docs", (G1, "make_latest"): "absent_per_docs", (G2, "make_latest"): "present_in_docs",
        (G2, "private"): "deprecated", (G1, "visibility"): "absent_per_docs",
        (S2, "--generate-markdown"): "absent_per_docs", (S3, "--generate-markdown"): "present_in_docs",
        (S2, "asyncio"): "absent_per_docs", (S3, "asyncio"): "present_in_docs",
        (G1, "nonexistent_param_xyz"): "not_found", (S3, "nonexistent_param_xyz"): "not_found",
    }
    got = {k: real.check(*k).status for k in expect}
    assert got == expect
    assert real.check(G2, "assignee").replacement == "assignees" and real.check(G2, "private").replacement == "visibility"
