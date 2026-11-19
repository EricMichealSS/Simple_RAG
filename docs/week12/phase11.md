# Phase 11 — Packaging, operational quality, and the clean-checkout run

Date: 2026-11-04 · Status: **done, with named limits** · Nothing committed, pushed or deployed; no tracked file modified.

## Objective

One documented entry point; validated configuration; health checks; dependency and environment documentation (`.env.example` with placeholders, pinned requirements); a README a stranger can follow; CI that runs without secrets; and proof of each by running it, not by writing it.

## Files inspected

`sdk_engine/config.py`, `cli.py`, `factory.py`, `llm.py`, `mcp_server.py`, `mcp_client.py`, `toolbox.py`, `tool_policy.py`, `agent_race/ingest.py`, `chunkers.py` and the legacy `config.py` (the import-time key trap), `pytest.ini`, `.gitignore`, the legacy `requirements.txt`, the installed package set (`pip list`), and the OpenAI SDK defaults.

## Files created or modified (all untracked; none of them existed in git)

| file | change | purpose |
|---|---|---|
| `sdk_engine/config.py` | validated config: `ConfigError` naming the variable; 4 new variables (`LLM_BASE_URL`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT_S`, `TOP_K`); https-only provider URL without credentials; `public_view()` | a bad setting stops with a clear message instead of a traceback or a key sent over plain http |
| `sdk_engine/cli.py` | `ConfigError` → exit 2; `health` shows effective settings and `--deep`; `index check/rebuild`; `mcp-serve`; `eval` in `--help`; stdout hardening | the single documented CLI |
| `sdk_engine/indexing.py` (new) | read-only index integrity check against `page_metadata.json`; guarded rebuild (location, collection, embedding model; placeholder key for the legacy child) | the version filter is only as good as the tags |
| `sdk_engine/toolbox.py` | clamp `top_k` to the tool's maximum | found by review (a retry doubled it past the tool schema) |
| `sdk_engine/llm.py` | missing-key message names both variable spellings | accuracy |
| `sdk_eval/cli.py` | `eval trajectory`, `eval history`; full RED-check listing | Phase 9/11 |
| `sdk_engine/README.md` (new) | operator's guide, rewritten after the review | stranger-readable |
| `.env.example` (new) | every setting, placeholders only | drift-tested against `config.py` |
| `requirements-sdk-engine.txt`, `-embeddings.txt`, `-dev.txt`, `-sdk-engine.lock.txt` (new) | pinned direct dependencies; 137-package snapshot | reproducible install |
| `scripts/scan_secrets.py` (new) | stdlib tripwire for credential-shaped strings in every committable file | CI step, 10 tests |
| `.github/workflows/sdk-engine-ci.yml` (new) | secret scan, then the unit/protocol/security suite on ubuntu and windows, key variables forced empty | CI without secrets |
| `tests/conftest.py` | autouse fixture: no test may load the real `.env` | a test must never put the live key in the process |
| `tests/test_config.py` (23), `test_indexing.py` (16), `test_scan_secrets.py` (10), `test_eval_cli_extras.py` (4), `test_graph_tools.py` (+1) | new | each fix and feature, RED first |

## Connections and data flow

`cli.main` → `build_parser` → command; `EngineConfig.from_env` is called once per command and by the MCP server process (which inherits the environment, so both validate the same values). `health --deep` starts the real MCP server, discovers its tools and runs one probe search; `index check` reads the Chroma collection's metadata and compares it with `page_metadata.json`; `index rebuild` runs the unmodified legacy ingest in a child process. Data flow of a request: `architecture_target.md`.

## Commands run and actual results

| command | result |
|---|---|
| `python -m sdk_engine index check` (the real index) | ok: 39 chunks, 24/24 pages, 0 version mismatches, 0 untagged |
| `python -m sdk_engine health --deep` (the real index) | status ok; MCP start-up 23.7 s; 3 tools discovered and authorised; probe `search_docs` 95.8 ms |
| `python scripts/scan_secrets.py` | 397 files scanned, 0 suspicious lines |
| `pytest -m "not integration"` in a clean checkout (no `data/`, no `.env`) with a fresh venv built only from `requirements-dev.txt` (no torch) | **735 passed**, 11 deselected, 61 s (this was before the review fixes; later runs below) |
| `pytest` (everything) in the working tree | **749 passed** after Phase 9 (integration tests included); after the review fixes `pytest -m "not integration"` **743 passed** and `pytest -m integration` **11 passed** (754 in all, final code); detail in `final_review.md` §4 |
| YAML parse of the workflow | valid after fixing one unquoted colon the parse found |

## Clean-checkout walkthrough of the README: **incomplete, and what that means**

| attempt | what happened |
|---|---|
| 1 (checkout under a deep temp path; venv reused from the CI simulation) | `versions` worked with no index (exit 0). `index check` before any index failed clearly (exit 2, names the path, says to build it). `pip install -r requirements-embeddings.txt` **failed**: `OSError: [Errno 2] No such file or directory` — Windows' 260-character path limit, the venv sat under a ~170-character path and torch's files are deeply nested. The consequences were reported clearly rather than as tracebacks: `index rebuild` exit 2 (`ModuleNotFoundError: sentence_transformers`), `health --deep` degraded (exit 1), `query --no-llm` an `error` outcome `index_missing` (exit 1). I stopped it during `eval run`. |
| 2 (short path, brand-new venv, cold Hugging Face cache, the three install commands in order) | **Stopped by the system** during the first install (`requirements-sdk-engine.txt`): "the system is running low on memory". Free memory at that moment was 388 MB of 16 GB; the largest consumers were other applications (WSL, an editor, a browser), not this job. Two `pip` child processes it had started were still running afterwards; my attempt to stop them was **denied** by the permission classifier and I did not try another way. Per the notice I did not restart it. |

**Not verified in a clean checkout, therefore:** the three installs together (the embedding pins `torch==2.15.0`, `sentence-transformers==6.2.0`, `transformers==5.22.1` resolve in the author's own venv; a from-scratch resolution was never completed), `index rebuild` end to end with no key, a `query` after a rebuild, and `eval run` / `eval regress` in a clean checkout. The README's claim that a stranger can install, index, query and evaluate is **plausible and partly tested, not demonstrated**.

**Verified in a clean checkout earlier (the CI simulation):** from nothing, `requirements-dev.txt` (no torch) on Python 3.14.8, then the unit suite: **735 passed, 11 deselected in 61 s**; the secret scan in that checkout: 0 hits. **Verified by a targeted check:** the `index rebuild` key trap is resolved at its source: `import chunkers` with no key raises `GROK_API_KEY is missing` (control), and succeeds with the environment the rebuild now builds for the child process.

To finish the job (not done): re-run `docs/week12/evidence/phase11/` walkthrough when the machine has several GB free, from a short path.


## Defects the review of this phase found (and fixed)

From a fresh-subagent read of the README against the code (`fresh_eyes_review.md`): `index rebuild` failed for anyone without the legacy `GROK_API_KEY`; a retry's doubled `top_k` was rejected by the tool schema and silently fell back; printing to a redirected Windows stdout could raise; changing the embedding model would silently mismatch the index. Each has a test that failed first (the stdout one reproduces the crash before the fix). Also found by running things: the YAML colon; pip's 260-character path limit when the venv sits under a very deep path.

## Security and privacy considerations

`.env` is git-ignored and never read by tests (autouse fixture); `.env.example` holds placeholders and a test proves it loads as a blank configuration; `ConfigError` messages never echo a URL (it could carry a credential); the provider URL must be https (plain http only for localhost) because the key is sent there; the key is never stored on the config object; `index rebuild` passes a placeholder key to a child process only when none is set and never prints it; CI forces the key variables empty and has no secrets; the tripwire scan runs before anything is installed. Limits: the scan knows only the credential shapes it lists.

## Remaining limitations

The CI workflow has never run on GitHub (Linux leg untested); the clean-checkout run is Windows 11 / Python 3.14.8 only; the Hugging Face cache was cold but the network was fast and anonymous; the demo and the clean-checkout run use this machine's network and a free-tier provider key; exit codes across commands are not uniform (documented, not changed); `eval run --live` without a key silently skips the live cases (documented); `SDK_ENGINE_USE_RERANKER` does not reach the MCP server (documented).

## Acceptance and next steps

Single entry point, validated config, health checks, `.env.example`, pinned requirements, README, CI file, tests: **met**; "CI that runs without exposing secrets": **written and partly simulated, not run on GitHub**; "a stranger can install, index, query and evaluate": **not demonstrated** (see the walkthrough above). Next: the final documents and report.
