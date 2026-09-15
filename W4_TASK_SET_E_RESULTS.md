# Week 4 Task Set E — Results

## Executive Summary

**Baseline hit-rate@3: 83.3% (10/12)** → **Hybrid BM25+RRF hit-rate@3: 83.3% (10/12)**  
**Baseline p50 latency: 36.5ms** → **Hybrid p50 latency: 35.5ms (-1.1ms)**

**Change made:** Added BM25 + RRF fusion (k=60) to dense vector retrieval  
**Justification:** Tally showed 2/2 failures were R-type exact-symbol retrieval failures — BM25 is designed for exact token matching

**Result:** Fixed both original R-failures (q8, q9) but introduced 2 new misses (q6, q12). Net hit-rate unchanged. Latency slightly improved.

**Shipping decision:** **DO NOT SHIP** — no net hit-rate improvement; hybrid adds complexity and instability (breaks 2 previously working queries) for zero net gain.

---

## 1. Golden Set (12 Real Developer Questions)

| ID | Question | Expected Chunk | Token Type | Token |
|----|----------|----------------|------------|-------|
| q1 | What is the default value of retry_backoff_ms in the OctoClient send method? | `client-reference:clientsend:2` | symbol | `retry_backoff_ms` |
| q2 | What is the HTTP status code for RateLimitError in the OctoKit SDK v3? | `errors-reference:ratelimiterror:2` | error_code | `429` |
| q3 | What is the primary rate limit for unauthenticated requests per hour? | `rate-limits:p2:top:2` | symbol | `60 requests per hour` |
| q4 | What is the version string for the OctoKit SDK documented in the reference pages? | `client-reference:octoclient-reference-sdk-v3:0` | version_string | `v3` |
| q5 | How do you authenticate with a personal access token using curl? | `authentication:p2:top:3` | procedure | — |
| q6 | What are the required parameters for creating a webhook in the OctoKit SDK v3? | `events-reference:createwebhook:1` | parameter_list | — |
| q7 | What error is thrown when the SDK exceeds the rate limit? | `errors-reference:ratelimiterror:2` | error_class | `RateLimitError` |
| q8 | How do you create an issue using the GitHub REST API with gh CLI? | `getting-started:p8:top:12` | procedure | — |
| q9 | What is the default value of max_retries in the OctoClient send method? | `client-reference:clientsend:2` | symbol | `max_retries` |
| q10 | What is the default scope for getAccessToken in the OctoKit SDK v3? | `auth-reference:getaccesstoken:2` | symbol | `repo, workflow` |
| q11 | What is the maximum number of concurrent requests allowed for the GitHub REST API? | `rate-limits:p4:top:4` | symbol | `100 concurrent requests` |
| q12 | How do you upload a release asset using the OctoKit SDK v3? | `uploads-reference:uploadreleaseasset:1` | procedure | — |

**4+ exact-token questions:** q1, q2, q3, q4, q9, q10, q11 (7 questions with exact tokens)

---

## 2. Baseline Hit-Rate@3 (Dense Vector Only)

| ID | Question | Hit? | Rank | Latency |
|----|----------|------|------|---------|
| q1 | retry_backoff_ms default | ✓ HIT | 1 | 379ms |
| q2 | RateLimitError status code | ✓ HIT | 1 | 41ms |
| q3 | primary rate limit | ✓ HIT | 1 | 52ms |
| q4 | SDK version string | ✓ HIT | 1 | 38ms |
| q5 | PAT authentication | ✓ HIT | 1 | 43ms |
| q6 | webhook required params | ✓ HIT | 1 | 14ms |
| q7 | rate limit error class | ✓ HIT | 1 | 15ms |
| q8 | create issue with gh CLI | ✗ MISS | — | 43ms |
| q9 | max_retries default | ✗ MISS | — | 13ms |
| q10 | getAccessToken default scope | ✓ HIT | 1 | 12ms |
| q11 | max concurrent requests | ✓ HIT | 1 | 35ms |
| q12 | upload release asset | ✓ HIT | 2 | 13ms |

**Baseline: 10/12 = 83.3% hit-rate@3 | p50 latency: 36.5ms**

---

## 3. Failure Labelling (Inspection View)

### Failure Tally
| Label | Count |
|-------|-------|
| **R (Retrieval)** | 2 |
| **G (Generation)** | 0 |
| **Not-In-Corpus** | 0 |

