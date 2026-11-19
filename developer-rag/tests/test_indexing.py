"""Index integrity check and the guarded rebuild. The check is read-only; the rebuild deletes and re-creates the official collection, so it needs --yes
and refuses to touch anything but the location the legacy ingest script writes to."""

import json

import pytest

from sdk_engine import cli, indexing
from sdk_engine.config import ENGINE_ROOT, EngineConfig

EXPECTED = {("doc_a", 1): "2022-11-28", ("doc_a", 2): "2022-11-28", ("doc_b", 1): "3.x"}


def md(source, page, version="2022-11-28", **extra):
    return {"source_file": f"{source}.pdf", "page": page, "api_version": version, **extra}


def test_a_consistent_index_is_ok_and_counts_chunks_per_version():
    r = indexing.check_metadatas(EXPECTED, [md("doc_a", 1), md("doc_a", 1), md("doc_a", 2), md("doc_b", 1, "3.x")])
    assert r["ok"] and r["chunks"] == 4 and r["by_version"] == {"2022-11-28": 3, "3.x": 1}
    assert r["pages_expected"] == 3 and r["pages_indexed"] == 3 and r["problems"] == []


def test_a_chunk_tagged_with_the_wrong_version_is_a_problem_because_it_would_defeat_the_version_filter():
    r = indexing.check_metadatas(EXPECTED, [md("doc_a", 1), md("doc_a", 2), md("doc_b", 1, "2022-11-28")])
    assert not r["ok"] and r["version_mismatches"] == [{"source": "doc_b", "page": 1, "indexed_as": "2022-11-28", "expected": "3.x"}]
    assert any("version" in p for p in r["problems"])


def test_untagged_chunks_missing_pages_and_unknown_pages_are_each_reported():
    r = indexing.check_metadatas(EXPECTED, [md("doc_a", 1), {"source_file": "doc_a.pdf", "page": 2}, md("doc_z", 9)])
    assert not r["ok"]
    assert r["untagged_chunks"] == 1 and r["missing_pages"] == [{"source": "doc_b", "page": 1}] and r["unexpected_pages"] == [{"source": "doc_z", "page": 9}]


def test_an_empty_index_is_not_ok():
    r = indexing.check_metadatas(EXPECTED, [])
    assert not r["ok"] and r["chunks"] == 0 and any("empty" in p for p in r["problems"])


def test_expected_pages_are_read_from_the_hand_authored_page_metadata(tmp_path):
    path = tmp_path / "page_metadata.json"
    path.write_text(json.dumps({"_comment": "x", "doc_a.pdf": {"1": {"api_version": "2022-11-28"}, "2": {"api_version": "2022-11-28"}},
                                "doc_b.pdf": {"1": {"api_version": "3.x"}}, "known_endpoints": ["create_issue"]}), encoding="utf-8")
    assert indexing.expected_pages(path) == EXPECTED


def test_the_real_page_metadata_yields_a_page_map_with_four_versions():
    pages = indexing.expected_pages(EngineConfig().page_metadata_path)
    assert len(pages) >= 20 and len(set(pages.values())) == 4


# ------------------------------------------------------------------ the guarded rebuild
def test_a_rebuild_is_refused_for_a_location_the_legacy_ingest_does_not_write_to(tmp_path):
    cfg = EngineConfig(chroma_path=tmp_path / "elsewhere")
    with pytest.raises(indexing.RebuildRefused) as e:
        indexing.rebuild(cfg, runner=lambda: pytest.fail("must not run"))
    assert "SDK_ENGINE_CHROMA_PATH" in str(e.value)
    with pytest.raises(indexing.RebuildRefused) as e2:
        indexing.rebuild(EngineConfig(official_collection="other_docs"), runner=lambda: pytest.fail("must not run"))
    assert "SDK_ENGINE_COLLECTION" in str(e2.value)


def test_a_rebuild_at_the_default_location_runs_the_ingest_once_and_reports_its_failure():
    calls = []
    indexing.rebuild(EngineConfig(), runner=lambda: calls.append(1) or 0)
    assert calls == [1]
    with pytest.raises(indexing.RebuildFailed):
        indexing.rebuild(EngineConfig(), runner=lambda: 3)


# ------------------------------------------------------------------ the CLI wiring
def test_the_cli_refuses_a_rebuild_without_yes_and_does_not_run_anything(monkeypatch, capsys):
    monkeypatch.setattr(indexing, "rebuild", lambda *a, **k: pytest.fail("must not run"))
    assert cli.main(["index", "rebuild"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_a_bad_setting_is_reported_without_a_traceback_and_names_the_variable(monkeypatch, capsys):
    monkeypatch.setenv("SDK_ENGINE_TOP_K", "banana")
    assert cli.main(["versions"]) == 2
    err = capsys.readouterr().err
    assert "SDK_ENGINE_TOP_K" in err and "Traceback" not in err


def test_health_lists_the_effective_settings_without_the_key(monkeypatch, capsys):
    secret = "gsk_" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2"
    monkeypatch.setenv("GROQ_API_KEY", secret)
    cli.main(["health"])
    out = capsys.readouterr().out
    assert secret not in out and '"llm_key_present": true' in out and '"top_k"' in out


def test_the_mcp_serve_command_is_registered():
    assert cli.build_parser().parse_args(["mcp-serve"]).command == "mcp-serve"
    assert ENGINE_ROOT.name == "developer-rag"


# ------------------------------------------------------------------ found by review: the rebuild's hidden prerequisites
def test_the_legacy_ingest_child_gets_a_placeholder_key_only_when_none_is_set():
    assert indexing._legacy_ingest_env({})["GROK_API_KEY"] == indexing._PLACEHOLDER_KEY                  # a stranger with no key can still index
    assert indexing._legacy_ingest_env({"GROQ_API_KEY": "gsk_" + "x" * 24})["GROK_API_KEY"] == "gsk_" + "x" * 24      # the provider's name is mapped
    assert indexing._legacy_ingest_env({"GROK_API_KEY": "legacy-value"})["GROK_API_KEY"] == "legacy-value"    # an existing key is untouched
    assert indexing._legacy_ingest_env({"GROK_API_KEY": "  "})["GROK_API_KEY"] == indexing._PLACEHOLDER_KEY    # blank counts as unset


def test_a_rebuild_is_refused_when_the_configured_embedding_model_is_not_the_one_the_ingest_uses():
    with pytest.raises(indexing.RebuildRefused) as e:
        indexing.rebuild(EngineConfig(embedding_model="some/other-model"), runner=lambda: pytest.fail("must not run"))
    assert "SDK_ENGINE_EMBEDDING_MODEL" in str(e.value)


# ------------------------------------------------------------------ found by review: the CLI itself
def test_help_lists_the_eval_command_and_eval_still_reaches_the_evaluation_cli(monkeypatch, capsys):
    assert "eval" in cli.build_parser().format_help()
    seen = []
    monkeypatch.setattr("sdk_eval.cli.main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["eval", "list"]) == 0 and seen == [["list"]]


def test_printing_a_character_the_console_code_page_lacks_does_not_crash(monkeypatch):
    import io

    narrow_nbsp = "size.\u202f[chunk] \u201cquoted\u201d \u2192"
    raw = io.BytesIO()
    fake = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")            # what a redirected Windows stdout is
    monkeypatch.setattr("sys.stdout", fake)
    with pytest.raises(UnicodeEncodeError):
        print(narrow_nbsp, file=fake, flush=True)                               # the defect, reproduced
    cli._harden_streams()
    print(narrow_nbsp, flush=True)
    assert b"[chunk]" in raw.getvalue()
