# Week 6 — Judge Validation Report
**Track E: Developer Documentation RAG**
**Date: 2026-09-03**

---

## 1. What changed from Week 5 to Week 6

Week 5 read 20 real traces by hand and produced a 5-mode failure taxonomy
(`../taxonomy.md`) with a dated prediction. Week 6 does not add new app
features — it builds the *measurement machine*: a mode-tagged eval set that
runs in one command, deterministic assertions that replace part of the judge,
and a validated LLM judge whose agreement with a human was measured *before*
it was trusted.

## 2. Eval set

`cases.json` — 26 cases (exceeds the 25+ requirement), each tagged with one
mode:

| mode | n | source |
|---|---|---|
| `baseline` | 15 | 12 from the existing `golden_set.jsonl` + 3 new (uploadAttachment, listWebhooks, waitForRateLimit) |
| `mode1_groq_error` | 2 | **regression, verbatim** from real failed traces `610698d4`, `49bcc271` |
| `mode2_relevance_gate_false_refusal` | 2 | 1 **regression, verbatim** (`03099fdc`) + 1 constructed sibling |
| `mode3_answerable_wrong_chunks` | 2 | 2 **regression, verbatim** (`fd0c9df9`, `9fe19bdb`) |
| `mode4_cross_document` | 2 | 1 **regression, verbatim** (`4335c1b8`) + 1 constructed |
| `version_confusion_w6` | 3 | new this week — the v2-REST-vs-v3-SDK conflation the problem statement names |

6 cases are regression cases replayed verbatim from real failed traces
(rubric asks for "at least 2").

One command runs everything:

```
python3 eval/run_eval.py
```

Live output (2026-09-03, fresh retrieval + generation, not the frozen set):

```
PASS RATE BY MODE
============================================================
mode                                      pass   total    rate
baseline                                    11      15    73%
mode1_groq_error                             1       2    50%
mode2_relevance_gate_false_refusal           0       2     0%
mode3_answerable_wrong_chunks                0       2     0%
mode4_cross_document                         0       2     0%
version_confusion_w6                         2       3    67%
------------------------------------------------------------
OVERALL                                     14      26    54%

Regression guard (Week 5 Mode 5, distance-metadata placeholder):
0/26 clean (still broken on every case — not yet fixed)
```

This is exactly the failure the brief warns about: one overall number (54%)
would hide that 3 of 6 failure modes are at **0%** while `baseline` (73%)
props the average up. The by-mode table is the deliverable that's actually
actionable — it says "fix retrieval for cross-document and wrong-chunk
questions first," which "54% pass rate" alone never would.

The Mode 5 regression guard (`distance_not_placeholder`) also confirms the
Week 5 bug (BM25 placeholder distance overwriting real dense distances in RRF
fusion, `rag_hybrid.py`) is **still unfixed** — it fires on every single case,
exactly as taxonomy.md predicted it would until someone removes the
`.update()` overwrite.

## 3. Deterministic assertions (Requirement 2)

`assertions.py` implements **3 assertions** pulled out of the judge, plus one
separate regression guard:

| assertion | replaces judge criterion | how |
|---|---|---|
| `code_sample_parses` | "does the code sample parse" | `node --check` on every fenced JS block |
| `endpoints_and_symbols_exist` | "does every endpoint/method mentioned exist" | checks every `method()` / `METHOD /path` mention against `symbol_registry.json`, built by hand from the actually-ingested docs |
| `api_version_stated` | "is the API version stated" | if the question is version-ambiguous and doesn't already name a version, the answer must say v2/v3/SDK/REST API |
| `distance_not_placeholder` *(not a judge criterion — new)* | — | regression guard for Week 5 Mode 5 |

**Assertions: 3. Judged criteria remaining: 1** (`HELPFUL` — the one thing
that genuinely needs a subjective read: correctness and completeness).
Before this week, the same 4 things would all have been judge criteria.

