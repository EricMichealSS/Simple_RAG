# Phase 9 — Taxonomy re-score and trajectory evaluation

Date: 2026-11-04 · Status: **done** (with one open owner decision per finding, §5) · Nothing committed, pushed or deployed. No tracked file was modified (`git diff --name-status` empty).

## 1. What was asked

Re-score the Week 5 failure taxonomy on the integrated engine (4–7 modes, at least one created by the integration), and evaluate tool trajectories the Week 8 way (at least 10 cases: tool-choice accuracy, expected vs actual tools, outcome vs path, failed and unnecessary calls, sample size and limitations).

## 2. What was done

| step | result | evidence |
|---|---|---|
| Live run of all 45 golden questions through the whole engine (version → MCP tools → `gpt-oss-120b` → verification), judge `gpt-oss-20b` | 45/45 completed; 35 answered, 10 abstained, 0 degraded/error; **43/45 judged correct** | `evidence/phase9/golden_results.json`, `golden_console.txt`, `traces/` |
| Cost of that run | $0.017443 total, mean $0.000388, max $0.000837; 71,414 prompt + 11,218 completion tokens; every query priced; 44 queries made 1 model call, 1 made 2 | `golden_analysis.json` |
| My own read of all 35 answers and 10 abstentions against the golden answers | agreed with the judge on 45/45; found one defect the judge was not asked about (look-alike hyphens) | `error_taxonomy.md` §1, §3 |
| Week 5 taxonomy re-score | 7 modes, 5 from Week 5, 2 new, 1 integration-created; measured, with denominators and representative trace ids | `error_taxonomy.md` |
| Trajectory evaluation | 10 questions, 23 calls: path ok 8/10, tool-choice 21/23, outcome 10/10, failed calls 0 | `trajectory_eval.md`, `trajectory_summary.json` |
| Distance re-score (Week 5 mode 5) and gate context, no model call | 0/225 placeholder distances; top-1 distance does not separate answerable from unanswerable | `distance_check.json` |
| **Defect found and fixed:** hyphen look-alikes in delivered answers | 17/35 answers; fixed at the single point the draft is stored | §3 |
| New CLI: `eval trajectory`, `eval history`, `eval regress` shows every failing check | tests: `test_eval_cli_extras.py` (4) | |

## 3. The defect the run found (and how it was fixed)

The engine verified the model's text and delivered it unchanged. The model writes typographic hyphens (U+2011 65 times in these 35 answers), including inside things a user pastes: `okhttp‑gson` (the default library template, `easy_03`) and a library list (`hard_05`). Phase 8's date-only counter had shown 8 of 45; the actual figure is 17 of 35 answered.

* **Impact analysis (CLAUDE.md).** GitNexus had no record of the function (`generate` is a closure in a new untracked file: `risk: UNKNOWN`, "target not found"), so I resolved it by text search: the draft (`state["draft"]`) is read only inside `graph.py` (code check, `verify`, `finalize`) and written in one place. Changing the one write site changes what all three read; blast radius is that single graph, risk low.
* **Test first.** 3 new tests failed (RED), then passed. `reg05` was strengthened rather than a 41st case added, because the brief caps the suite at 40 cases; with the defect re-created all three of its checks fail, on the current code all pass, and 5/5 regression cases remain verified.
* **Live confirmation.** 4 affected questions re-run: 0 look-alikes in 4/4, judge PASS 4/4, `okhttp-gson` is now ASCII (`evidence/phase9/fix_confirmation/`). The four answers cost $0.0019 (grading not included). The other 13 affected answers were not re-run live.
* **What it does not do:** it does not catch other classes of look-alike (the 35 answers had no other non-ASCII character inside a code span; U+202F appears 93 times in prose only, harmless).

## 4. Findings that are worse than the headline 43/45

1. **A wrong answer with a citation that looks fine** (`easy_11`). The verifier checks that a citation exists and is in scope; nothing checks that the cited text *supports* the claim. The model read "(Default: true)" in an illustration of the *2.x style* as the 3.x default. The judge caught it, the engine did not. (Ledger G-COR-12.)
2. **The sufficiency gate is inert on this corpus.** Top-1 distance: answerable 0.124–0.250, unanswerable 0.161–0.334, threshold 0.65. All 10 abstentions came from the model. The model refused 9/9 unanswerable questions, which is the only defence between them and a fabricated answer. (G-ORC-7.)
3. **A retrieval miss from PDF text extraction** splitting option names across lines (`allowUnicodeIdentifie│rs`). (G-COR-13.)

