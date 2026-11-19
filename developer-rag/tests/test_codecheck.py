"""Phase 6: static validation of code samples in an answer. Pure unit tests: the symbol lookup is injected, so every evidence
tier is exercised without a model, a network or an MCP server. Nothing here claims a sample would run: only that it parses and
that each identifier in it is (or is not) backed by documentation for the caller's version."""

import re

import pytest

from sdk_engine import codecheck
from sdk_engine.codecheck import (
    MAX_SYMBOLS, explain_blocked, extract_blocks, failures_for, has_hard, render_footer, required_params, validate_code,
)
from sdk_engine.schemas import Passage
from sdk_engine.symbols import Evidence, SymbolFinding

V22, V25 = "2022-11-28", "2025-06-01"
CID = "gh:p4:spec:create-an-issue:0"
# Text shaped like the REAL 2022-11-28 create-an-issue chunk (parameter table with "Required." markers, then a curl example).
DOC = ("Create an issue POST /repos/{owner}/{repo}/issues Any user with pull access to a repository can create an issue. "
       "Parameters Name In T ype Description owner path string Required. The account owner of the repository . "
       "repo path string Required. The name of the repository without the .git extension. "
       "title body string or integer Required. The title of the issue. body body string The contents of the issue. "
       "assignee body string Login for the user that this issue should be assigned to. "
       "labels body array of strings Labels to associate with this issue. "
       "Send X-GitHub-Api-Version to select the version. "
       "Example request curl -L \\ -X POST \\ -H \"Accept: application/vnd.github+json\" \\ "
       "https://api.github.com/repos/octocat/Hello-World/issues \\ -d '{\"title\":\"Found a bug\"}'")


def passage(text=DOC, cid=CID, endpoint="create_issue", version=V22):
    return Passage(chunk_id=cid, text=text, source_name="github_combined_reference.pdf", api_version=version, endpoint=endpoint)


def ev(cid="gh:p5:diff:previous-version:2", version=V25, kind="absence_statement"):
    return Evidence(kind=kind, chunk_id=cid, source_name="s", page=5, api_version=version, excerpt="x")


def found(status, symbol="x", version=V22, **kw):
    return SymbolFinding(version=version, symbol=symbol, status=status, **kw)


class Lookup:
    """Scripted symbol lookup {symbol: finding | callable(version, endpoint)}; unknown symbols are not_found. Records every call."""

    def __init__(self, table=None):
        self.table, self.calls = table or {}, []

    def __call__(self, version, symbol, endpoint):
        self.calls.append((version, symbol, endpoint))
        entry = self.table.get(symbol)
        if callable(entry):
            return entry(version, endpoint)
        return entry if entry is not None else found("not_found", symbol, version)


def fence(lang, body):
    return f"```{lang}\n{body}\n```"


def report(body, lang="json", *, lookup=None, passages=None, endpoints=(), versions=None, version=V22):
    return validate_code(fence(lang, body), version=version, versions=versions, lookup=lookup,
                         passages=[passage()] if passages is None else passages, question_endpoints=endpoints)


def finding(rep, symbol, block=0):
    return next(f for f in rep.blocks[block].findings if f.symbol == symbol)


# ------------------------------------------------------------------ extraction
def test_fenced_blocks_are_found_in_order_with_their_language_hints():
    ans = "intro\n```bash\ncurl x\n```\ntext\n~~~json\n{\"a\": 1}\n~~~\n``` \n{\"b\": 2}\n```\n"
    blocks = extract_blocks(ans)
    assert [(b.info, b.closed) for b in blocks] == [("bash", True), ("json", True), ("", True)]
    assert blocks[0].text == "curl x" and blocks[2].text == '{"b": 2}'


def test_an_answer_without_a_fenced_block_has_no_report_and_inline_code_does_not_count():
    assert validate_code("Use `assignee` as a string [x:y].", version=V22) is None
    assert not codecheck.has_code("Use `assignee`, then ``code``.")


def test_an_unterminated_fence_is_reported_not_guessed_at():
    rep = validate_code("```bash\ncurl -X POST https://api.github.com/x", version=V22, passages=[passage()])
    b = rep.blocks[0]
    assert b.syntax == "not_checked" and b.findings == [] and any("not closed" in u for u in b.unsupported_checks)


