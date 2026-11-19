"""The secret tripwire used by CI. Fake keys are assembled at run time so this file itself holds nothing key-shaped."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scan_secrets", ROOT / "scripts" / "scan_secrets.py")
scan_secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan_secrets)

FAKES = {
    "groq-style key": "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "anthropic-style key": "sk-ant-" + "api03-" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "openai-style key": "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
    "aws access key id": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github token": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "hugging face token": "hf_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
    "private key block": "-----BEGIN " + "RSA PRIVATE KEY-----",
}


@pytest.mark.parametrize("kind, value", sorted(FAKES.items()))
def test_each_credential_shape_is_found_and_only_a_prefix_is_reported(kind, value):
    hits = scan_secrets.scan_text(f"line one\nexport X={value}\n")
    assert (2, kind) in [(n, k) for n, k, _ in hits]                 # found, on the right line (a long key may match a second, broader shape too)
    assert all(len(p) <= 7 and value not in p for _, _, p in hits)    # a prefix only, never the value


def test_ordinary_text_placeholders_and_ignore_marked_lines_are_not_flagged():
    assert scan_secrets.scan_text("GROQ_API_KEY=\nthe key looks like gsk_ followed by letters\nsk-short\n") == []
    assert scan_secrets.scan_text(f"token = {FAKES['groq-style key']}  # secret-scan: ignore\n") == []


def test_scan_reports_relative_paths_and_skips_binary_suffixes(tmp_path):
    (tmp_path / "a.txt").write_text(f"x\n{FAKES['github token']}\n", encoding="utf-8")
    (tmp_path / "b.pdf").write_bytes(FAKES["github token"].encode())
    found = scan_secrets.scan([tmp_path / "a.txt", tmp_path / "b.pdf", tmp_path / "missing.txt"], tmp_path)
    assert [(p, n, k) for p, n, k, _ in found] == [("a.txt", 2, "github token")]


def test_the_files_this_phase_added_contain_nothing_key_shaped():
    own = [ROOT / ".env.example", ROOT / "requirements-sdk-engine.txt", ROOT / "requirements-dev.txt", ROOT / "sdk_engine" / "config.py"]
    assert scan_secrets.scan(own, ROOT) == []
