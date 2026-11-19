# SDK docs answer engine (Week 12)

Answers a developer's question about an SDK/API **for the version they use**, with citations, and says so when it cannot.
It is a LangGraph pipeline over a version-tagged vector index, with its own MCP tool server, code validation, private
user uploads, one trace id per request, and an integrated evaluation suite.

This README is the operator's guide. The evidence behind every claim, and everything that is **not** proven, is in
[`docs/week12/`](../../docs/week12/): start with `known_weaknesses.md`, `final_review.md` and `gap_analysis.md` (the ledger), and
`technical_implementation_guide.md` for how the code works. Nothing here is a production-readiness claim.

## What it does, in one request

1. **Validate** the question (control characters stripped; empty, over-long or non-text questions and bad owner ids or scopes are
   refused). For the **official corpus**, resolve the **version before any retrieval**: from `--version`, from the question text,
   or from an operator-configured default for the product the question names. If it cannot tell which supported version you mean it
   asks (`clarify`); it never picks a version for you silently. A question with no documented product or version at all is
   out of scope and abstains. (Questions about your own uploads skip version resolution: they have no SDK version.)
2. **Retrieve** with the version as a metadata filter *inside* the vector-store query, through the MCP tool server (`search_docs`,
   and `lookup_changes` for deprecations/changes). If a tool call fails, the in-process retriever serves the request and the trace says
   so. Tool choice is rule-based and made by the graph, not by the model.
3. **Generate** one answer with the model from the retrieved passages (one model call; a second only if verification asks for a correction).
   Retrieved text and uploads are treated as **data**, never as instructions.
