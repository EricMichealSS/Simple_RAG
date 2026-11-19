# Phase 4 — User-uploaded documents

Date: 2026-11-03 · **Status: COMPLETE for its scope.** Uploads are validated, hashed, chunked with the existing chunker, stored in a private per-owner collection, searchable through the same retrieval interface with scope enforcement, cited by filename and page/section, listable, and **physically** deletable (verified on disk). **Not covered:** authentication (the owner id is a caller-supplied string), OCR, and live-model answer quality **for uploads** (the key arrived after this phase; live runs so far covered the official corpus only — see `phase1.md`'s addendum).

## Objective
"What does my uploaded document say about X?" searches only the caller's private uploads, answers only from evidence that is present, cites the filename and page/section, and never mixes with the official corpus or another owner's data.

## Files created (all new; no tracked file modified)
| File | Purpose |
|---|---|
| `sdk_engine/chunking.py` | Reuses the **unmodified** legacy structure-aware chunker (see "Chunker reuse") |
| `sdk_engine/extract.py` | Validation + in-memory extraction for PDF / DOCX / Markdown / text; filename sanitiser |
| `sdk_engine/uploads.py` | `UploadStore`: ingest, per-owner dedup, list, delete, delete_all, search; `DeleteResult.purged` is honest |
| `sdk_engine/sqlite_store.py` | `SqliteUploadClient`: the production upload store (see "Why not Chroma for uploads") |
| `sdk_engine/graph.py` (edited) | `select_corpus` / `route_upload` / `retrieve_upload` nodes; upload-aware generate/verify/finalize/abstain |
| `sdk_engine/answering.py`, `schemas.py`, `config.py`, `factory.py`, `cli.py`, `retrieval.py` (edited) | upload prompt + abstain text, `AnswerResult.corpus`, limits/env vars, `LazyUploadStore`, `upload`/`uploads` commands, opaque `owner_key` |
| `tests/{filefactory,test_chunking,test_uploads,test_uploads_physical_delete,test_sqlite_store,test_graph_uploads,test_cli}.py` | see Tests |

## Upload pipeline
1. **Validate** owner (`[A-Za-z0-9_.@-]{1,64}`), size (≤ `max_upload_bytes`, 5 MB), filename → sanitised **display-only** basename (both path separators, control/bidi characters, hidden names, length) and extension allowlist (`pdf docx md txt`); **content must match the extension** (PDF header; DOCX zip with `word/document.xml`; text must be UTF-8 with no NUL/binary).
2. **Reject hostile containers:** DOCX with macros (`vbaProject.bin` etc.), traversal member names, > 2000 members, > 50 MB expanded or > 100:1 compression ratio (zip bomb); encrypted or unreadable PDFs; > 200 pages; > 1 M extracted characters; files whose parse exceeds 20 s (abandoned by timeout).
3. **Extract text only** in memory — PDF per page, DOCX paragraphs/headings/tables (as Markdown), MD/TXT decoded. Nothing is written to disk, nothing is executed, nothing embedded is followed.
4. **Hash** (SHA-256 of the bytes): `doc_id = hash[:16]`; identical bytes → `duplicate` (per owner only).
5. **Chunk** with the legacy structure-aware chunker (tables and fenced code kept whole; PDF text normalised, MD/TXT/DOCX not), hard cap 3000 chars per stored chunk; ≤ 500 chunks per document (rejected, not truncated, and atomically: nothing is added).
6. **Index** chunk text + provenance metadata (`owner_id` = opaque key, `doc_id`, `content_hash`, `source_file`, `file_type`, `page` for PDFs, `anchor`/`section`, `chunk`, `uploaded_at`, `index_version`) with the local embedder; chunk ids `upl:{doc_id}:p{page|0}:{anchor}:{n}`.
7. **Ask:** `select_corpus` (explicit `--scope`, or the wording "my uploaded document/file/pdf/notes…" + an owner id) → `route_upload` → `retrieve_upload` (in-process only) → distance gate → `generate` (system prompt: only the uploads, untrusted-data rule, no outside knowledge) → `verify` (citations must be retrieved chunks; version checks skipped — uploads are unversioned) → `finalize`.

## Isolation (each layer has its own tests)
1. a **separate collection per owner** (`uploads_<sha256(owner)[:24]>`), in a **separate on-disk store** from the official index (`data/uploads/uploads.sqlite3` vs `data/chroma`);
2. every chunk stamped `owner_id = owner_key(owner)` and every query filtered on it;
3. `enforce_scope` re-checks each result and fails closed (`ScopeViolation` → request error + `security_event` span).
Also: the raw owner id (possibly an email) never appears in the store, metadata, collection names or spans (only the hashed key); uploads never go through the shared MCP server (it has no user identity); uploads are never searched without an owner (the engine asks for one); dedup never looks across owners (identical files from two owners are both `indexed`, so no signal leaks); no cross-owner delete (`not_found`).

## Chunker reuse without editing it
GitNexus impact for `chunkers.chunk_structured`: **HIGH** — 6 direct callers across all four ingest flows. Therefore **not edited**. `chunkers.py` imports the legacy `config.py`, which raises without `GROK_API_KEY`. `chunking.py` reads the three size constants from the legacy `config.py` *source* with `ast` (no import, no key), executes the unmodified `chunkers.py` under a stand-in `config` module, and restores `sys.modules`. `tests/test_chunking.py` proves **exact output parity** with the real legacy import (subprocess, placeholder key) in both `normalize` modes, that a pre-existing `config` module is not clobbered, and that structure (tables, code) is preserved.

## Why not Chroma for uploads (a measured redesign)
I first built uploads on Chroma and tested deletion by scanning every byte of the store directory for a unique marker:
- after `delete(doc)` the text stayed in `chroma.sqlite3`; after `delete_collection` it stayed until `VACUUM`; a per-document delete was not purged even by `VACUUM` (live write-log/full-text rows) — so I added a rebuild-the-collection step;
- **but the HNSW index files (`data_level0.bin`, `length.bin`) routinely contained document-text residue** (stale heap contents written with the index): survivor text appeared in a vector file in ~30 of 40 cycles, and the **deleted** text in **1/40** (same-process) and **6/40 = 15%** (realistic: ingest, delete, exit in separate processes); a later sweep of orphaned segment directories (which can't be removed while the process holds them open on Windows) left **2/40 = 5%**. My hypothesis (stale malloc'd buffers) fits the evidence; I did not prove the cause.
- Conclusion: **with Chroma, physical deletion could not be guaranteed.** Private uploads are small (≤ 25 docs × 500 chunks per owner), so they moved to `SqliteUploadClient`: one SQLite file, `secure_delete=ON`, `journal_mode=DELETE` (no WAL), brute-force numpy cosine search, a `VACUUM` after every delete. It exposes the small Chroma-like surface `ChromaRetriever` and `UploadStore` use, supports only equality filters on `owner_id`/`doc_id` and **raises on any other `where`** (no filter is ever silently ignored), and validates collection names.
- **Result with the final design, real separate processes, 40 cycles: deleted text found on disk 0/40**; control (the surviving document's text still stored) 40/40; only `uploads.sqlite3` remains in the directory (no journal/WAL/temp files). The official corpus keeps using Chroma (read-only at query time; no private data in it).

## Commands run and actual results
- `pytest -q` (no key): **391 passed** in ≈90 s (includes 4 real-index integration tests). Per file, from `pytest --collect-only`: uploads 61, versioning 46, retrieval_scope 37, redaction 33, tool_policy 31, graph 26, symbols 23, sqlite_store 22, protocol_independent 17, answering 15, graph_uploads 15, mcp_client 14, graph_tools 12, trace 12, uploads_physical_delete 9, chunking 6, cli 6, mcp_graph_e2e 3, integration_real_index 3 (these sum to 391).
- **Mutation checks** (throwaway plugin): all owners collapsed to one key → 7+ failures (cross-owner tests, quota, delete isolation, broken-filter post-check); upload filter **and** post-check removed → many failures; raw (unsanitised) filenames → 18 failures; macro check off → 4; content check off → 4. Un-mutated: green.
- **Real embedder, real files, real CLI** (no key, `--no-llm`): uploaded a real PDF (2 pages), a DOCX (heading + table) and a Markdown file for two owners; results: `indexed … pages=2`, duplicate re-upload detected, `.docm` rejected (`unsupported_type`), the owner's query returned excerpts citing `zoo_guide.pdf p.1`, another owner got none of Alice's text, and a query with no `--owner` returned the "upload scope required" clarification.
- **Distance-gate calibration probe** (authored documents + authored questions, bge-small, 10 answerable + 10 unanswerable): answerable top-1 distance 0.105–0.373, unanswerable 0.481–0.611; the official placeholder gate (0.65) would have admitted **10/10 unanswerable** questions, so uploads got their own gate **0.50** (all 10 answerable kept, 0.127 margin; 2/10 unanswerable pass to the model, whose prompt tells it to abstain). **Small, authored, not an evaluation** — re-tune on real data.

## Tests (what they prove)
`test_uploads.py` (61): filename sanitisation table (traversal, drive letters, null bytes, bidi override, long names, hidden files), unsupported types, content/extension mismatch, empty/oversize, macros, zip bomb, traversal members, encrypted/garbage/blank/too-long PDFs, text limits, parser timeout; PDF page provenance, DOCX headings→sections with the table kept whole, BOM/CRLF; per-owner dedup (same bytes any name; same name different bytes); owners cannot see/delete each other's documents; identical files from two owners don't reveal each other; the raw owner id is never stored; uploads and the official corpus never mix; owner-id validation; a store with a **broken owner filter** is caught by the post-check; list/delete/delete_all; quotas; a rejected document leaves nothing behind. `test_uploads_physical_delete.py` (9): control scan sees stored text; one-document delete leaves no trace of it and keeps the rest searchable; **40 independent cycles with zero residue**; reopen; delete_all; other owners untouched; only the database file remains; a client that cannot compact reports `purged=False` instead of claiming a purge. `test_sqlite_store.py` (22): round trips, cosine distances match Chroma's semantics, unsupported/odd filters raise, SQL metacharacters are data, concurrent writers, a second client on the same file. `test_graph_uploads.py` (15): answered from the upload with filename/page citations and the official corpus untouched; unanswerable → abstain; weak evidence refused before the model; no owner → clarify; other owner → nothing and no model call; official question stays official with an owner given; explicit `official` scope overrides upload wording; uploads never use MCP; invalid owner/scope rejected; spans carry the hashed owner key only (no raw id, no question, no secret); broken upload filter fails closed; degraded excerpts; hostile text in an upload stays quoted data (cannot close our delimiter tags). `test_cli.py` (6): upload/list/query/delete/purge round trips on an on-disk store, failure categories + exit codes, size cap enforced before reading, `--yes` required for purge.

## Defects found in my own code or assumptions (all fixed or recorded)
1. **Chroma retained deleted text** (above) → replaced the store; this changed a design decision after measurement.
2. **The 0.65 distance gate admitted every unanswerable upload question** with the real embedder → upload-specific, calibrated 0.50.
3. Degraded answers said "most relevant excerpts" although they can be unrelated → reworded to "closest … (may not answer your question)".
4. Test pollution from Chroma's process-wide ephemeral client with deterministic collection names → unique prefix per test store.
5. A shell/heredoc layer silently collapsed backslashes while I was writing patch scripts → file-based scripts for anything containing escapes.

## Security and privacy
Documented above. Additionally: error messages carry categories, never file contents; uploaded text reaches the model only inside escaped `<document>` delimiters with a policy that tells it not to obey them (Phase 5 adds detection and adversarial tests); the CLI refuses oversized files before reading them.

## Remaining limitations (honest)
- **No authentication.** `owner` is whatever string the caller supplies; anyone who knows another owner's id can read that owner's uploads through this library/CLI. The isolation is between *scopes*, not an identity system; a multi-tenant deployment needs authentication in front of it and a mapping from the authenticated user to the owner id.
- **File-level deletion only.** Physical purge is verified at the file level for this store; SSD wear-levelling, OS backups, shadow copies and disk snapshots are outside its reach (use disk encryption). The official Chroma index is not private data and is unchanged.
- **Parsing is in-process** (pypdf, python-docx). Size/page/member/time limits bound it, but a hostile file that hangs the parser is only abandoned (its thread keeps running), not killed. A subprocess sandbox would be stronger.
- Scanned PDFs (no text layer) are rejected; no OCR. Images/embedded objects/comments/headers/footers/footnotes in DOCX are ignored. Extracted PDF text is as good as pypdf makes it.
- Brute-force search scales with an owner's chunk count (bounded by the quotas); not for large corpora.
- The upload distance gate is calibrated on 20 authored questions; the model's abstention remains the last line of defence. **Generation quality for uploads has not been measured against a live model.**
- **CLI cold start:** each `python -m sdk_engine` call that touches the embedder takes ≈12–13 s to load the model (measured); warm calls are ≈0.1–0.2 s. A long-lived process would remove it. Recorded for `tenx.md`.
- Per-owner storage is bounded by document count and chunk count, not bytes on disk; no retention/expiry policy.

## Acceptance
Accepted for Phase 4's scope, with the no-authentication and no-live-LLM caveats above.

## Next
Phase 5 — prompt-injection detection and adversarial regression tests (retrieved, uploaded and tool content), secret-exfiltration and cross-document attempts, and log-redaction coverage end to end.