## 5. Decisions that are yours (not made by me)

* **Rebuild the index with re-joined identifiers** (fixes the cause of `easy_11` and probably `hard_03`): rebuilds `race_docs`, changes every measured baseline. Not done.
* **Add a claim-support check** before delivery for value questions (deterministic, no model call) and decide whether an unsupported answer is withheld or delivered with a warning. Not done.
* The standing open items: Q5 (router location), Q6 (tracked `traces.jsonl`), Q7 (hand labels: judge-vs-human agreement remains **NOT MEASURED**), the outcome label for answers whose code could not be checked, redaction of pasted secrets before the provider.

## 6. Limits of this phase

* The 45 questions are the author's golden set, reused from earlier weeks and Phase 2: optimistic, not held out, not field traffic.
* The judge is a model whose agreement with a human is unmeasured; I read the outputs too, but I am one reader and not independent.
* The trajectory evaluation measures rules, not LLM tool choice; specs and rules share an author; 4 of the 10 questions are one template.
* This run was paced to the key's token limit, so latency and any provider-error behaviour from it are not valid evidence (Phase 10 measures latency separately).
* Page-level gold matching overstated retrieval (it scored `easy_11` a hit).

## 7. Files inspected

`developer-rag/taxonomy.md` (Week 5 modes), `agent_race/week8_report.md` and `agent_race/trajectory_specs.py` (Week 8 method and baseline), `agent_race/golden_sets/` via `sdk_eval/golden.py` (the 45 questions), `sdk_engine/graph.py` (route rule at line 257; edges; the draft write site), `sdk_engine/answering.py`, `sdk_engine/retrieval.py` (distance handling), `sdk_engine/pricing.py`, the index contents for the failing questions (`swagger_v3_reference:p3`, `p6`), and every span file of the run.

## 8. Commands run and actual results

| command | result |
|---|---|
| `python -m sdk_engine eval golden --out <dir>` (live, model paced inside the call) | 45 questions, exit 0, 43/45 judged correct |
| `python -m sdk_engine eval trajectory --results evidence/phase9/golden_results.json` | table + summary in `trajectory_rescore_console.txt`; path ok 8/10 |
| `python evidence/phase9/scripts/analyze_golden.py` / `distance_check.py` | `golden_analysis.json`, `distance_check.json` |
| `python -m sdk_engine eval regress` (before and after the fix) | 5/5 VERIFIED; reg05 RED fails 3 checks with the defect re-created |
| `python -m sdk_engine eval golden --only easy_03 medium_04 hard_01 hard_05` (after the fix) | 4/4 answered, 0 look-alike hyphens, judge PASS 4/4 |
| `python -m pytest -q` (everything, integration included) | **749 passed** in 247 s |
| `python scripts/scan_secrets.py` | 356 files, 0 suspicious lines |

## 9. Security and privacy considerations

The golden run sent the 45 golden questions (public documentation questions, no personal data) to Groq, and 45 short grading prompts to Groq (question, golden answer, system answer). The key was read from the git-ignored `.env`, never printed; the spans store question *lengths* and redaction counts, not question text. Evidence files were scanned for credential-shaped strings (0 found). The fix touches what the user receives, not what is logged.

## 10. Acceptance status and next steps

Taxonomy: 7 modes, 1 integration-created, with counts, denominators, severity, representative trace ids: **met**. Trajectory: 10 questions, all required metrics, limitations stated: **met**. Not met and not claimed: judge-vs-human agreement (Q7). Next: Phase 10 (cost, latency, load, 10×).

## 11. Files changed in this phase

New: `docs/week12/error_taxonomy.md`, `trajectory_eval.md`, `phase9.md`, `evidence/phase9/` (golden results, spans, analyses, scripts, regression and fix-confirmation outputs). Code: `sdk_engine/answering.py` (`ascii_hyphens`), `sdk_engine/graph.py` (one call at the draft write site), `sdk_eval/cases.py` (`reg05` strengthened), `sdk_eval/regress.py` (defect re-creation covers the output side), `sdk_eval/cli.py` (`trajectory`, `history`, full RED-check listing). Tests: `test_answering.py` +2, `test_graph.py` +1, `test_eval_cli_extras.py` +4. Full suite: **749 passed** (11 integration tests included), 2026-11-04.

Phase 11 work done early because it was independent of Phase 9 results, recorded in `phase11.md`: validated config, `index`, `mcp-serve`, `health --deep`, `.env.example`, pinned requirements, README, CI workflow.