def test_language_is_taken_from_the_fence_else_sniffed_else_unknown():
    langs = lambda ans: [b.language for b in validate_code(ans, version=V22).blocks]       # noqa: E731
    assert langs("```\ncurl -X GET https://api.github.com\n```") == ["shell"]
    assert langs("```\n{\"a\": 1}\n```") == ["json"]
    assert langs("```\nsome prose\n```") == ["unknown"]
    assert langs("```PY\nx = 1\n```") == ["python"]


def test_a_language_without_a_parser_is_reported_as_not_checked_with_no_findings():
    rep = report("class A { int x; }", lang="java")
    b = rep.blocks[0]
    assert b.language == "java" and b.syntax == "not_checked" and b.findings == []
    assert any("no parser for `java`" in u for u in b.unsupported_checks)


def test_only_the_first_blocks_are_analysed_and_the_rest_are_counted():
    ans = "\n".join(fence("json", '{"title": "a"}') for _ in range(codecheck.MAX_BLOCKS + 2))
    rep = validate_code(ans, version=V22, passages=[passage()])
    assert len(rep.blocks) == codecheck.MAX_BLOCKS and rep.skipped_blocks == 2


# ------------------------------------------------------------------ python
PY = ('import requests\n'
      'r = requests.post(URL, json={"title": "x", "assignee": "octocat"},\n'
      '                  headers={"Accept": "a", "X-GitHub-Api-Version": "2022-11-28"}, timeout=10)\n')


def test_python_is_parsed_and_only_sdk_facing_names_are_extracted_not_requests_plumbing():
    rep = report(PY, lang="python")
    b = rep.blocks[0]
    assert b.syntax == "parsed"
    assert {f.symbol for f in b.findings} == {"title", "assignee", "X-GitHub-Api-Version"}      # no json/headers/timeout/Accept
    assert all(f.tier == "cited" for f in b.findings)


def test_python_imports_and_method_calls_are_never_claimed_checked():
    rep = report(PY, lang="python")
    notes = " | ".join(rep.blocks[0].unsupported_checks)
    assert "imports (requests)" in notes and "not checked" in notes
    assert "method call(s)" in notes and "signatures: not checked" in notes
    assert "execution and runtime behaviour: not performed" in notes


def test_a_python_syntax_error_is_a_hard_finding():
    rep = report("def f(:\n    pass", lang="python")
    assert rep.blocks[0].syntax == "error" and rep.hard == 1
    assert any("does not parse" in f for f in failures_for(rep, retries_left=False))


def test_keyword_arguments_of_an_unknown_client_are_extracted_for_checking():
    rep = report('client.issues.create(title="x", assignees=["a"])', lang="python", lookup=Lookup())
    assert {f.symbol for f in rep.blocks[0].findings} == {"title", "assignees"}
    assert any("method call(s)" in u for u in rep.blocks[0].unsupported_checks)


# ------------------------------------------------------------------ shell / curl
CURL = ("curl -L \\\n  -X POST \\\n  -H \"Accept: application/vnd.github+json\" \\\n  -H \"X-GitHub-Api-Version: 2022-11-28\" \\\n"
        "  https://api.github.com/repos/octocat/Hello-World/issues \\\n  -d '{\"title\":\"Found a bug\",\"assignee\":\"octocat\"}'")


def test_a_docs_style_curl_with_continuations_is_understood_and_fully_supported_by_the_cited_passage():
    rep = report(CURL, lang="bash")
    b = rep.blocks[0]
    assert b.syntax == "parsed"
    route = finding(rep, "POST /repos/octocat/Hello-World/issues")
    assert route.kind == "route" and route.tier == "cited" and route.chunk_id == CID
    kinds = {f.symbol: f.kind for f in b.findings if f.kind != "route"}
    assert kinds == {"X-GitHub-Api-Version": "header", "title": "body_key", "assignee": "body_key"}      # Accept is plumbing
    assert all(f.severity == "info" for f in b.findings) and rep.hard == rep.soft == 0


def test_a_curl_that_omits_a_parameter_the_docs_mark_required_gets_a_warning_not_a_block():
    rep = report('curl -X POST https://api.github.com/repos/o/r/issues -d \'{"assignee":"octocat"}\'', lang="bash")
    miss = [f for f in rep.blocks[0].findings if f.tier == "missing_required"]
    assert [f.symbol for f in miss] == ["title"] and miss[0].severity == "warn" and miss[0].chunk_id == CID
    assert failures_for(rep, retries_left=True) == []


