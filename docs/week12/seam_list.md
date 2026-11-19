# Week 12 — Seam list

Date: 2026-11-04 · A *seam* is a joint between two components that each worked alone and failed together. Every row below comes from a failure observed in this repository during this build (source in the last column), or from a check I ran. I have not listed a seam I did not observe, and I have not reconstructed history I did not witness.

Status words: **Fixed** (a test or measured before/after shows it) · **Accepted** (a limit I chose to live with, named) · **Not done** (with the reason: it needs your decision, needs data I do not have, or is outside this brief). The brief's third status, **ran-out-of-time: none.** Nothing here was skipped for lack of time; where something is not done, the reason is not time. (The brief's phrase "Tuesday integration seam list" assumes a calendar I was not given; the dates in this repository's evidence are the real ones and I have not tied any seam to a weekday.)

## Fixed

| # | Seam | What failed | Fix and proof | Source |
|---|---|---|---|---|
| 1 | model typography ↔ version resolver ↔ delivery | pasted dates with U+2011 were "unsupported"; a model's wrong-version claim written with U+2011 passed verification; 17/35 delivered answers had look-alike hyphens, 2 inside code spans (`okhttp‑gson`) | `normalize_dashes` (input side, 25 tests) and `ascii_hyphens` (output side, once, at the draft write site); `reg05` RED on 3/3 checks with the defect re-created; 0/4 live re-runs affected | phase8.md, error_taxonomy.md mode 7 |
| 2 | verifier ↔ ordinary release numbers | any known version label of *any* product was read as a claim ("v2.0.0" matched the Swagger label 2.x) and a correct answer was withheld | claims scoped to the resolved product; `reg01` | phase6.md |
| 3 | output-secret check ↔ the user's own token | an echoed `Authorization: Bearer <token>` the user pasted was refused as an invented secret | compare the token, not the whole match; the invented token is still refused; `reg02` | phase7.md |
| 4 | shell-block parser ↔ multi-line curl | a quoted multi-line `-d '{...}'` body was split by physical line; JSON keys were reported as unknown commands | logical-line joining; `reg03` | phase6.md |
| 5 | degraded mode ↔ output safety | with no model, retrieved text was printed with none of the output checks (a poisoned page's auto-loading image would have shown) | degraded excerpts pass the same checks; `reg04`; mutation-checked | phase5.md |
| 6 | MCP server ↔ Windows stdio | first `import chromadb` inside a tool call, with a stdin read pending, hung the server until more input arrived | state built before `mcp.run()`; a protocol test now guards it | mcp_server.py docstring, phase3.md |
| 7 | uploads ↔ "delete means delete" | Chroma's index files retained deleted text | uploads moved to SQLite with `secure_delete` + VACUUM; 0/40 delete cycles leave text on disk (verified by reading the file) | phase4.md |
| 8 | upload corpus ↔ official corpus | the legacy app mixed uploads into the official index | separate store and a separate process boundary; owner filter + post-check; cross-owner tests | phase4.md |
| 9 | version tag on a chunk ↔ the filter that trusts it | nothing verified the tags against `page_metadata.json`; a wrong tag silently breaks the version guarantee | `index check` compares every chunk's tag with the hand-authored metadata (24/24 pages, 39 chunks, 0 mismatches) and is part of `health --deep` | phase11.md |
| 10 | harness pacing ↔ latency | the paced model call slept inside the request, so every measured latency included waiting | `--between` mode spaces whole requests instead | phase9/10 |
| 11 | tests ↔ the real `.env` | `cli.main` loads `.env`; a test calling it would put the live key in the process for every later test | autouse fixture stubs the loader for all tests | conftest.py |
| 12 | env var names ↔ documentation | an operator knob with no documentation, or documentation for one that does not exist | a test fails if `.env.example` misses any `ENV_*` constant in `config.py` | test_config.py |
| 13 | config values ↔ start-up | a bad value surfaced as a pydantic traceback or, for `llm_base_url`, could send the key over plain http | `ConfigError` names the variable and never echoes the value of a URL; http allowed only for localhost | test_config.py (23 tests) |

## Accepted (named limits)

| # | Seam | Limit |
|---|---|---|
| 14 | golden-set gold chunk ↔ retrieval metric | gold matching is page-level; it scored `easy_11` a hit though the answer-bearing chunk was not retrieved. Chunk-level matching would need relabelling the 45 questions |
| 15 | model-chosen tools ↔ evaluation | the graph chooses tools by rule, so the trajectory evaluation measures the rules, not an LLM (decision in `tool_selection.md`) |
| 16 | two key names | `GROQ_API_KEY` and the legacy `GROK_API_KEY` are both read; the legacy scripts still require the second |
| 17 | MCP server ↔ Hugging Face hub | the server runs offline from the model cache (`HF_HUB_OFFLINE=1`) and logs a harmless warning; the first indexing run needs the network |
| 18 | scripted-model cases ↔ real model | 29 offline cases test the engine's defences against a worst-case *scripted* model, not model behaviour; the 11 live cases and the golden run do |
| 19 | prices ↔ the provider | rates in `pricing.py` match Groq's published page as of 2026-11-04 (gpt-oss-120b $0.15/$0.60 per 1M tokens, gpt-oss-20b $0.075/$0.30) but cached-input discounts are not modelled, so cost is a slight overstatement if caching applies |

## Not done (and why)

| # | Seam | Why not |
|---|---|---|
| 20 | answer ↔ the cited text (claim support) | `easy_11` shows it matters (G-COR-12). Needs your decision: withhold or warn |
| 21 | PDF extraction ↔ the identifiers users search for | split option names defeat retrieval (G-COR-13). Fixing it rebuilds the index and changes every baseline: needs approval |
| 22 | distance gate ↔ the corpus | measured inert here (G-ORC-7); a better gate needs a design choice, not a number |
| 23 | verifier ↔ audit | a rejected draft is not kept, so over-blocking cannot be measured (G-OBS-7) |
| 24 | judge ↔ human | agreement NOT MEASURED; needs your hand labels (Q7) |
| 25 | the legacy router ↔ this engine | the Pydantic router the brief mentions was not found on this machine (Q5) |
| 26 | owner id ↔ identity | no authentication: isolation is between scopes, not people (G-UP-6) |
| 27 | CI ↔ GitHub | the workflow has not been run on GitHub; the same commands passed in a clean Windows checkout and venv; the Linux leg is untested |
| 28 | `traces.jsonl` tracked in git | left alone pending Q6 |
| 29 | prompt ↔ provider privacy | a pasted secret is redacted in logs but still reaches the model provider (G-OBS-6, your decision) |
