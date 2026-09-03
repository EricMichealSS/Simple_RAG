# Week 5 — Error Analysis Notes (Track E: Developer Documentation)
Date: 2026-08-26

---

## Seeded Random Sample

**Pool:** 23 traces in traces.jsonl
**Sample size:** 20
**Seed:** 42

**Sampling script:**
```python
import json, random

with open("traces.jsonl") as f:
    traces = [json.loads(l) for l in f if l.strip()]

random.seed(42)
sampled = random.sample(traces, 20)
excluded = [t for t in traces if t not in sampled]

print("SAMPLED (20):")
for t in sampled:
    print(" ", t["trace_id"], "|", t["question"][:60])

print("\nEXCLUDED (3):")
for t in excluded:
    print(" ", t["trace_id"], "|", t["question"][:60])
```

**Script output — excluded 3 trace_ids:**
- `afe5c24d-4005-4997-967a-0bf728ae1ab8` — Can username and password authentication be used…
- `c24d47c4-9c0f-416f-9f44-044a3b04214b` — What is the maximum number of OAuth access-token…
- `4075eb4c-0754-4a6b-a398-7084eb84af89` — What is the authenticated user limit per hour?

**Sampled 20 trace_ids (in order drawn):**
1. `c3b452e3-f832-4dea-afc2-712e75ea0d0b`
2. `01c5c7f6-8e9e-4226-9744-311601b9e888`
3. `7653571c-2c56-4c44-9d9e-58c887293d86`
4. `b524784d-8093-4968-a70a-f1a72a5b3667`
5. `d7d280b9-760e-46ab-96f8-a1a9e589498e`
6. `34b88579-fb09-4cf0-8515-7808401f6b07`
7. `76315343-a05f-4c42-8f0d-6c19344f8c67`
8. `a75e3112-0d6c-4914-96c3-6739a403604d`
9. `9772230d-21b9-4ab8-8eb0-0c151800180d`
10. `4335c1b8-95c8-4303-8988-52ef0f1da6f4`
11. `0947c98a-66c3-4958-b8c7-946aa7407fbf`
12. `ef0b4fc5-4cbf-4352-86b4-f3e23f1b7aee`
13. `03099fdc-f5f1-4407-964f-4e2fb687ecc1`
14. `03c64dfd-a6e9-43b8-a338-0c7bc98012c2`
15. `65b1f8c0-cb17-4fa2-98b0-afcd5eecb334`
16. `49bcc271-35e6-4e55-872c-91c4d8840c72`
17. `610698d4-b8c9-4006-bf9d-7e499116cb94`
18. `9fe19bdb-1809-4d18-b00f-81e1a67dd977`
19. `888b601e-6b41-4a4a-87be-277cbcfabd2e`
20. `fd0c9df9-f70e-46bb-ac47-14fbc3c32e6a`

---

## Open-Coding: 20 Honest Observation Sentences
*(One per trace. No fixes applied. No category labels. What I saw.)*

**NOTE: Zero code changes were made during this step.**

1. **c3b452e3** — The app listed three token types (PAT, GitHub App token, GITHUB_TOKEN) and cited one chunk correctly, but the first retrieval call took 6,255 ms before any answer appeared.

2. **01c5c7f6** — Instead of an answer, the user saw a full ⚠️ error block with five raw chunk excerpts because Groq rejected the generation call with a tokens-per-minute rate limit before the LLM produced a single token.

3. **7653571c** — The app named GITHUB_TOKEN as the recommended mechanism for GitHub Actions workflows, but the three other retrieved chunks about curl, JavaScript, and GitHub CLI methods were not mentioned anywhere in the answer.

4. **b524784d** — The top chunk (rerank score 8.49) contained the SAML SSO token authorization instruction and the answer was correct, but four of the five retrieved chunks had negative rerank scores (−2.11 to −3.00), all of them unrelated rate-limits content.

5. **d7d280b9** — The answer describing OctoClient's purpose was correct, but generation took 11,087 ms and three of the five retrieved chunks came from ApiError and Client.send() sections with negative rerank scores below −1.5.

6. **34b88579** — The app correctly identified the single parameter `request` with default `{}` for `client.getRateLimit()` and cited the right chunk; retrieval and generation completed in under 3 seconds total.

7. **76315343** — The user received a ⚠️ error block with five raw rate-limits chunk excerpts because the HTTP payload exceeded Groq's size limit (413); the LLM was never called despite five relevant chunks being retrieved.

8. **a75e3112** — The app returned "I don't know" and the highest-ranked chunk (rerank score 8.24) discussed the 60-requests-per-hour unauthenticated limit, not Git LFS limits specifically.

9. **9772230d** — The app correctly answered "100 concurrent requests" citing chunk `rate-limits:p4:top:4`, which contained that exact figure in its first 300 characters.

10. **4335c1b8** — The app returned "I don't know" for a question comparing the REST API header `x-ratelimit-reset` with the SDK's `RateLimitError.reset_at`; every retrieved chunk came from rate-limits.pdf and no chunk from errors-reference.md appeared in the results.

11. **0947c98a** — The app returned "I don't know" for the GitHub Enterprise Cloud GITHUB_TOKEN per-repository rate limit; all five retrieved chunks discussed github.com primary limits with no mention of Enterprise Cloud.