def test_curl_method_defaults_to_post_with_data_get_without_and_the_query_string_is_read():
    post = report('curl https://api.github.com/repos/o/r/issues -d \'{"title":"t"}\'', lang="bash")
    assert any(f.kind == "route" and f.symbol.startswith("POST ") for f in post.blocks[0].findings)
    get = report("curl 'https://api.github.com/repos/o/r/issues?state=open&per_page=5'", lang="bash", lookup=Lookup())
    assert {f.symbol for f in get.blocks[0].findings if f.kind == "query_param"} == {"state", "per_page"}
    route = finding(get, "GET /repos/o/r/issues")           # the documents only show POST for this path
    assert route.tier == "unverified" and route.severity == "soft"


@pytest.mark.parametrize("path, ok", [("/repos/o/r/issues", True), ("/repos/o/r/issues/", True), ("/repos/o/r", False),
                                      ("/repos/o/r/issues/1", False), ("/orgs/o/r/issues", False)])
def test_route_templates_match_segment_by_segment(path, ok):
    assert codecheck._template_matches("/repos/{owner}/{repo}/issues", path) is ok


def test_the_swagger_codegen_cli_flags_are_extracted_after_the_subcommand_and_jvm_flags_are_not():
    rep = report("java -Xmx1g -jar swagger-codegen-cli.jar generate -i petstore.yaml -l java -o out --generate-markdown -DapiTests=false",
                 lang="bash", lookup=Lookup(), passages=[passage("nothing relevant")])
    assert {f.symbol for f in rep.blocks[0].findings} == {"-i", "-l", "-o", "--generate-markdown"}
    assert all(f.kind == "flag" for f in rep.blocks[0].findings)


def test_a_multi_line_quoted_json_body_is_one_argument_not_several_commands():
    """Found by the live run: the model's curl had `-d '{` ... `}'` spread over several lines. Splitting by physical line gave a
    'syntax error' and reported the JSON keys as shell commands ('command `title`')."""
    body = ("curl -L \\\n  -X POST \\\n  https://api.github.com/repos/<owner>/<repo>/issues \\\n"
            "  -d '{\n        \"title\": \"Found a bug\",\n        \"body\": \"I'\\''m having a problem.\",\n        \"assignee\": \"octocat\"\n      }'")
    rep = report(body, lang="bash")
    b = rep.blocks[0]
    assert b.syntax == "parsed" and not any(u.startswith("command ") for u in b.unsupported_checks)
    assert {f.symbol for f in b.findings if f.kind == "body_key"} == {"title", "body", "assignee"}
    assert any(f.kind == "route" and f.symbol == "POST /repos/<owner>/<repo>/issues" for f in b.findings)


def test_two_commands_on_separate_lines_are_both_understood_and_a_quoted_newline_does_not_split():
    rep = report("curl https://api.github.com/repos/o/r/issues -d '{\"title\": \"a\"}'\n"
                 "curl -X POST https://api.github.com/repos/o/r/issues -d '{\n\"title\": \"b\"}'", lang="bash")
    assert rep.blocks[0].syntax == "parsed" and len([f for f in rep.blocks[0].findings if f.kind == "route"]) == 2


def test_logical_lines_follow_shell_quoting_rules():
    ll = codecheck._logical_lines
    joined = ll("a \\\n b\nc")                                                 # continuation joins, a bare newline separates
    assert len(joined) == 2 and joined[0].split() == ["a", "b"] and joined[1] == "c"
    assert ll("a 'x\ny' b\nc") == ["a 'x\ny' b", "c"]                          # a newline inside single quotes does not separate
    assert ll('a "x\ny" b') == ['a "x\ny" b'] and ll("a 'x\ny") == ["a 'x\ny"]   # an unterminated quote swallows the rest (reported)
    assert ll("a \\'b\nc") == ["a \\'b", "c"]                                  # an escaped quote does not open a quote


def test_unbalanced_shell_quoting_is_a_warning_not_a_block():
    rep = report("curl -H \"Accept: x https://api.github.com", lang="bash")
    assert rep.blocks[0].syntax == "error" and rep.hard == 0 and rep.soft == 0
    assert failures_for(rep, retries_left=True) == []


