# Week 5 — Failure Mode Taxonomy
**Track E: Developer Documentation RAG**
**Date: 2026-08-26 | Sample: 20 traces (seed=42) from 23 total**

---

| # | Mode Name | Count | Freq % | Severity | Example trace_id |
|---|-----------|-------|--------|----------|-----------------|
| 1 | Groq API returns 413 or TPM error — user sees raw chunk fallback, no generated answer | 4 | 20% | HIGH | `610698d4` |
| 2 | Reranker scores correct top-1 chunk below 0.0; relevance gate fires in <1 ms; "I don't know" returned despite answer being in the retrieved chunk | 1 | 5% | HIGH | `03099fdc` |
| 3 | Specific documented figure not in any of the 5 retrieved chunks — "I don't know" returned for a question the indexed corpus can answer | 2 | 10% | MEDIUM | `fd0c9df9` |
| 4 | Cross-document comparison question retrieves chunks from only one source file — the second document's relevant chunk is absent from results | 1 | 5% | MEDIUM | `4335c1b8` |
| 5 | Every chunk distance value logs as 1.0 across all traces — BM25 placeholder distance overwrites real dense vector distance in the RRF fusion path | 20 | 100% | LOW | `c3b452e3` |

---

## Notes per mode

**Mode 1 — Generation API error (4/20, 20%, HIGH)**
Traces 01c5c7f6, 76315343, 49bcc271, 610698d4. Two failure subtypes: HTTP 413 (payload too large, 3 traces) and Groq TPM rate limit (1 trace). In all four cases the LLM was never called; the user received a ⚠️ fallback block with raw chunk excerpts. Notably traces 49bcc271 and 610698d4 had the correct answer visible in the top chunk's 300-char snippet — the 413 error blocked generation despite the content being available. Severity is HIGH because the user cannot obtain any answer.

**Mode 2 — Relevance gate false refusal (1/20, 5%, HIGH)**
Trace 03099fdc. Question: "What is the default value of follow_redirects?" The correct chunk (`client-reference:clientsend:2`) was retrieved at rank 1, but received rerank score −1.26. The `is_relevant()` function returns False when the top rerank score ≤ 0.0, so the LLM was never called. gen_ms = 0.9 ms confirms no generation occurred. Severity is HIGH: the app gives a confident "I don't know" for a question it could have answered.

**Mode 3 — Answerable question, wrong chunks retrieved (2/20, 10%, MEDIUM)**
Traces fd0c9df9 and 888b601e's excluded pair c24d47c4. Both ask the same question about the OAuth access-token per-hour ceiling under secondary rate limits. Retrieved chunks surface the concurrent request limit (100) and per-minute REST API limit (900 points) but not the OAuth hourly figure. The information exists in rate-limits.pdf but the retrieval pool of 5 chunks missed the specific page/chunk containing it. Severity is MEDIUM: the user is told the documented limit is unknown, which is factually incorrect.

**Mode 4 — Cross-document retrieval failure (1/20, 5%, MEDIUM)**
Trace 4335c1b8. Question asks for the difference between the REST API `x-ratelimit-reset` response header (in rate-limits.pdf) and `RateLimitError.reset_at` (in errors-reference.md). All five retrieved chunks came from rate-limits.pdf; errors-reference.md contributed zero chunks to the result set. The cross-encoder had no errors-reference content to rerank alongside the rate-limits content, making a cross-document comparison structurally impossible. Severity is MEDIUM: user cannot obtain a documented comparison that exists across two indexed files.

**Mode 5 — Distance metadata always 1.0 (20/20, 100%, LOW)**
Every trace in the sample logs `distance: 1.0` for every retrieved chunk regardless of the query. The cause is in `rag_hybrid.py`: during RRF fusion, `id_to_candidate` is first populated with dense results (which carry real distances), then updated with BM25 candidates (which carry placeholder `distance: 1.0`). The `.update()` call overwrites real distances for any chunk that appears in both result sets. Severity is LOW because rerank scores still drive correct ordering and answer quality is unaffected — but the distance field is useless for debugging retrieval quality.

---

## Fix Priority Order

1. **Mode 1** (20%, HIGH) — highest combined impact; users get no answer 1 in 5 queries
2. **Mode 2** (5%, HIGH) — silent "I don't know" for retrievable content; hardest user experience failure to diagnose
3. **Mode 3** (10%, MEDIUM) — misleads user about documented API limits
4. **Mode 4** (5%, MEDIUM) — blocks valid cross-document questions
5. **Mode 5** (100%, LOW) — no user-visible harm, but breaks distance-based debugging

---

## Target for Week 6

**Mode 1.** Add retry with exponential backoff (max 2 retries, 2s initial delay) on Groq 413/TPM errors. Reduce `MAX_TOTAL_CHARS` from 1800 to 900.

**Prediction:** "Groq API returns 413 or TPM error" drops from **20% (4/20)** to **under 5% (1/20)** on a fresh 20-trace sample drawn with seed=99 by 2026-09-02.