4. **Verify**: the answer must cite at least one retrieved chunk, and every cited id is checked to be a retrieved chunk in an allowed version;
   version claims in the text are checked; code samples are checked against the docs for *your* version (using the `check_symbol` tool:
   deprecated / absent / only in another version / could not be checked); unsafe output is withheld (a leak of the system prompt, a key-shaped
   value that is not in the sources, a link or embedded resource on a host the operator has not allowed; the one exception is a URL quoted
   verbatim from your own unflagged upload). A failed check retries once and then abstains; a safety failure abstains at once.
   **It does not check that a cited chunk actually supports the claim** (see `known_weaknesses.md` #1).

Outcomes: `answered`; `abstain` (the docs do not say, the question is out of scope, or a check withheld the answer); `clarify` (needs a
version or a scope); `degraded` (returned **only** when no model is configured or the model call fails, for example a provider quota: cited
excerpts instead of an answer); `error` (invalid request or an internal failure, with a category).

## Install

Measured on Python 3.14.8 / Windows 11. From the repository's `developer-rag/` directory, create and **activate** a virtual environment, then
install; every later command assumes it is active (`python` is the venv's).

```
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell      (Git Bash: source .venv/Scripts/activate)
source .venv/bin/activate           # Linux / macOS           (the CI workflow also runs on ubuntu; that leg has not been run yet)

python -m pip install -r requirements-sdk-engine.txt     # the engine, MCP, LangGraph, Chroma, upload parsers
python -m pip install -r requirements-embeddings.txt     # local embeddings (large: torch). NEEDED for every query, even --no-llm
python -m pip install -r requirements-dev.txt            # tests
```

**Windows:** keep the checkout path short. `pip install -r requirements-embeddings.txt` failed with `OSError: [Errno 2] No such file or directory`
when the virtual environment sat under a ~170-character path, because torch's nested files exceed Windows' 260-character limit (the repository's own venv, at a short path, has torch installed; enabling Windows long-path support is
the other fix).

Retrieval embeds the question locally, so `requirements-embeddings.txt` is required for **every** `query` (including `--no-llm`), for
`health --deep`, for `index rebuild`, for uploads, and for every `eval` command except `eval list`. Only the unit test suite runs without it
(it uses a deterministic fake embedding). `requirements-sdk-engine.lock.txt` is a full snapshot (137 packages) of the environment the suite
was measured in: a reference, not an install file. The first run downloads the embedding model from the Hugging Face hub.

Settings: copy `.env.example` to `.env` (git-ignored, read only from `developer-rag/.env`; a variable already set in the environment wins
over `.env`) and fill in what you need. **Numeric, URL and collection-name settings are validated at start-up** and a bad value stops the
command with a message naming the variable; boolean flags treat anything but `1/true/yes/on` as off, and models and paths are not
checked until used. Not every setting has an environment variable (page/chunk limits and similar are fixed in `config.py`). The key is read
lazily, is never stored on the config object, and is never printed (`health` reports only whether one is present).

## Build or check the index

The official index (`data/chroma`, collection `race_docs`: 4 versions across GitHub REST 2022-11-28 / 2025-06-01 and Swagger
Codegen 2.x / 3.x) is git-ignored and rebuilt from the three PDFs in `agent_race/documents/`:

```
python -m sdk_engine index check             # read-only: every chunk's version tag vs agent_race/page_metadata.json
python -m sdk_engine index rebuild --yes     # DELETES and re-creates the collection (runs agent_race/ingest.py)
```

`rebuild` refuses a non-default location, collection or embedding model (the ingest script hard-codes where it writes and what it embeds
with). It needs no provider key (the legacy chunker insists on one being set; the command supplies a placeholder to the child process only).

## Use it

```
python -m sdk_engine query "How do I create an issue with several assignees?" --version 2025-06-01
python -m sdk_engine query "Which codegen flag sets the Java package?" --version 3.x --json
python -m sdk_engine query "..." --no-llm            # retrieval only: cited excerpts, no model call, no key (still needs the embedding stack and the index)
python -m sdk_engine versions                        # the versions and endpoints in agent_race/page_metadata.json (does not open the index)
python -m sdk_engine health                          # settings, index, catalog, key presence (never the key)
python -m sdk_engine health --deep                   # + index integrity, starts the MCP server, discovers its tools, one probe search
```

| command | what it does |
|---|---|
| `query` | answer one question (`--version`, `--owner`, `--scope auto/official/upload`, `--no-llm`, `--no-mcp`, `--json`) |
| `versions` | list documented versions and endpoints |
| `health [--deep]` | check settings, index, catalog, key presence; `--deep` also checks integrity and the MCP server end to end |
| `index check` / `index rebuild --yes` | verify / rebuild the official index |
| `mcp-serve` | run the MCP tool server on stdio (the engine spawns its own copy; you rarely need this) |
| `upload FILE --owner ID` | add a PDF/DOCX/Markdown/text file to **your** uploads collection |
| `uploads list\|delete DOC_ID\|purge --owner ID` | manage your uploads (`purge` needs `--yes`) |
| `trace find\|show ID\|stats` | find a request by time, version, tool, status, code finding, cost; see one request's span tree across the MCP hop |
| `eval list\|run\|judge\|label-sheet\|agreement\|regress\|golden\|trajectory\|load\|history` | the integrated evaluation suite (`eval --help`) |

Exit codes (the printed message is authoritative; the commands were written one at a time): `0` the command ran, **including** `abstain`,
`clarify` and `degraded` query outcomes (read the printed `[outcome]` line); `1` a `query` that ended in an `error` outcome (this includes an
invalid question), a `health` or `index check` that found a problem, or an `eval` command that failed; `2` bad command-line input or a bad
setting, an upload file error, or an `index` command that could not run.

## Private uploads

`--owner ID` selects *your* uploads: a SQLite store physically separate from the official index (and not opened by the MCP server process).
Another owner's documents are never retrieved and the official corpus is never mixed with yours in one answer. This is isolation between
**labels, not people**: there is no authentication, so anyone who passes the same `--owner` sees the same documents. Deleting a document
purges its text from disk by overwriting and `VACUUM` (verified by tests that read the database file); the command says plainly when that
best effort fails. Uploaded text is untrusted data; instruction-like content is flagged but cannot change what the engine does. The
file-size limit is a setting (`SDK_ENGINE_MAX_UPLOAD_BYTES`); the page, chunk and per-owner limits are fixed in `config.py`.

## Tracing

Every request has one trace id from entry through the graph, retriever and the MCP hop (the server continues the client's trace). Spans go to
`data/traces/` (`spans.jsonl`, `server_spans.jsonl`, plus the MCP server's stderr log); `trace show ID` prints the tree with a per-stage cost
table. **The question and answer text are never written to a span**, only lengths, counts and labels, and everything persisted also passes
through redaction. Cost is `calculated` from a priced model or `unavailable` for an unpriced one, never guessed.

## Tests and evaluation

```
python -m pytest -m "not integration" -q      # unit, protocol, security: no key, no model, no index (this is what CI runs)
python -m pytest -q                            # + integration tests against the real index and the local embeddings
python -m sdk_engine eval list                 # the cases: tier, category, failure modes, origin (needs nothing)
python -m sdk_engine eval run                  # offline tier: no key, but needs the index and the embedding stack
python -m sdk_engine eval run --live           # + the live cases; WITHOUT a key they are silently skipped and the exit code is still 0
python -m sdk_engine eval regress              # RED/GREEN: each past defect re-created, then the current code
```

The live tiers call a paid API and are **never** part of CI. The provider key's quotas are real: on the free tier used here the binding limit
is 200,000 tokens per day (about 109 answered questions), reached during the evaluation (`docs/week12/tenx.md`). `eval judge` and `eval
agreement` grade live outputs with a model judge and compare it with hand labels; until a human has labelled, agreement is reported as *not
measured*.

## Layout

| module | role |
|---|---|
| `graph.py` | the LangGraph state machine and every routing decision |
| `schemas.py` | the shared Pydantic contracts: version resolution, passages, citations, answer result, route decisions, code findings, usage |
| `versioning.py` | version catalog, resolution, endpoint/product detection |
| `retrieval.py`, `indexing.py` | version-scoped retrieval; index integrity check and guarded rebuild (the official index is chunked by the legacy `chunkers.py` through `agent_race/ingest.py`) |
| `mcp_server.py`, `mcp_client.py`, `tool_policy.py`, `toolbox.py` | the tool server; the client with discovery, allowlist and schema validation; the graph's gate around each call (it raises `ToolFailure`, and the graph falls back) |
| `factory.py` | wiring, including the lazy in-process fallbacks (retriever, symbol index, upload store) |
| `answering.py`, `codecheck.py`, `symbols.py`, `injection.py`, `redaction.py` | prompt, citation/output checks, code validation, injection signals, redaction |
| `uploads.py`, `extract.py`, `chunking.py`, `sqlite_store.py` | private uploads (`chunking.py` wraps the legacy chunker for uploads only) |
| `llm.py`, `pricing.py` | the model call (up to two per request) and cost accounting |
| `trace.py`, `tracequery.py` | spans, request summaries, `trace` queries |
| `config.py`, `cli.py`, `__main__.py` | validated settings; the single CLI; `python -m sdk_engine` |
| `../sdk_eval/` | evaluation: `cases.py`, `runner.py`, `harness.py` (builds the real engines), `judge.py`, `regress.py`, `trajectory.py`, `golden.py`, `loadtest.py`, `cli.py` |

## Known limits (read before relying on it)

* A wrong answer with a valid-looking citation occurred (1 of 45 golden questions): nothing checks that a cited chunk supports the claim.
* Two small corpora (39 chunks in the official index). Every measured figure has a small denominator and is quoted with it in `docs/week12/`.
* The retrieval-sufficiency distance threshold is a placeholder and, measured, never fires on this corpus: abstention is done by the model.
* The tool-selection policy is rules, so the trajectory evaluation measures the rules, not model judgement.
* The suite was iterated on; a perfect score on the suite is not a prediction for unseen questions. Judge-vs-human agreement is **not measured**.
* Code the engine could not check is still returned as `answered` with a visible footer; whether that deserves its own outcome is an open owner decision.
* Pasted secrets/PII are redacted in logs, but the raw question still reaches the model provider (open owner decision, ledger G-OBS-6).
* CI runs the offline suite only; it has not been executed on GitHub by its author.
