import pytest
from pydantic import ValidationError

from sdk_engine.schemas import Product, VersionResolution, VersionSource, VersionStatus
from sdk_engine.versioning import detect_endpoints, is_version_sensitive, resolve_version

GH, SW = Product.GITHUB_REST, Product.SWAGGER_CODEGEN


def test_catalog_is_derived_from_corpus_metadata(catalog):
    assert catalog.versions[GH] == ("2022-11-28", "2025-06-01")
    assert catalog.versions[SW] == ("2.x", "3.x")
    assert catalog.endpoint_product["create_issue"] is GH
    assert catalog.endpoint_product["java_generator"] is SW
    assert len(catalog.endpoint_product) == 10


# ---- explicit -----------------------------------------------------------------------------
@pytest.mark.parametrize("given, version, product", [
    ("2022-11-28", "2022-11-28", GH), ("2025-06-01", "2025-06-01", GH),
    ("v3", "3.x", SW), ("3.x", "3.x", SW), ("2", "2.x", SW), ("v2.4", "2.x", SW), ("version 3.0.1", "3.x", SW),
])
def test_explicit_version_resolves(catalog, given, version, product):
    r = resolve_version("How do I do this?", catalog=catalog, explicit=given)
    assert (r.status, r.version, r.product, r.source) == (VersionStatus.RESOLVED, version, product, VersionSource.EXPLICIT)
    assert r.confidence == 1.0 and not r.needs_clarification


@pytest.mark.parametrize("given", ["2024-01-01", "4.x", "v4", "2023-06-15"])
def test_explicit_unsupported_version_is_refused_not_substituted(catalog, given):
    r = resolve_version("create an issue", catalog=catalog, explicit=given)
    assert r.status is VersionStatus.UNSUPPORTED and r.version is None and r.needs_clarification
    assert set(r.candidates) == set(catalog.all_versions)


def test_unsupported_explicit_does_not_fall_back_to_question_or_config(catalog):
    r = resolve_version("create an issue in 2025-06-01", catalog=catalog, explicit="2024-01-01",
                        config_defaults={"github_rest": "2025-06-01"})
    assert r.status is VersionStatus.UNSUPPORTED and r.version is None


# ---- from the question --------------------------------------------------------------------
@pytest.mark.parametrize("q, version, product", [
    ("My app uses API version 2022-11-28. How do I create an issue?", "2022-11-28", GH),
    ("On 2025-06-01 which parameter replaces assignee?", "2025-06-01", GH),
    ("Using Swagger Codegen v3, how do I configure the java generator?", "3.x", SW),
    ("What does the python generator do in swagger codegen 2.x?", "2.x", SW),
    ("swagger codegen v2.4 ignore file syntax?", "2.x", SW),
    ("Java version 17 with codegen 3.x, how do I set groupId?", "3.x", SW),  # stray 'version 17' ignored: a known version is present
])
def test_version_stated_in_question(catalog, q, version, product):
    r = resolve_version(q, catalog=catalog)
    assert (r.status, r.version, r.product, r.source) == (VersionStatus.RESOLVED, version, product, VersionSource.QUESTION)


def test_date_is_not_misread_as_a_semver(catalog):
    r = resolve_version("version 2025-06-01 of the releases endpoint", catalog=catalog)
    assert r.version == "2025-06-01"


@pytest.mark.parametrize("q", [
    "How do I create an issue in version 2024-01-01?", "Does the java generator in codegen v4 differ?",
])
def test_question_naming_an_undocumented_version_is_refused(catalog, q):
    r = resolve_version(q, catalog=catalog)
    assert r.status is VersionStatus.UNSUPPORTED and r.needs_clarification and r.version is None


# ---- never guess --------------------------------------------------------------------------
def test_no_version_and_version_sensitive_asks_the_user(catalog):
    r = resolve_version("How do I create an issue?", catalog=catalog)
    assert r.status is VersionStatus.UNKNOWN and r.needs_clarification and r.version is None
    assert r.source is VersionSource.NONE


def test_non_version_sensitive_question_does_not_demand_a_version(catalog):
    r = resolve_version("What is the weather in Paris today?", catalog=catalog)
    assert r.status is VersionStatus.UNKNOWN and not r.needs_clarification
    assert not is_version_sensitive("What is the weather in Paris today?", catalog)