12. **ef0b4fc5** — The app correctly described `client.paginate()` iterating all pages and returning concatenated results, including a concrete GET /issues example, but generation took 10,234 ms.

13. **03099fdc** — The app returned "I don't know" in 0.9 ms (no LLM call), while the top retrieved chunk `client-reference:clientsend:2` contains the `follow_redirects` parameter table row but received a negative rerank score of −1.26, triggering the relevance gate to refuse generation.

14. **03c64dfd** — The app correctly identified `identity` as the default value of `map_fn` in `client.paginate()`, cited the right chunk, and answered in under 4 seconds total.

15. **65b1f8c0** — The app correctly answered 5,000 requests per hour for authenticated users with a personal access token, citing chunk `rate-limits:p2:top:2`.

16. **49bcc271** — The user received a ⚠️ error block (413 payload too large) even though the top-ranked chunk `client-reference:clientsend:2` (rerank score 3.09) contains the sentence "It returns a Promise that resolves to the response body" within its first 300 characters.

17. **610698d4** — The user received a ⚠️ error block (413 payload too large) even though the top-ranked chunk `errors-reference:errors-reference-sdk-v3:0` (rerank score 5.78) contains the sentence "Errors are never swallowed" within its first 300 characters; generation took 0 effective tokens but the timer ran 13,534 ms waiting for the failed call.

18. **9fe19bdb** — The app returned "I don't know" for the Git LFS per-minute request limit; the highest-ranked chunk (rerank score 6.93) described the 60-per-hour unauthenticated limit, not Git LFS.

19. **888b601e** — The app correctly answered "5,000 requests per hour" with the right citation after the user resubmitted the same question that had failed with a Groq TPM error in a previous session; retrieval took 9,816 ms.

20. **fd0c9df9** — The app returned "I don't know" for the OAuth access-token per-hour ceiling under secondary rate limits; the five retrieved chunks covered concurrent request limits (100) and per-minute limits (900 points) but none surfaced the OAuth hourly figure.

---

## Replay Evidence

**Trace replayed:** `34b88579-fb09-4cf0-8515-7808401f6b07`
**Question:** "What are the default parameters of client.getRateLimit()?"

**Fields available in trace log (all present — full replay possible):**
- `question` ✅
- `retrieved_chunks[].chunk_id + text_snippet` ✅
- `retrieved_chunks[].rerank_score` ✅
- `model` + `model_params` (temperature, max_tokens) ✅
- `raw_answer` ✅
- `timestamp` ✅

**Replay procedure (from trace alone):**
Build the prompt using the same template with chunk `client-reference:clientgetratelimit:4` as SOURCE 1 (the only chunk with positive rerank score, score = 6.88). The snippet reads:
```
## Client.getRateLimit()
`client.getRateLimit()` returns the rate limit status for the authenticated
user, including the remaining requests and the reset time.

| Parameter | Type | Default | Required |
|---|---|---|---|
| request | object | {} | no |
```
Call groq/compound at temperature=0.1, max_tokens=512.

**Original answer (from trace):**
> "The only parameter for `client.getRateLimit()` is `request`, and its default value is an empty object `{}`. [client-reference:clientgetratelimit:4]"

**Replayed answer (reconstructed from trace content alone):**
> "The only parameter for `client.getRateLimit()` is `request`, with a default value of an empty object `{}` [client-reference:clientgetratelimit:4]."

**Match:** Near-identical. Wording varies by ~3 words; factual content and citation are identical.
**Fields missing from trace:** None — all fields needed to replay were present.

---

## Dated Falsifiable Prediction
**Date committed: 2026-08-26**

**Target mode:** "Groq API returns 413 or TPM error; user sees raw chunk fallback, no generated answer" (Mode 1 in taxonomy.md)

**Current frequency:** 4 out of 20 sampled traces = 20%

**Specific change:** Add a retry wrapper around the `grok_client.chat.completions.create` call in `stream_answer()` with exponential backoff — 2 attempts maximum, 2-second initial delay — and simultaneously reduce `MAX_TOTAL_CHARS` from 1800 to 900 characters.

**Prediction:** By 2026-09-02, applying both changes will drop the "Groq API error blocks all output" mode from 20% (4/20) to under 5% (1/20) on a fresh 20-trace sample drawn with seed=99.

*(Git commit hash: to be added upon commit)*

---

## Why Public Benchmarks Would Have Missed These Failures

Public benchmarks like MMLU and HumanEval evaluate a model's parametric knowledge or code generation ability against fixed answer keys, so they never exercise the HTTP pipeline between the app and the inference provider; Mode 1 (Groq 413/TPM errors blocking all output) is invisible to any benchmark because the benchmark calls the model directly without the retrieval-plus-prompt-assembly step that creates the oversized payload.

Mode 2 (negative reranker score triggering a false "I don't know" in 0.9 ms) is specific to the cross-encoder relevance gate in this pipeline and would not appear on any benchmark because benchmarks do not evaluate retrieval-augmented systems with a learned reranker sitting between the vector store and the LLM.

Mode 5 (all chunk distance values logging as 1.0 due to BM25 placeholder overwrite) is a metadata integrity issue in the pipeline's internal bookkeeping, not an answer quality issue, so a benchmark that scores only final answers would report 100% pass rate on every trace affected by this bug while the debugging signal is silently destroyed.
