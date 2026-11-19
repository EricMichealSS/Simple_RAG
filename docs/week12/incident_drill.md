# Incident drill — "A code sample used a deprecated method" (Week 12 stack)

Date: 2026-11-04 (spans are stamped UTC, so the run itself is 2026-11-03 evening UTC) · **Status: performed twice, blind, against a SYNTHETIC log. This is a rehearsal of the tooling, not a real or historical production incident.**

## The complaint (same as Week 11; nothing else is known)
> A developer reports that the AI application generated a code sample using a deprecated method sometime last week.

No trace id, user id, question text or SDK version.

## Relationship to the Week 11 drill
Week 11's drill (`agent_race/week11/drill.md`, left untouched) found its planted incident in **00:47** by grepping the `answer`/`question` fields of ~30 logged rows, and then audited itself honestly: that speed was an artifact of having ~30 rows, and it named what was missing. Where each stands now:

| Week 11 named gap | Week 12 |
|---|---|
| no `trace_id` on the rows | one trace id on every span of a request, across the MCP hop; `trace_id` is printed after every CLI answer; `trace show` takes an id or a unique prefix |
| no timestamp index | every span has `start_ts`/`end_ts`; `find --since/--until` (+ `--as-of` for exact re-runs); `stats --bucket hour\|day` |
| no failure-mode / forbidden-term index | structured summary fields on the root span: version, endpoint, tools, outcome, cost, retries, **code tiers**, **deprecated-warned count**, **unchecked count + reason**, MCP fallbacks, security events |
| (new) the grep itself | **no longer possible by design**: neither the question nor the answer is persisted (tested). The structured fields are the index, with the consequence below |

## The synthetic log (everything in it is synthetic, and says so)
`evidence/phase7/make_synthetic_log.py` writes spans with the real shape (same span names, attributes, parent links as the live trace in `sample_trace.json`) and computes costs with the engine's real price function; each root span carries `synthetic_log` in its attributes. **422 requests**: 400 background requests spread over 14 days (80% answered; ~30% of those with code; a few warned deprecations), a 12-request **MCP-outage window** (2026-10-30, 13:30–15:10 UTC; tool calls time out, retrieval falls back to local), and 10 decoys (2025-06-01 `create_issue` samples whose deprecation **was** warned). One request in the window is the **planted** one: `bc17de9100ef…` (2025-06-01, `create_issue`, code sample, check not run). Ground truth was kept in a separate file the investigator never saw.

**The scenario is constructed, not a likelihood claim.** In the shipped wiring the in-process symbol index (`LazySymbolIndex`) also covers an MCP outage, so a real outage would normally give `symbol_source=local`, not `none`. The rehearsal assumes **both** were unavailable (for example a failing in-process index build) so that the code check could not run. The drill tests the tooling, not how probable that chain is.

## Method
A **blind investigator**: a fresh Claude subagent with no access to this session's context, given only the complaint, the CLI path and rules (use only the trace CLI; read no source, docs, `.env` or other files; do not list directories for hints; time yourself with `date`). I did not investigate myself because I planted the incident. The investigator is an AI working at machine speed: **a human would be slower**, and these are **two single runs**, not a distribution.

## Results (verbatim reports in `evidence/phase7/drill_run1_report.txt`, `drill_run2_report.txt`)
| | Run 1 | Run 2 |
|---|---|---|
| CLI | find/show/stats with time, version, endpoint, tool, status, code-tier filters | + `find --fallback`, `stats --bucket`, `--as-of`, plainer flags and legend, `deprecated_warned` / `unchecked:<why>` |
| Log | first synthetic log | regenerated (outage durations fixed, new fields), same trace ids |
| Self-timed (includes `--help` calls) | **65.3 s** (candidate set found ≈ 15 s before the end) | **37.1 s** |
| Harness-recorded run duration | 98.1 s (11 tool calls) | 60.3 s (9 tool calls) |
| Narrowed 422 requests to | **4 candidates, including the planted one** | **the same 4** |
| Picked a single trace? | **No** — said so | **No** — said so |
| Mechanism identified | MCP timeouts → local fallback → code check ran with no symbol source → unflagged samples shipped as `answered` | same |
| Stated confidence | 40–50% the incident is among the 4; <10% for any one | 75–80% among the 4; 25% each |
| Said what would resolve it | the developer's version and endpoint | the same |

**Grading against ground truth.** Both investigators' candidate set contained the planted request `bc17de9100ef…`, both named the real mechanism and the real outage window, and both correctly said the logs cannot tell the four apart. Run 2's own note: with the developer's version and endpoint (2025-06-01 + `create_issue`) the set collapses to exactly the planted request. **Neither run "found the incident" in the sense of naming one trace**, and I am not scoring it as if they had.

**Why it cannot pin one request, and why that is the design.** The four requests are indistinguishable by every field the logs hold, because the only things that would separate them (the question and the answer) are deliberately not stored. To close the loop, the complaint must carry something the logs have: a **trace id** (printed as `trace_id=…` after every CLI answer; an "ask users to quote that line" instruction is a process step, not code), or a version, endpoint or time.

## What changed because of the drills
From run 1's feedback (all tested; run 2 measured them): `find --fallback`; `stats --bucket`; `--as-of`; `code_deprecated` / `code_unchecked` / `code_unchecked_reasons` on the summary; flags renamed (`mcp_fallback:N`, `unchecked:<why>`, `deprecated_warned:N`) with a printed legend; and — found while reading the code against the feedback — **the answer footer now says when identifiers "could NOT be checked" and why** (before, an unchecked identifier looked exactly like one that passed). From run 2's feedback: `find --unchecked` and `--has-code` (**added after run 2 and not re-drilled**). Run 2's suggestion to mark unchecked-code answers `outcome=degraded` was **not** adopted: `degraded` currently means "no answer model", and overloading it is a design decision for the owner (see Open).

## Caveats on the numbers
* n = 2, AI investigators, a log built by me. The improvements after run 1 were made in response to run 1's scenario, so run 2's speed-up partly reflects tuning to this scenario ("teaching to the test"); I cannot separate that from a general improvement.
* Run 1 used the first generator, whose outage requests had durations shorter than their own timeout spans (the investigator noticed); fixed before run 2 without changing any random draw.
* My own external stopwatch for run 1 included my delay in reading the result, so the figures above are the investigators' own `date` stamps and the harness's recorded durations.
* Machine time of a single CLI command on this log is well under a second (see the scale table in `phase7.md`); nearly all the elapsed time is the investigator thinking.

## Open
1. **What outcome should an answer have when its code could not be checked?** Today `answered` (with the footer warning and the `unchecked` flag in the trace). Alternatives: a new outcome (`answered_unchecked`), or abstaining on code. Your call.
2. Ask users to quote the `trace_id=` line when reporting a problem (process, not code).
3. Re-run the drill, ideally with a human on-call engineer and a log from real traffic, once there is any.

## Reproduce
```
python docs/week12/evidence/phase7/build_drill_log.py <log_dir> <truth_file>      # synthetic log + planted incident
python -m sdk_engine trace --dir <log_dir> stats --bucket day                     # where is the anomaly?
python -m sdk_engine trace --dir <log_dir> find --fallback --unchecked --has-code # who is exposed?
python -m sdk_engine trace --dir <log_dir> show <trace_id_prefix>                 # what happened?
```