def test_config_default_is_used_only_for_the_questions_product_and_marked_low_confidence(catalog):
    defaults = {"github_rest": "2025-06-01"}
    r = resolve_version("How do I create an issue?", catalog=catalog, config_defaults=defaults)
    assert (r.status, r.version, r.source) == (VersionStatus.RESOLVED, "2025-06-01", VersionSource.CONFIG)
    assert r.confidence < 0.8
    # A GitHub default must never be applied to a Swagger question.
    r2 = resolve_version("How do I set options for the java generator?", catalog=catalog, config_defaults=defaults)
    assert r2.status is VersionStatus.UNKNOWN and r2.needs_clarification and r2.version is None


def test_invalid_config_default_is_ignored_not_trusted(catalog):
    r = resolve_version("create an issue", catalog=catalog, config_defaults={"github_rest": "1999-01-01"})
    assert r.status is VersionStatus.UNKNOWN and r.needs_clarification


def test_explicit_conflicting_with_question_is_ambiguous_without_comparison_intent(catalog):
    r = resolve_version("In 2025-06-01 how do I create an issue?", catalog=catalog, explicit="2022-11-28")
    assert r.status is VersionStatus.AMBIGUOUS and r.needs_clarification and r.version is None


def test_two_products_named_is_ambiguous(catalog):
    r = resolve_version("What changed between 2025-06-01 and 3.x?", catalog=catalog)
    assert r.status is VersionStatus.AMBIGUOUS and r.needs_clarification


def test_several_versions_without_comparison_intent_is_ambiguous(catalog):
    r = resolve_version("2022-11-28 2025-06-01 create issue", catalog=catalog)
    assert r.status is VersionStatus.AMBIGUOUS and r.needs_clarification


# ---- comparison is the only way to authorise cross-version -------------------------------
def test_from_to_migration_resolves_to_the_from_version_and_records_both(catalog):
    r = resolve_version("How do I migrate create_issue from 2022-11-28 to 2025-06-01?", catalog=catalog)
    assert r.status is VersionStatus.RESOLVED and r.version == "2022-11-28"
    assert r.comparison_versions == ["2022-11-28", "2025-06-01"] and r.is_comparison


def test_between_phrasing_orders_lowest_first(catalog):
    r = resolve_version("What changed between 2025-06-01 and 2022-11-28 for releases?", catalog=catalog)
    assert r.version == "2022-11-28" and r.comparison_versions == ["2022-11-28", "2025-06-01"]


def test_explicit_version_plus_comparison_question(catalog):
    r = resolve_version("What changes if I upgrade to 2025-06-01?", catalog=catalog, explicit="2022-11-28")
    assert r.status is VersionStatus.RESOLVED and r.version == "2022-11-28"
    assert r.comparison_versions == ["2022-11-28", "2025-06-01"]


# Real, human-written questions from agent_race/golden_sets that the first resolver wrongly sent to
# clarification because its comparison markers were too narrow. Texts are verbatim.
@pytest.mark.parametrize("q, expected_order", [
    ("Which CLI option flag suppresses generation timestamp comments in output files across both Swagger Codegen v2 and v3?",
     ["2.x", "3.x"]),
    ("What negation syntax does the .swagger-codegen-ignore file support in Swagger Codegen v3 that is missing in v2?",
     ["2.x", "3.x"]),
    ("What top-level configuration key was introduced in Swagger Codegen v3 config files to nest generator-specific "
     "options that did not exist in v2?", ["2.x", "3.x"]),
    ("How does the make_latest parameter in GitHub API version 2025-06-01 release creation replace the undocumented "
     "heuristic used in 2022-11-28?", ["2022-11-28", "2025-06-01"]),
])
def test_real_golden_questions_with_comparison_intent_resolve_as_comparisons(catalog, q, expected_order):
    r = resolve_version(q, catalog=catalog)
    assert r.status is VersionStatus.RESOLVED and r.is_comparison and r.comparison_versions == expected_order
    assert r.version == expected_order[0] and not r.needs_clarification


def test_widened_markers_do_not_turn_an_explicit_version_conflict_into_a_comparison(catalog):
    """Negative control: 'new' is NOT a comparison marker, so a conflicting explicit version still asks."""
    r = resolve_version("In 2025-06-01 how do I create a new issue?", catalog=catalog, explicit="2022-11-28")
    assert r.status is VersionStatus.AMBIGUOUS and r.needs_clarification