While building these I caught two false positives in my own checks before
trusting them — worth reporting since it's the same discipline as judge
validation, just applied to code instead of a prompt: `endpoints_and_symbols_exist`
was flagging bare `send()` mentions inside dumped source text (fixed by
accepting the bare method name, not just `client.send()`), and
`api_version_stated` was flagging questions that already name their version
in the question text itself, e.g. "...in the OctoKit SDK v3?" (fixed by
skipping the check when the question already states a version).

## 4. Blind labels (Requirement 3)

`labels_25.json` — 26 answers (all cases) hand-labeled PASS/FAIL against the
judge's single binary criterion (`HELPFUL`), graded blind against each case's
`key_fact` in `cases.json`. **Written and saved before any judge was run** —
`answers_25.json` (the frozen answer set) and `labels_25.json` were both
created and file-dated before `judge_v1_verdicts.json` exists at all; commit
these two files first if/when you push, so the git history carries the same
ordering proof the filesystem timestamps already do.

Result: 16 PASS / 10 FAIL.

## 5. Judge validation — agreement before → after (Requirement 4)

**A harness bug found before trusting the "before" number:** the first
`judge_v1` run only agreed 50% (13/26) — but 4 of those disagreements
(`c01`, `c09`, `c12`, `c19`) turned out to be an artifact of my own eval
code: `judge.py` was building the judge's context from the 300-char
`text_snippet` stored for display, while the real generator saw 600 chars
(`app.py`'s `MAX_CHUNK_CHARS`). E.g. `retry_backoff_ms` sits at character 310
of the `client.send()` chunk — inside what the generator saw, outside what
the judge saw. Fixed `judge.py` to pull the full chunk text (truncated to the
same 600-char budget the generator actually uses) by `chunk_id` from the live
index instead. That is the fair, apples-to-apples baseline:

**agreement_before = 21/26 = 80.8%**

### Prediction (written before iterating, `prediction.txt`)

> Adding `c22` and `c24` as few-shot FAIL examples will fix those two failure
> shapes and raise agreement to roughly 88–92%. It will *not* fix `c05`,
> `c20`, or `c26`, because those are cases where my own hand-label was the
> miscalibrated one, not a judge error.

### What actually happened

**agreement_after (judge_v2) = 20/26 = 76.9%**

The prediction was **wrong on the direction of the total**, right about
*why* three specific cases wouldn't move, and right that the two targeted
fixes would land:

- ✅ `c24` (version conflation) and `c22` (retrieval-scope blindness) — **both
  fixed exactly as intended.** judge_v2 now correctly fails the answer that
  silently substitutes "the SDK's `client.send()`" for "the REST API," and
  correctly fails the "I don't know" that skipped a comparison the docs do
  contain because retrieval only surfaced one side of it.
- ✅ `c05`, `c20`, `c26` — persisted exactly as predicted. On closer reading
  (see §6) these are cases where my blind label, not the judge, was off.
- ❌ **Unpredicted regression:** `c03`, `c16`, and `c21` newly disagree in v2
  (v1 correctly agreed FAIL with the human label on all three; v2 flips all
  three to PASS). `c03` and `c16` are Groq-413 error blocks that dump the raw
  retrieved chunk text as a fallback (`⚠️ Generation unavailable...` +
  "Relevant chunks") — no real answer was ever generated, and judge_v1
  correctly failed them. judge_v2 incorrectly passes them, reasoning e.g.
  "the answer correctly states SDK errors are never swallowed" — the judge
  is crediting a fact that appears only inside the dumped fallback excerpt,
  not anything the assistant actually asserted. `c21` is a plain "I don't
  know" (Git LFS per-minute limit) that v1 correctly failed and v2 now
  passes with reasoning nearly identical to `c20`'s ("the documents do not
  contain..."), even though — unlike `c20` — the Git LFS figure genuinely
  never made it into any retrieved chunk in this run, so "I don't know" here
  actually *was* correct; the point is that v2 reached that verdict for the
  same overgeneralized reason as `c20` rather than distinguishing the two
  cases. The two few-shot examples I added both argue "don't let the answer
  off the hook just because it says 'I don't know'" — that framing appears
  to have made the judge weight *presence of a correct fact somewhere in the
  context or answer text* over *whether the answer field itself asserts a
  real answer*, which backfired specifically on the error-fallback answer
  shape (`c03`, `c16`) and blunted its "I don't know" scrutiny generally
  (`c21`).

Net: 2 real disagreements fixed, 2 new ones introduced, agreement fell. This
is reported honestly rather than iterating a third time to force a nicer
number — see the brief's own warning: "Reaching 85% by relabelling the
answers you disagreed on instead of fixing the judge — you moved the ruler,
not the thing being measured." judge_v1 (with the context bug fixed) is kept
as the **production judge** in `run_eval.py`; judge_v2 is preserved as a
recorded, informative failed iteration, not adopted. A real v3 would add one
line: "only credit facts that appear in the ANSWER's own asserted sentences,
never facts that only appear inside a dumped source-chunk fallback after a
generation error" — not attempted here, left as the next iteration.

## 6. Disagreement analysis — who was right (Requirement 4/rubric line 4)

**`c24`** (judge wrong, fixed in v2): Q: "Does the REST API automatically
retry a request if it fails?" A: "Yes. The SDK's low-level `client.send()`
method retries failed requests automatically... [cites client.send()
defaults]." judge_v1 passed this because every individual fact cited is
accurate to its chunk. **The human label (FAIL) was right**: the question
asked about "the REST API" in general; the answer's entire proof is a v3 SDK
method's defaults, and it never says so. This is the exact DevRel complaint
from the problem statement, live in the eval set on the first try.

**`c20`** (human wrong, judge right): Q: "OAuth access-token requests per
hour under secondary rate limits?" A: "I don't know." My original blind label
called this FAIL, reasoning the fact exists in the retrieved chunk
`rate-limits:p4:top:4`. Checking precisely: that chunk is 2,180 characters
and the "2,000 OAuth access token requests per hour" bullet sits at character
1,261 — past the 600-char per-chunk budget the app truncates every chunk to
before it ever reaches the LLM. The fact is in the *document*, but never in
the *context the generator actually saw*. "I don't know" is correct given
what was actually shown to the model. **The judge was right; my label was
wrong** — I checked the full document instead of the truncated prompt. The
sibling case `c21` (Git LFS per-minute limit) is the same story: the correct
fact sits at character 1,554 of a 2,813-character chunk that *was* retrieved
at rank 1 — also past the 600-char cutoff, also a correct "I don't know"
given what the generator actually saw. This surfaces a second, more specific
root cause behind Week 5's Mode 3 ("answerable, wrong chunks retrieved"):
sometimes the *right* chunk is retrieved, and the fact is still lost to
per-chunk truncation, not to retrieval ranking at all. Worth a line in a
future taxonomy revision, and a cheap fix (raise `MAX_CHUNK_CHARS` or trim
each chunk to only the matched section) once someone picks it up.

## 7. Bonus (RAGAS) — not attempted

Out of scope for this pass given time — flag if you want it done: faithfulness
+ context precision on the docs-backed cases, specifically hunting for a
high-faithfulness/wrong-version answer like `c24` (it grounds itself
correctly in a real chunk while answering the wrong question — a strong
candidate for "confidently faithfully wrong").

## 8. Files

| file | what it is |
|---|---|
| `symbol_registry.json` | ground-truth endpoints/SDK symbols, hand-built from the ingested docs |
| `assertions.py` | 3 deterministic checks + 1 regression guard |
| `cases.json` | 26 mode-tagged eval cases |
| `generate.py` | answer generation, mirrors `app.py` exactly (no retry — regression cases must stay faithful) |
| `freeze_answers.py` / `answers_25.json` | one frozen answer set used for labeling + judge validation |
| `labels_25.json` | 26 blind hand labels, written before any judge ran |
| `judge_v1.txt` / `judge_v2.txt` | judge prompts (v1: original; v2: + 2 few-shot examples) |
| `judge.py` / `judge_v1_verdicts.json` / `judge_v2_verdicts.json` | judge runner + both verdict sets |
| `prediction.txt` | written before building judge_v2 |
| `run_eval.py` / `run_eval_latest.json` | the one-command eval (live pipeline + assertions + judge_v1, pass rate by mode) |
| `report.md` | this file |
