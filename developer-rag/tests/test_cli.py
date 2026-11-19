"""The CLI end to end on a real on-disk uploads store (deterministic 'hash-test' embedder; no key, no model)."""

import json

import pytest

import filefactory as ff
from sdk_engine import cli

ZOO = b"# Zoo notes\n\nThe zebra enclosure was repainted in March. Zebras prefer shade near the east fence.\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)          # never load a real .env (and its key) inside tests
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.setenv("SDK_ENGINE_UPLOADS_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("SDK_ENGINE_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("SDK_ENGINE_EMBEDDING_MODEL", "hash-test")
    monkeypatch.setenv("SDK_ENGINE_MAX_UPLOAD_BYTES", "200000")
    # The sufficiency distance threshold is embedder-specific and untuned; the hash test-embedder's distances are larger
    # than a real model's, so tests that exercise the happy path loosen it explicitly.
    monkeypatch.setenv("SDK_ENGINE_UPLOAD_MAX_DISTANCE", "2.0")
    return tmp_path


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_upload_list_query_delete_round_trip(env, capsys):
    f = env / "Zoo Notes.md"
    f.write_bytes(ZOO)
    code, out, _ = run(capsys, "upload", str(f), "--owner", "alice")
    assert code == 0 and out.startswith("indexed: Zoo Notes.md (md)") and "chunks=" in out
    doc_id = out.split("doc_id=")[1].split()[0]

    code, out, _ = run(capsys, "upload", str(f), "--owner", "alice")                      # same bytes again
    assert code == 0 and out.startswith("duplicate:")

    code, out, _ = run(capsys, "uploads", "list", "--owner", "alice")
    assert code == 0 and doc_id in out and "Zoo Notes.md" in out
    code, out, _ = run(capsys, "uploads", "list", "--owner", "bob")
    assert out.strip() == "no uploaded documents"                                          # private

    code, out, _ = run(capsys, "query", "What does my uploaded document say about the zebra enclosure?",
                       "--owner", "alice", "--no-llm", "--no-mcp")
    assert code == 0 and "Zoo Notes.md" in out and "your upload" in out and "[degraded]" in out and "retrieval=uploads" in out

    code, out, _ = run(capsys, "query", "What does my uploaded document say about the zebra enclosure?",
                       "--owner", "bob", "--no-llm", "--no-mcp")
    assert "Zoo Notes.md" not in out and "zebra enclosure was repainted" not in out and "[abstain]" in out

    code, out, _ = run(capsys, "uploads", "delete", doc_id, "--owner", "alice")
    assert code == 0 and f"deleted" in out and doc_id in out
    assert run(capsys, "uploads", "list", "--owner", "alice")[1].strip() == "no uploaded documents"


def test_query_json_output_is_the_structured_result(env, capsys):
    f = env / "z.md"
    f.write_bytes(ZOO)
    run(capsys, "upload", str(f), "--owner", "alice")
    code, out, _ = run(capsys, "query", "my uploaded document zebra enclosure", "--owner", "alice", "--no-llm", "--no-mcp", "--json")
    data = json.loads(out)
    assert code == 0 and data["corpus"] == "upload" and data["citations"][0]["source_name"] == "z.md" and data["citations"][0]["api_version"] is None


def test_upload_failures_have_a_category_and_a_nonzero_exit_code(env, capsys):
    bad = env / "evil.exe.md"
    bad.write_bytes(b"abc\x00def")
    code, _, err = run(capsys, "upload", str(bad), "--owner", "alice")
    assert code == 2 and err.startswith("error: not_text")
    huge = env / "huge.md"
    huge.write_bytes(b"x" * 200_001)
    code, _, err = run(capsys, "upload", str(huge), "--owner", "alice")
    assert code == 2 and "file_too_large" in err                                             # refused before being read
    code, _, err = run(capsys, "upload", str(env / "missing.md"), "--owner", "alice")
    assert code == 2 and "not a file" in err
    code, _, err = run(capsys, "upload", str(env / "evil.exe.md"), "--owner", "../etc")
    assert code == 2 and "bad_owner" in err


def test_owner_is_mandatory_for_uploads(env, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["upload", str(env / "x.md")])
    assert e.value.code == 2


def test_deleting_and_purging_are_explicit(env, capsys):
    f = env / "z.md"
    f.write_bytes(ZOO)
    run(capsys, "upload", str(f), "--owner", "alice")
    code, _, err = run(capsys, "uploads", "purge", "--owner", "alice")
    assert code == 2 and "--yes" in err and "no uploaded documents" not in run(capsys, "uploads", "list", "--owner", "alice")[1]
    code, out, _ = run(capsys, "uploads", "purge", "--owner", "alice", "--yes")
    assert code == 0 and out.startswith("purged")
    code, _, err = run(capsys, "uploads", "delete", "--owner", "alice")
    assert code == 2 and "DOC_ID" in err
    code, _, err = run(capsys, "uploads", "delete", "0123456789abcdef", "--owner", "alice")
    assert code == 2 and "not_found" in err


def test_uploaded_binary_pdf_and_docx_round_trip_through_the_cli(env, capsys):
    pdf, docx = env / "guide.pdf", env / "plans.docx"
    pdf.write_bytes(ff.make_pdf(["page one about lions", "page two about the zebra enclosure repainted in March"]))
    docx.write_bytes(ff.make_docx([("h1", "Pricing"), ("p", "The starter plan is cheap.")]))
    assert run(capsys, "upload", str(pdf), "--owner", "alice")[1].startswith("indexed: guide.pdf (pdf)")
    assert "pages=2" in run(capsys, "uploads", "list", "--owner", "alice")[1]
    assert run(capsys, "upload", str(docx), "--owner", "alice")[1].startswith("indexed: plans.docx (docx)")
    code, out, _ = run(capsys, "query", "my uploaded pdf: the zebra enclosure repainted in March", "--owner", "alice", "--no-llm", "--no-mcp")
    assert "guide.pdf p.2" in out