def test_other_shell_commands_are_reported_as_not_understood():
    rep = report("npm install octokit", lang="bash")
    assert any("command `npm`" in u for u in rep.blocks[0].unsupported_checks) and rep.blocks[0].findings == []


# ------------------------------------------------------------------ evidence tiers
def test_an_identifier_shown_in_a_cited_passage_needs_no_lookup():
    lk = Lookup()
    rep = report('{"assignee": "octocat"}', lookup=lk)
    assert finding(rep, "assignee").tier == "cited" and lk.calls == []


def test_the_cited_tier_is_strict_a_longer_identifier_is_not_supported_by_a_shorter_word():
    # 'assignees' squashes into 'assigneestring', so the lenient (index) matcher would call it present; the cited tier must not
    lk = Lookup({"assignees": found("not_found", "assignees", present_in_other_versions=[V25])})
    shown = passage("Create an issue. assignee string login for the user")      # squashed: ...assigneestringlogin... contains 'assignees'
    assert codecheck.appears_in(shown.text, "assignees") is True                # the lenient matcher really is fooled here
    rep = report('{"assignees": ["octocat"]}', lookup=lk, endpoints=["create_issue"], passages=[shown])
    assert finding(rep, "assignees").tier == "other_version_only"


def test_documented_elsewhere_in_the_version_passes_with_the_chunk_that_documents_it():
    lk = Lookup({"labels2": found("present_in_docs", "labels2", evidence=[ev("gh:p9:spec:x:1", V22, "spec")])})
    f = finding(report('{"labels2": 1}', lookup=lk), "labels2")
    assert f.tier == "elsewhere_in_version" and f.severity == "info" and f.chunk_id == "gh:p9:spec:x:1"


def test_a_real_header_the_model_added_is_documented_elsewhere_not_an_error():
    """Calibration against the real corpus: X-GitHub-Api-Version IS documented for 2022-11-28 (one chunk) although the
    create-issue passage does not show it. Treating it as unsupported would be a false positive; it is a version-wide
    symbol, so it is looked up WITHOUT an endpoint scope."""
    lk = Lookup({"X-GitHub-Api-Version": found("present_in_docs", "X-GitHub-Api-Version", evidence=[ev("gh:p3:spec:api-versions:0", V22, "spec")])})
    rep = report('curl -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com', lang="bash",
                 passages=[passage("Create an issue")], lookup=lk, endpoints=["create_issue"])
    f = finding(rep, "X-GitHub-Api-Version")
    assert f.tier == "elsewhere_in_version" and f.severity == "info"
    assert lk.calls == [(V22, "X-GitHub-Api-Version", None)]


def test_deprecation_stated_in_a_cited_passage_is_surfaced_with_its_replacement():
    p = passage("assignee is deprecated in favor of assignees. assignees array")
    f = finding(report('{"assignee": "octocat"}', passages=[p]), "assignee")
    assert f.tier == "deprecated" and f.severity == "warn" and f.replacement == "assignees" and f.chunk_id == CID


def test_deprecation_stated_by_the_symbol_lookup_is_surfaced_too():
    lk = Lookup({"old_flag": found("deprecated", "old_flag", replacement="new_flag", evidence=[ev("gh:p2:spec:x:0", V22, "deprecation_statement")])})
    f = finding(report('{"old_flag": 1}', lookup=lk), "old_flag")
    assert f.tier == "deprecated" and f.replacement == "new_flag" and f.severity == "warn"


def test_a_symbol_the_next_versions_docs_say_did_not_exist_is_a_hard_finding_with_the_citation():
    lk = Lookup({"make_latest": found("absent_per_docs", "make_latest", evidence=[ev("gh:p13:diff:previous-version:5", V25)])})
    rep = report('{"make_latest": "true"}', lookup=lk)
    f = finding(rep, "make_latest")
    assert f.tier == "absent_per_docs" and f.severity == "hard" and f.chunk_id == "gh:p13:diff:previous-version:5"
    assert "did not exist in 2022-11-28" in f.detail and V25 in f.detail
    assert failures_for(rep, retries_left=False) and has_hard(rep)