### Per-Failure Evidence

#### q8: "How do you create an issue using the GitHub REST API with gh CLI?"
- **Label:** R (Retrieval fetched bad context)
- **Expected chunk:** `getting-started:p8:top:12` (contains `gh api --method POST /repos/REPO-OWNER/REPO-NAME/issues` code example)
- **Evidence:** Expected chunk exists in corpus but ranks #6+ (distance >0.25). Top-3 returns: intro page (`p1:top:0`), auth (`p5:2-authenticate:7`), endpoint selection (`p6:3-choose-an-endpoint:9`) — none contain the issue creation code example.
- **Retrieved top-3:** `getting-started:p1:top:0` (intro), `getting-started:p5:2-authenticate:7` (auth), `getting-started:p6:3-choose-an-endpoint:9` (endpoint selection)

#### q9: "What is the default value of max_retries in the OctoClient send method?"
- **Label:** R (Retrieval fetched bad context)
- **Expected chunk:** `client-reference:clientsend:2` (contains parameter table with `max_retries | number | 3 | no`)
- **Evidence:** Expected chunk exists in corpus but ranks #4 (distance 0.3005). Top-3 returns: constructor params (`octoclient-constructor:1`), error class (`ratelimiterror:2`), rate limit headers (`getting-started:p9:top:13`) — none contain the Client.send parameter table.
- **Retrieved top-3:** `client-reference:octoclient-constructor:1`, `errors-reference:ratelimiterror:2`, `getting-started:p9:top:13`

---

## 4. Single Change Justification

**Change:** Added BM25 + RRF fusion (k=60) to dense vector retrieval  
**Code diff:** New `rag_hybrid.py` with `BM25` class, `reciprocal_rank_fusion()`, and `hybrid_retrieve()` — drops in as `retrieve()` replacement

**Justification from tally:**
- Both failures (q8, q9) are **R-type exact-symbol failures** — the correct chunks exist but dense embeddings rank them too low
- BM25 excels at exact token matching (symbols like `max_retries`, command tokens like `gh api --method POST`)
- Dense embeddings are structurally bad at exact symbol retrieval (semantic similarity ≠ exact match)
- BM25 + RRF is the canonical fix for exact-token R-failures per the module guidance

---

## 5. After Hit-Rate@3 (BM25+RRF Hybrid)

| ID | Question | Hit? | Rank | Latency |
|----|----------|------|------|---------|
| q1 | retry_backoff_ms default | ✓ HIT | 1 | 1117ms |
| q2 | RateLimitError status code | ✓ HIT | 1 | 43ms |
| q3 | primary rate limit | ✓ HIT | 1 | 40ms |
| q4 | SDK version string | ✓ HIT | 1 | 40ms |
| q5 | PAT authentication | ✓ HIT | 1 | 44ms |
| q6 | webhook required params | ✗ MISS | — | 14ms |
| q7 | rate limit error class | ✓ HIT | 1 | 15ms |
| q8 | create issue with gh CLI | ✓ HIT | 1 | 31ms |
| q9 | max_retries default | ✓ HIT | 1 | 15ms |
| q10 | getAccessToken default scope | ✓ HIT | 1 | 19ms |
| q11 | max concurrent requests | ✓ HIT | 1 | 42ms |
| q12 | upload release asset | ✗ MISS | — | 14ms |

**Hybrid: 10/12 = 83.3% hit-rate@3 | p50 latency: 35.5ms**

---

## 6. Before → After Comparison

| Metric | Baseline | Hybrid | Delta |
|--------|----------|--------|-------|
| **Hit-rate@3** | 83.3% (10/12) | 83.3% (10/12) | **0.0%** |
| **p50 Latency** | 36.5ms | 35.5ms | **-1.1ms** |

### Per-Question Fixed/Unfixed Table

| ID | Baseline | Hybrid | Status | Notes |
|----|----------|--------|--------|-------|
| q1 | HIT | HIT | — | Still works |
| q2 | HIT | HIT | — | Still works |
| q3 | HIT | HIT | — | Still works |
| q4 | HIT | HIT | — | Still works |
| q5 | HIT | HIT | — | Still works |
| q6 | HIT | **MISS** | **REGRESSION** | BM25 returns page overview instead of sub-section |
| q7 | HIT | HIT | — | Still works |
| q8 | MISS | **HIT** | **FIXED** | BM25 finds `gh api --method POST` exact tokens |
| q9 | MISS | **HIT** | **FIXED** | BM25 finds `max_retries` exact symbol |
| q10 | HIT | HIT | — | Still works |
| q11 | HIT | HIT | — | Still works |
| q12 | HIT | **MISS** | **REGRESSION** | BM25 returns page overview instead of sub-section |

