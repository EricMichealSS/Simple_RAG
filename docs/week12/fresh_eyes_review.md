# Week 12 — Fresh-eyes review

Date: 2026-11-04.

## What this is, and what it is not

**It is not an external review and not a human review.** It is a single read-only pass by a *fresh subagent instance* of the same model family, started with no access to this session's context, my conclusions or my fixes. I wrote the code and the README it reviewed, so the value of the pass is that its reader had nothing to anchor on, not that it was independent in the sense a person outside the project would be. No human has reviewed this work during this session (the repository owner was away while it was built); nothing here should be read as a human sign-off.

**Scope I gave it.** Read `sdk_engine/README.md`, `.env.example`, the three requirements files and the CI workflow; compare every checkable claim with the code; run nothing except `--help`; read no secrets; modify nothing. It had to report document line **and** code line for each mismatch and separate "confirmed wrong" from "could not verify". I did not tell it what I expected to be wrong.

**Cost and limits.** One pass, about 10 minutes, 89 tool calls. It did not run the engine, so "would fail" statements are from reading code; I checked the ones that mattered before acting on them (below). It reviewed the README-level documentation, not the technical guide, the evidence or the evaluation numbers.

## What it found, and what I did

The report listed 18 confirmed mismatches, 5 things it could not verify, and a long list of claims it confirmed. Every confirmed item was checked against the code before I acted.

| # | Finding (summary) | Verified? | Action |
|---|---|---|---|
| A1 | `index rebuild` fails for a stranger: the legacy chunker imports legacy `config.py`, which raises unless `GROK_API_KEY` is set; docs said nothing | yes (`chunkers.py:11`, `config.py:11-15`) | **code fix** (`indexing._legacy_ingest_env`: placeholder for the child process only) + 2 tests; README/`.env.example` say no key is needed; checked at the import step (`import chunkers` raises without a key and succeeds with the child environment); **not exercised end to end** (the clean-checkout run was stopped, `phase11.md`) |
| A2 | install commands use `.venv`, usage commands use bare `python`; `Scripts/` is Windows-only | yes | README: activate the venv, per-platform commands |
| A2b | embedding stack described as needed for "real answers and uploads"; it is needed for every query, `health --deep`, `eval run/regress/load` | yes (`retrieval.py:338-342`) | README and `requirements-embeddings.txt` corrected |
| A3 | README said failed verification leads to `degraded`; `degraded` only means no model/model failure | yes (`graph.py:421-439`, `649-654`) | README corrected |
| A4 | "each claim carries a citation" overstates; only ≥1 retrieved citation is enforced | yes (`answering.py:178-195`) | README corrected (and the real gap is now `known_weaknesses.md` #1) |
| A5 | exit-code rule in the README is not what the code does (invalid question exits 1, `eval` input errors exit 1, `index` failures 2, abstain/clarify/degraded 0) | yes | README states the real behaviour; **code unchanged** (accepted: inconsistent but harmless, and printed messages are authoritative) |
| A6 | only the upload size limit is a setting | yes | README corrected |
| A7 | "a bad value names the variable" is true only for numeric/URL/collection settings | yes | README and `.env.example` say which are validated |
| A8 | `SDK_ENGINE_TOP_K` documented 1–20 but the MCP tool caps at 10; the retry doubles `top_k`, so such requests were rejected and silently fell back | yes (reproduced by test) | **code fix** (`toolbox.SEARCH_TOP_K_LIMIT`, clamp) + regression test; docs corrected |
| A9 | `SDK_ENGINE_USE_RERANKER` does nothing on the default (MCP) path | yes (`factory.py:98`, `mcp_server.py:64`) | documented; **not wired through** (the reranker is off by default and gave no gain) |
| A10 | README/CI/ledger cite files that did not exist yet (`final_review.md`, `phase10.md`, `phase11.md`) and a wrong `phase2.md` pointer | yes | the files now exist; the pointer is fixed |
| A11 | `versions` reads `page_metadata.json`, not the index | yes | README corrected |
| A12 | Layout table errors (`chunking.py`, `toolbox.py`, `schemas.py`, "one model call", sdk_eval file names, missing rows) | yes | table rewritten |
| A13 | "raw questions are redacted in logs": they are not logged at all | yes | README corrected |
| A14 | version-before-retrieval holds only for the official corpus; abstain also covers out-of-scope and withheld answers; "encoding" is not checked | yes | README corrected |
| A15 | `eval run --live` without a key skips live cases and exits 0 | yes (`harness.py:149`) | documented; **code unchanged** |
| A16 | one exception to the host allowlist (a URL quoted verbatim from the user's own unflagged upload) | yes (`answering.py:303`) | README states it |
| A17 | "purges from disk" is best effort; "private" is an unauthenticated label | yes | README corrected |
| A18 | `--help` omits `eval`; `SDK_ENGINE_EMBEDDING_MODEL` is ignored by `index rebuild` (would silently mismatch); `.env` precedence and location undocumented; relative paths resolve against the cwd; threshold wording; `check_symbol` placement | yes | `eval` stub in the parser + test; rebuild now **refuses** a non-default embedding model + test; the rest documented |

Could not verify (its words) and what I did: Windows stdout encoding on redirect — **it was a real defect** (reproduced: a cp1252 stream raises on U+202F); fixed in `cli._harden_streams` with a test. CI comment "would fail loudly" was wrong (a blank key degrades quietly): comment corrected. "Unit suite needs no model or index on a clean machine": confirmed by running it in a clean checkout with a fresh venv without torch (735 passed, 11 integration tests deselected). "39 chunks": confirmed by `index check` on the real index (39 chunks, 24/24 pages). Pins existing for Python 3.14: the `requirements-dev.txt` pins installed from nothing in a clean venv; the embedding pins resolve in the author's venv but a from-scratch install was **not** completed (`phase11.md`).

Things it confirmed correct (not re-listed in full): every CLI command/flag in the README table exists; all 22 environment variables exist with matching defaults and ranges; every pin equals the lock entry; every third-party import is covered by the requirements; `.env.example` is not git-ignored and `.env` is; the CI pytest command equals the README's; the version, citation, output-safety, upload-isolation, fallback and cost behaviours the README describes match the code.

## Result

3 real code defects found by a reader who was only asked about documentation (A1, A8, stdout encoding), plus one safety guard added (A18 embedding-model refusal) and about 20 documentation errors corrected, in one pass. That is evidence the documentation had not been read against the code before; it is **not** evidence the remaining documents (the technical guide, the evidence write-ups) are accurate. A second pass on those, ideally by a person outside the project, would be worth doing and has not been done.