def test_a_wrong_version_field_is_a_hard_finding_only_when_the_endpoint_is_known_and_scoped():
    lk = Lookup({"assignees": lambda v, ep: found("not_found", "assignees", present_in_other_versions=[V25])})
    scoped = report('{"assignees": ["octocat"]}', lookup=lk, endpoints=["create_issue"])
    f = finding(scoped, "assignees")
    assert f.tier == "other_version_only" and f.severity == "hard" and f.other_versions == [V25]
    assert (V22, "assignees", "create_issue") in lk.calls                      # the lookup really was endpoint-scoped
    unscoped = report('{"assignees": ["octocat"]}', lookup=Lookup(lk.table), endpoints=[])
    assert finding(unscoped, "assignees").severity == "soft"                    # no endpoint: weaker evidence, one retry only


def test_not_found_for_the_endpoint_but_documented_elsewhere_in_the_version_is_not_blocked():
    lk = Lookup({"state": lambda v, ep: found("not_found", "state", v) if ep else found("present_in_docs", "state", v, evidence=[ev("gh:p6:spec:x:0", V22, "spec")])})
    f = finding(report('{"state": "open"}', lookup=lk, endpoints=["create_issue"]), "state")
    assert f.tier == "elsewhere_in_version" and "not for this endpoint" in f.detail


def test_found_nowhere_is_unverified_and_soft():
    f = finding(report('{"zzz_unknown": 1}', lookup=Lookup()), "zzz_unknown")
    assert f.tier == "unverified" and f.severity == "soft"


def test_without_any_symbol_index_nothing_is_blocked_and_the_gap_is_reported():
    rep = report('{"zzz_unknown": 1}', lookup=None)
    f = finding(rep, "zzz_unknown")
    assert f.tier == "not_checked" and f.severity == "warn" and "no symbol index" in f.detail
    assert failures_for(rep, retries_left=True) == []


def test_a_failing_lookup_is_reported_as_not_checked_and_never_breaks_the_answer():
    def boom(version, symbol, endpoint):
        raise RuntimeError("index exploded")
    f = finding(report('{"zzz_unknown": 1}', lookup=boom), "zzz_unknown")
    assert f.tier == "not_checked" and "unavailable" in f.detail


def test_the_number_of_distinct_lookups_per_answer_is_bounded():
    lk = Lookup()
    keys = ", ".join(f'"k{n:02d}": 1' for n in range(MAX_SYMBOLS + 5))
    rep = report("{" + keys + "}", lookup=lk, passages=[passage("nothing")])
    assert len({s for _, s, _ in lk.calls}) == MAX_SYMBOLS
    over = [f for f in rep.blocks[0].findings if f.tier == "not_checked"]
    assert len(over) == 5 and all("limit" in f.detail for f in over)


def test_an_explicit_comparison_accepts_an_identifier_documented_in_either_version():
    lk = Lookup({"assignees": lambda v, ep: found("present_in_docs", "assignees", v, evidence=[ev("gh:p5:spec:create-an-issue:1", V25, "spec")])
                 if v == V25 else found("not_found", "assignees", v, present_in_other_versions=[V25])})
    rep = report('{"assignees": ["octocat"]}', lookup=lk, versions=[V22, V25], endpoints=["create_issue"])
    assert finding(rep, "assignees").tier == "elsewhere_in_version" and rep.hard == 0


def test_findings_from_a_hostile_server_cannot_put_a_link_in_the_visible_summary():
    evil = "gh:p1:x https://evil.example/c?d=1 ![x](https://evil.example/i.png)"
    lk = Lookup({"old_flag": found("deprecated", "old_flag", replacement="new https://evil.example", evidence=[ev(evil, V22, "deprecation_statement")]),
                 "make_latest": found("absent_per_docs", "make_latest", evidence=[ev(evil, "2025 https://evil.example")])})
    rep = report('{"old_flag": 1, "make_latest": 2}', lookup=lk)
    text = render_footer(rep) + explain_blocked(rep, "GitHub REST API", V22)
    assert "make_latest" in text                                        # the real content is still there ...
    assert "://" not in text and "![" not in text and "](" not in text  # ... but nothing in it can be a link or an image


# ------------------------------------------------------------------ required parameters
def test_required_parameters_are_read_from_the_docs_parameter_table():
    assert required_params(DOC) == [("owner", "path"), ("repo", "path"), ("title", "body")]


