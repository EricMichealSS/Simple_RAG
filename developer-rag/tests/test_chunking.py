"""The chunk loader must behave exactly like the legacy chunker it wraps, without needing a key."""

import json
import subprocess
import sys

import pytest

from conftest import ENGINE_ROOT
from sdk_engine import chunking

SAMPLE = (
    "# Authentication\n\nUse a token. It must be sent in the header.\n\n## Scopes\n\n| scope | meaning |\n|---|---|\n| read | read only |\n| write | read and write |\n\n"
    "```js\nconst client = new Client({ timeout: 5000 });\n```\n\n"
    "OVERVIEW\nThis paragraph follows an all-caps line, which PDF normalisation turns into a header.\n\n"
    + "A very long paragraph that has no breaks. " * 200
)


def legacy_output(normalize):
    code = (
        "import json, sys; sys.path.insert(0, '.'); import chunkers; "
        f"print(json.dumps(chunkers.chunk_structured({SAMPLE!r}, normalize={normalize!r})))"
    )
    env = {"GROK_API_KEY": "placeholder-not-a-secret", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PATH": __import__("os").environ.get("PATH", "")}
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ENGINE_ROOT), env=env, capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout.splitlines()[-1])


@pytest.mark.parametrize("normalize", [False, True])
def test_loader_matches_the_real_legacy_chunker_exactly(normalize):
    """Subprocess imports the REAL chunkers.py under the REAL config.py (placeholder key); we compare to our loader."""
    expected = legacy_output(normalize)
    got = chunking._legacy_module().chunk_structured(SAMPLE, normalize=normalize)
    assert [dict(c) for c in got] == expected
    assert len(expected) >= (4 if normalize else 3)   # normalisation promotes the ALL-CAPS line to a header: one more chunk


def test_constants_come_from_the_legacy_config_source_not_a_copy():
    assert chunking.legacy_chunk_constants() == {"CHUNK_SIZE": 800, "CHUNK_OVERLAP": 150, "STRUCTURED_CHUNK_SIZE": 1200}


def test_loading_does_not_leave_a_config_module_behind_or_replace_a_real_one():
    import sys as _sys

    chunking._legacy_module.cache_clear()
    _sys.modules.pop("config", None)
    chunking._legacy_module()
    assert "config" not in _sys.modules
    marker = type(_sys)("config")
    _sys.modules["config"] = marker
    try:
        chunking._legacy_module.cache_clear()
        chunking._legacy_module()
        assert _sys.modules["config"] is marker          # a pre-existing module is restored, not clobbered
    finally:
        _sys.modules.pop("config", None)
        chunking._legacy_module.cache_clear()


def test_structure_is_preserved_tables_and_code_stay_whole():
    chunks = chunking.chunk_structured(SAMPLE, normalize=False)
    table = [c for c in chunks if "| scope | meaning |" in c["text"]]
    assert len(table) == 1 and "| write | read and write |" in table[0]["text"]
    code = [c for c in chunks if "```js" in c["text"]]
    assert len(code) == 1 and "timeout: 5000" in code[0]["text"] and code[0]["text"].count("```") == 2


def test_no_stored_chunk_exceeds_the_hard_cap():
    chunks = chunking.chunk_structured("word " * 20000, normalize=False)
    assert chunks and all(len(c["text"]) <= chunking.MAX_CHUNK_CHARS for c in chunks)