**Fixed (original R-failures):** q8, q9 ✓  
**Regressed (previously working):** q6, q12 ✗  
**Net change:** 0

---

## 7. Failure Analysis: What the Change Fixed vs. Left Untouched

| Original R-Failure | Fixed? | Evidence |
|--------------------|--------|----------|
| q8: gh CLI issue creation | **YES** | BM25 matched exact tokens `gh api --method POST` in chunk `getting-started:p8:top:12` |
| q9: max_retries default | **YES** | BM25 matched exact token `max_retries` in chunk `client-reference:clientsend:2` |

| Previously Working | Now Broken? | Cause |
|--------------------|-------------|-------|
| q6: webhook params | **YES** | RRF fused BM25 page-overview chunk (`events-and-webhooks-reference-sdk-v3:0`) above dense sub-section |
| q12: upload release asset | **YES** | RRF fused BM25 page-overview chunk (`uploads-reference-sdk-v3:0`) above dense sub-section |

**Root cause of regressions:** BM25 matches page titles ("Events and webhooks reference", "Uploads reference") which contain query terms broadly. RRF (k=60) fuses ranks such that page-overview chunks outrank specific sub-sections for these queries.

---

## 8. Shipping Decision

### DO NOT SHIP

**Reasoning:**
1. **Zero net hit-rate improvement** (83.3% → 83.3%)
2. **Instability:** Fixed 2 R-failures but broke 2 previously working queries
3. **Complexity cost:** Hybrid adds BM25 index, RRF fusion, dual retrieval paths
4. **Latency variance:** q1 latency spiked to 1117ms (BM25 scoring 62 docs)

**What would need to change to ship:**
- Tune RRF `k` parameter (lower k = more dense weight)
- Or use cross-encoder rerank instead (different change, not allowed this week)
- Or implement MMR for diversity (bonus challenge)

**Honest assessment:** The change successfully fixed the exact-symbol R-failures identified in the tally, but RRF fusion introduced instability for sub-section retrieval. The net zero gain with added complexity and regressions means this specific change is not worth shipping.

---

## 9. Code Diff

### New File: `rag_hybrid.py`
```python
# BM25 + RRF hybrid retrieval
class BM25:
    def __init__(self, corpus, k1=1.5, b=0.75): ...
    def score(self, query, doc_idx): ...
    def top_k(self, query, k=60): ...

def reciprocal_rank_fusion(rank_lists, k=60, top_k=5): ...

def hybrid_retrieve(question, top_k=5, rrf_k=60, bm25_k=60):
    # 1. Dense vector top-25
    # 2. BM25 top-60
    # 3. RRF fusion (k=60)
    # 4. Return fused top-k
```

### Integration
```python
# In rag_hybrid.py
def retrieve(question, top_k=TOP_K, collection_name=COLLECTION_NAME, where=None):
    if collection_name != COLLECTION_NAME or where is not None:
        from rag import retrieve as original_retrieve
        return original_retrieve(question, top_k, collection_name, where)
    return hybrid_retrieve(question, top_k=top_k)
```

---

## 10. Files Generated

| File | Purpose |
|------|---------|
| `golden_set.jsonl` | 12 questions with expected chunk_ids |
| `baseline_results.json` | Dense-only evaluation results |
| `hybrid_results.json` | BM25+RRF evaluation results |
| `failure_analysis.json` | R/G/Not-In-Corpus labels with evidence |
| `rag_hybrid.py` | Hybrid retrieval implementation |
| `evaluate_baseline.py` | Baseline evaluation script |
| `evaluate_hybrid.py` | Hybrid evaluation script |

---

## Appendix: Latency Distribution

| Percentile | Baseline | Hybrid |
|------------|----------|--------|
| p50 | 36.5ms | 35.5ms |
| p90 | 52.3ms | 43.6ms |
| p99 | 379ms | 1117ms |

**Note:** Hybrid adds BM25 scoring overhead (~1ms per query) but q1 outlier (1117ms) suggests BM25 scoring 62 docs has tail latency.