# ------------------------------------------------------------------ policy and wording
def test_soft_findings_count_only_while_a_retry_remains_and_hard_ones_always_do():
    rep = report('{"zzz_unknown": 1, "make_latest": 2}',
                 lookup=Lookup({"make_latest": found("absent_per_docs", "make_latest", evidence=[ev()])}))
    with_retry, without = failures_for(rep, retries_left=True), failures_for(rep, retries_left=False)
    assert len(with_retry) == 2 and len(without) == 1 and "make_latest" in without[0]


def test_the_footer_says_what_was_and_was_not_checked_and_never_claims_the_code_works():
    rep = report(CURL, lang="bash", lookup=Lookup())
    footer = render_footer(rep)
    assert "nothing was executed" in footer and "Not checked:" in footer and "runtime behaviour" in footer
    assert "syntax parsed" in footer and "block 1 (shell)" in footer
    assert not re.search(r"\b(valid|works|correct|will run|runs)\b", footer, re.I)
    assert "://" not in footer


def test_json_with_comments_is_reported_as_not_strict_json_and_never_blocks():
    """Found live: the model wrote `// ...` comments inside a ```json block. The keys are still checked; the answer is not blocked."""
    rep = report('{\n  "assignee": "octocat",   // who\n  "labels": ["bug"]  // tags\n}', lookup=Lookup())
    assert rep.blocks[0].syntax == "error" and rep.hard == rep.soft == 0
    assert {f.symbol for f in rep.blocks[0].findings if f.kind == "body_key"} == {"assignee", "labels"}
    assert "not strict JSON (comments or elisions?)" in render_footer(rep) and failures_for(rep, retries_left=True) == []


def test_the_footer_says_which_identifiers_could_not_be_checked_and_why():
    """Found by the Phase 7 incident drill: in an outage the engine shipped samples whose identifiers were unchecked, and the
    footer said nothing about them, so an unchecked identifier looked exactly like one that passed."""
    none = report('{"zzz_unknown": 1}', lookup=None)
    assert "1 identifier(s) could NOT be checked (no symbol index available): `zzz_unknown`" in render_footer(none)

    def boom(version, symbol, endpoint):
        raise RuntimeError("index down")
    down = report('{"zzz_unknown": 1, "yyy": 2}', lookup=boom)
    assert "2 identifier(s) could NOT be checked (symbol lookup unavailable)" in render_footer(down)
    assert {f.reason_code for f in down.blocks[0].findings} == {"lookup_unavailable"}
    lots = report("{" + ", ".join(f'"k{n:02d}": 1' for n in range(MAX_SYMBOLS + 2)) + "}", lookup=Lookup(), passages=[passage("nothing")])
    assert "per-answer lookup limit" in render_footer(lots) and "lookup_limit" in {f.reason_code for f in lots.blocks[0].findings}


def test_the_footer_lists_unverified_deprecated_and_missing_required_items():
    p = passage(DOC + " assignee is deprecated in favor of assignees")
    rep = report('curl -X POST https://api.github.com/repos/o/r/issues -d \'{"assignee":"o","zzz":1}\'', lang="bash", passages=[p], lookup=Lookup())
    footer = render_footer(rep)
    assert "deprecated: `assignee` (use `assignees`)" in footer and "unverified: `zzz`" in footer and "does not set required: `title`" in footer


def test_the_blocked_explanation_is_plain_for_authoritative_evidence_and_hedged_for_weak_evidence():
    lk = Lookup({"make_latest": found("absent_per_docs", "make_latest", evidence=[ev("gh:p13:diff:previous-version:5", V25)]),
                 "assignees": lambda v, ep: found("not_found", "assignees", v, present_in_other_versions=[V25])})
    rep = report('{"make_latest": 1, "assignees": ["a"]}', lookup=lk, endpoints=["create_issue"])
    text = explain_blocked(rep, "GitHub REST API", V22)
    assert "`make_latest` does not exist in 2022-11-28 [gh:p13:diff:previous-version:5]" in text
    assert "It is documented from 2025-06-01: to use it, move to 2025-06-01" in text and "or ask me for a comparison" in text   # the migration path
    assert "`assignees` is not documented for 2022-11-28 (it is documented for 2025-06-01)" in text
    assert "removed" not in text.lower()