@pytest.mark.parametrize("q", [
    "What is the maximum file size limit supported when rendering Markdown content via the GitHub REST API?",
    "What is the HTTP success response status code returned when creating a pull request in the GitHub REST API?",
])
def test_real_golden_questions_naming_no_version_still_ask_the_user(catalog, q):
    r = resolve_version(q, catalog=catalog)
    assert r.status is VersionStatus.UNKNOWN and r.needs_clarification


# ---- schema invariants --------------------------------------------------------------------
def test_resolved_requires_version_and_product():
    with pytest.raises(ValidationError):
        VersionResolution(status=VersionStatus.RESOLVED)


def test_unresolved_may_not_carry_a_version():
    with pytest.raises(ValidationError):
        VersionResolution(status=VersionStatus.UNKNOWN, version="2025-06-01")


@pytest.mark.parametrize("q, endpoint", [
    ("How does create_issue behave?", "create_issue"),
    ("migrate create_pull_request to the new version", "create_pull_request"),
    ("options of java_generator", "java_generator"),
    ("what is in ignore_file", "ignore_file"),
])
def test_literal_endpoint_ids_are_detected(catalog, q, endpoint):
    """Regression: 'create_issue' used to be missed because '_' is a word char, so the alias 'issue' never matched."""
    assert endpoint in detect_endpoints(q, catalog)


def test_endpoint_detection_only_returns_endpoints_in_the_corpus(catalog):
    assert "create_issue" in detect_endpoints("Which field replaces assignee?", catalog)
    assert "create_release" in detect_endpoints("what does make_latest do", catalog)
    assert detect_endpoints("quantum entanglement", catalog) == []


# ---- non-ASCII hyphens (found by the Phase 8 live run: the model writes '2022<U+2011>11<U+2011>28'; users paste dates from web pages) -------
DASHES = ["‐", "‑", "‒", "–", "—", "−"]          # hyphen, NON-BREAKING hyphen, figure dash, en dash, em dash, minus


@pytest.mark.parametrize("dash", DASHES)
def test_a_date_typed_or_pasted_with_a_non_ascii_hyphen_resolves_like_the_ascii_one(catalog, dash):
    date = dash.join(["2025", "06", "01"])
    res = resolve_version(f"On API version {date}, how do I create an issue?", catalog=catalog)
    assert res.status is VersionStatus.RESOLVED and res.version == "2025-06-01" and res.source is VersionSource.QUESTION


@pytest.mark.parametrize("dash", DASHES)
def test_an_explicit_version_with_a_non_ascii_hyphen_is_accepted(catalog, dash):
    res = resolve_version("How do I create an issue?", catalog=catalog, explicit=dash.join(["2022", "11", "28"]))
    assert res.status is VersionStatus.RESOLVED and res.version == "2022-11-28"


@pytest.mark.parametrize("dash", DASHES)
def test_a_comparison_written_with_non_ascii_hyphens_is_still_a_comparison(catalog, dash):
    a, b = dash.join(["2022", "11", "28"]), dash.join(["2025", "06", "01"])
    res = resolve_version(f"Compare creating an issue between {a} and {b}", catalog=catalog)
    assert res.is_comparison and set(res.comparison_versions) == {"2022-11-28", "2025-06-01"}


@pytest.mark.parametrize("dash", DASHES)
def test_endpoint_detection_does_not_depend_on_which_hyphen_was_typed(catalog, dash):
    for ascii_q in ("how do I make a pull-request", "how do I create-an-issue", "set the make-latest flag on a release"):
        assert detect_endpoints(ascii_q.replace("-", dash), catalog) == detect_endpoints(ascii_q, catalog)


def test_mentioned_versions_sees_every_dash_variant():
    from sdk_engine.versioning import mentioned_versions
    from conftest import PAGE_METADATA
    from sdk_engine.versioning import VersionCatalog

    cat = VersionCatalog.from_page_metadata(PAGE_METADATA)
    for dash in DASHES:
        assert mentioned_versions(f"In 2025{dash}06{dash}01 use assignees", cat) == ("2025-06-01",), repr(dash)
