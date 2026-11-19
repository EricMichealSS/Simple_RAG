import json

import pytest

from sdk_engine.redaction import redact_obj, redact_text, redact_with_counts, truncate

# Built at runtime so no secret-shaped literal sits in the repo (the CI secret scan stays clean).
GROQ = "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
OPENAI = "sk-" + "proj_abcdefghijklmnopqrstuvwx"
GH = "ghp_" + "A" * 36
JWT = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


@pytest.mark.parametrize("secret", [GROQ, OPENAI, GH, JWT, "AKIA" + "ABCDEFGHIJKLMNOP"])
def test_provider_keys_are_redacted(secret):
    out = redact_text(f"debug dump: key is {secret} end")
    assert secret not in out
    assert "[REDACTED" in out


def test_authorization_header_and_bearer():
    out = redact_text("Authorization: " + "Bearer " + "abcdefghijklmnopqrstuvwxyz0123456789")
    assert "abcdefghijklmnop" not in out and "Authorization" in out
    out2 = redact_text("curl -H 'x-api-key: supersecretvalue123' https://api")
    assert "supersecretvalue123" not in out2
    out3 = redact_text("retry with bearer abcdefghijklmnopqrstuv now")
    assert "abcdefghijklmnopqrstuv" not in out3


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("password=hunter2hunter2", "hunter2hunter2"),
        ('{"api_key": "abc123def456"}', "abc123def456"),
        ("Server=db;User=u;Password='p@ss w0rd';", "p@ss w0rd"),
        ("client_secret: s3cr3t-value", "s3cr3t-value"),
        ("token: ZZZtokenvalueZZZ", "ZZZtokenvalueZZZ"),
    ],
)
def test_secret_assignments_hide_value_keep_key(text, leaked):
    out = redact_text(text)
    assert leaked not in out
    assert "[REDACTED:secret]" in out


def test_connection_string_credentials():
    out = redact_text("DATABASE_URL=postgres://admin:S3cretPw@db.internal:5432/app")
    assert "S3cretPw" not in out and "admin" not in out
    assert "db.internal" in out  # host is kept: useful for debugging, not a secret


def test_private_key_block():
    block = "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabcdef\n-----END RSA " + "PRIVATE KEY-----"
    out = redact_text("before\n" + block + "\nafter")
    assert "MIIEow" not in out and "before" in out and "after" in out


def test_pii_shapes():
    out = redact_text("mail me at jane.doe@example.com or call 555-123-4567, SSN 123-45-6789")
    assert "jane.doe@example.com" not in out
    assert "555-123-4567" not in out
    assert "123-45-6789" not in out


def test_card_numbers_need_a_valid_luhn_checksum():
    assert "4111 1111 1111 1111" not in redact_text("card 4111 1111 1111 1111 ok")
    # Same shape, wrong checksum: NOT a card, must be left alone (avoids mangling ids/counters).
    assert "4111 1111 1111 1112" in redact_text("ref 4111 1111 1111 1112 ok")


@pytest.mark.parametrize(
    "benign",
    [
        "API version 2025-06-01", "2022-11-28", "swagger codegen 3.x", "v2.4",
        "github_combined_reference:p5:spec:create-an-issue:3", "chunk gh:p13:diff:previous-version:5",
        "max_tokens=512", '"total_tokens": 3324', "prompt_tokens=1200 completion_tokens=40",
        "Basic authentication is supported", "cost_usd=0.000627", "page 5 of 13", "request_id=3f9a1c2b7d10",
    ],
)
def test_telemetry_fields_are_not_corrupted(benign):
    assert redact_text(benign) == benign


def test_counts_report_kinds_never_values():
    res = redact_with_counts(f"key {GROQ} mail a@b.co")
    assert res.counts.get("provider-key") == 1 and res.counts.get("email") == 1
    assert GROQ not in json.dumps(res.counts)


def test_redaction_kind_labels_never_collide_with_sensitive_key_names():
    """Regression: the label 'api-key' was also a sensitive KEY, so redact_obj blanked the telemetry
    counter {'api-key': 1} and hid the very diagnostic that says a secret was found."""
    from sdk_engine import redaction

    kinds = {k for k, _, _ in redaction._RULES} | {"card"}
    assert not (kinds & redaction.SENSITIVE_KEYS)
    counts = redaction.redact_with_counts(f"key {GROQ} mail a@b.co").counts
    assert redaction.redact_obj({"redactions": counts})["redactions"] == counts


def test_redact_obj_sensitive_keys_and_nesting():
    obj = {
        "Authorization": "Bearer x", "note": f"leak {GROQ}", "n": 3, "ok": True, "none": None,
        "nested": {"password": "pw", "items": [f"a {OPENAI}", 5, {"token": "t"}]},
    }
    out = redact_obj(obj)
    blob = json.dumps(out)
    assert GROQ not in blob and OPENAI not in blob and '"pw"' not in blob and '"t"' not in blob
    assert out["n"] == 3 and out["ok"] is True and out["none"] is None


def test_redact_obj_is_depth_limited():
    deep = cur = {}
    for _ in range(60):
        cur["x"] = {}
        cur = cur["x"]
    redact_obj(deep)  # must not recurse forever / raise


def test_truncate_marks_the_cut():
    assert truncate("short", 10) == "short"
    out = truncate("x" * 50, 10)
    assert out.startswith("x" * 10) and "truncated 40 chars" in out
