# Week 12 — Known weaknesses (top three)

**When each was identified (actual dates, not backdated):** #1 on 2026-11-04 (analysis of the 45-question run; the chunk-text cause found the same session); #2 measured on 2026-11-04 (distance check), though the threshold was flagged "untuned placeholder" in Phase 1 on 2026-11-02 and Phase 5 found the wording-steering trial on 2026-11-03; #3 on 2026-11-03 (Phase 8: the blind label sheet was built and no labels exist). All three were written down on 2026-11-04.

Written 2026-11-04 against the code and evidence of that date (`golden_results.json` from the 45-question live run; 749 tests passing). Chosen by *consequence to a user who trusts the answer*, not by how interesting they are. Each has the evidence, who is hurt, what a fix would have to prove, and its ledger id in `gap_analysis.md`. Re-read after any change to retrieval, the index, the verifier or the model; the date above is when they were true.

## 1. The engine can deliver a wrong answer with a citation that looks like proof

**What.** The verifier checks that every citation names a retrieved chunk, in the caller's version. It does not check that the chunk *says* what the answer claims.

**Evidence.** `easy_11`, trace `d9566c777e434934a0eb76e96d7a9899`: "What is the default value of `allowUnicodeIdentifiers` in the Swagger Codegen 3.x Java generator?" Answer: "**true**" `[swagger_v3_reference:p3:diff:top:4]`. Gold: false. The cited chunk is the 3.x page's "Compared to v2" paragraph, which says 2.x defaults were written inline like "(Default: true)" — an illustration of a *style*, not a value. Every engine check passed (citation exists, in scope, right version); only the judge noticed. The cause upstream is a retrieval miss: the answer-bearing chunk `p3:spec:top:3` was not retrieved because PDF extraction split the option name across lines (`allowUnicodeIdentifie│rs`) (G-COR-13).

**How often.** 1 of 45 on the golden set (2.2%), 1 of 35 answered (2.9%). The set is the author's, reused from earlier weeks and not held out, so this is a floor for hard questions, not an estimate for real traffic.

**Who is hurt.** Anyone who copies a default value, a flag or a limit from an answer. A citation makes it *more* believable.

**What a fix must prove.** `easy_11` no longer answers "true" (either it retrieves the right chunk, or the claim-support check turns it into an abstention) and a regression case for it exists; no currently-correct golden answer is turned into an abstention (re-run all 45; compare with `evidence/phase9/golden_results.json`). Two independent fixes are proposed in `error_taxonomy.md` §4; both need your approval because one rebuilds the index and the other decides whether an unsupported answer is withheld or shown with a warning. Ledger: G-COR-12, G-COR-13.

## 2. Abstention is a property of the model, not of the engine

**What.** When the corpus does not contain the answer, nothing in the engine stops the question reaching the model. The retrieval-distance gate is a placeholder (0.65) and, measured, it never fires.

**Evidence.** `evidence/phase9/distance_check.json`: top-1 cosine distance for the 36 answerable questions is 0.124–0.250; for the 9 unanswerable 0.161–0.334. The ranges overlap and every value is far below 0.65, so the gate admitted 9/9 unanswerable questions; the model refused 9/9 (`model_abstained`), and all 10 abstentions in the run were the model's, none the gate's. The unanswerable questions are adjacent to the corpus (same product, absent feature), which is the hard case. Separately, Phase 5 found that answer *wording* can be steered by a poisoned passage: 1 of 28 live trials passed every output layer (`phase5.md`, G-SEC-1).

**How often a fabricated answer occurred.** 0 of 9 on this set, with one model (`openai/gpt-oss-120b`), one provider, prompt version `sdk-answer-v1`, temperature 0. That is a measurement of this model on these nine questions, not a guarantee. A different model, a different prompt, or a model update can change it, and no engine check would notice.

**Who is hurt.** A user asking about a feature the docs never mention, after a model change.

**What a fix must prove.** A discriminating signal (not a distance threshold: none separates these sets here) evaluated on more unanswerable questions than nine, written by someone other than the engine's author; and an alarm if the refusal rate on a standing unanswerable set drops. Ledger: G-ORC-7, G-SEC-1.

## 3. The headline numbers are not independent evidence

**What.** Every "pass" figure in this project (40/40 suite, 43/45 golden, 34/36 answerable) was produced by tests, a judge or labels that the same author built, tuned against, or did not validate.

**Evidence.**
* The judge (`gpt-oss-20b`) vs a human: **NOT MEASURED**. The blind 23-item sheet exists; zero human labels (owner decision Q7). The ≥85% target is unverified. Historical Week 6 agreement was 80.8% / 76.9%.
* The 45 golden questions are not held out: used in Week 7, and Phase 2 chose retrieval settings by comparing them on 31 of these questions.
* The 40-case suite reached 40/40 after iteration (first full run 37/39; fixtures and one real defect fixed along the way). It was shown sensitive to 9/9 injected defects, with 2 resting on a single case.
* The 29 offline cases test the engine's defences against a scripted worst-case model; they say nothing about how the real model behaves.
* The trajectory evaluation measures rules written by the same author as the specs.
* I read all 45 outputs and agreed with the judge 45/45; I am one reader, not a second labeller.

**Who is hurt.** Whoever decides to ship, or to trust a model change, on these numbers.

**What a fix must prove.** Judge-vs-human agreement on ≥20 outputs labelled blind by a person who is not the author (the command is ready: `eval label-sheet`, `eval agreement`), plus a fresh question set written after the code froze. Ledger: G-EVA-1.

## Next tier (real, smaller, or needing your decision)

* No authentication: the owner id is caller-supplied; uploads are isolated between scopes, not people (G-UP-6).
* A pasted secret is redacted in logs but still reaches the model provider (G-OBS-6, your decision).
* A rejected draft is not retained, so over-blocking cannot be audited (G-OBS-7).
* Throughput is bounded by the provider key, not the engine: Phase 10 (`tenx.md`).
* The CI workflow has not been run on GitHub (`seam_list.md` #27).
