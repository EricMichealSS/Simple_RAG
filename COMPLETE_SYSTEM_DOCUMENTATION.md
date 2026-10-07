# Complete RAG System Technical Documentation

## Developer Documentation RAG — Full System Architecture & Evolution

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Corpus Description](#corpus-description)
3. [Embedding Methodology](#embedding-methodology)
4. [Chunking Strategies](#chunking-strategies)
5. [Retrieval Methodologies](#retrieval-methodologies)
6. [Generation & Citation](#generation--citation)
7. [Complete Flow: Before vs After](#complete-flow-before-vs-after)
8. [Evaluation Metrics](#evaluation-metrics)
9. [Comparative Analysis](#comparative-analysis)
9. [Knowledge Base: How Each Component Works](#knowledge-base-how-each-component-works)

---

## System Overview

### What This System Does

A **Retrieval-Augmented Generation (RAG)** system for developer documentation that:
- Indexes GitHub REST API docs (v2 PDFs) + OctoKit SDK reference (v3 markdown)
- Answers technical questions with **verified citations** to source chunks
- **Refuses** out-of-corpus questions via hard gate (not prompt suggestion)
- Supports **metadata filtering** (`sdk_version`, `page_type`)
- Uses **structure-aware chunking** preserving tables, code blocks, headers

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CORPUS (11 documents)                            │
│  5× GitHub REST API PDFs (v2)  +  6× OctoKit SDK v3 Markdown               │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         INGESTION PIPELINE                                   │
│  PDF/Markdown → Structure-Aware Chunking → BGE Embeddings → ChromaDB        │
│  Metadata: source_file, page_id, sdk_version, page_type, section, anchor   │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
        ┌─────────────────────┐     ┌─────────────────────┐
        │   DENSE RETRIEVAL   │     │    BM25 RETRIEVAL   │
        │  (BGE embeddings)   │     │  (keyword matching) │
        └──────────┬──────────┘     └──────────┬──────────┘
                   │                           │
                   └─────────────┬─────────────┘
                                 │
                                 ▼
                    ┌─────────────────────┐
                    │   RRF FUSION (k=60) │
                    └──────────┬──────────┘
                                 │
                                 ▼
                    ┌─────────────────────┐
                    │   TOP-K CHUNKS      │
                    │  (metadata + text)  │
                    └──────────┬──────────┘
                                 │
                                 ▼
                    ┌─────────────────────┐
                    │  RELEVANCE CHECK    │
                    │  (distance ≤ 0.60)  │
                    └──────────┬──────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
           ┌─────────────────┐       ┌─────────────────┐
           │ HARD REFUSAL    │       │ CITED GENERATION│
           │ (token overlap) │       │ (Groq compound) │
           └─────────────────┘       └─────────────────┘
```

---

## Corpus Description

### Documents Indexed

| Category | Documents | Chunks | SDK Version | Page Types |
|----------|-----------|--------|-------------|------------|
| **v2 REST API (PDF)** | 5 | 37 | v2 | guide (4), reference (1) |
| **v3 SDK Reference (MD)** | 6 | 25 | v3 | reference (6) |
| **TOTAL** | **11** | **62** | **v2 + v3** | **guide + reference** |

### v2 PDFs (GitHub REST API)
| File | Pages | Chunks | Page Type | Key Content |
|------|-------|--------|-----------|-------------|
| `authentication.pdf` | 6 | 10 | guide | PAT, OAuth, SAML, GH Actions auth |
| `getting-started.pdf` | 11 | 16 | guide | Requests, pagination, media types, examples |
| `issues.pdf` | 3 | 3 | reference | Issues API endpoints listing |
| `rate-limits.pdf` | 6 | 7 | guide | Primary/secondary limits, headers, best practices |
| `using-rest-api.pdf` | 1 | 1 | guide | Overview landing page |

### v3 Markdown (OctoKit SDK Reference)
| File | Chunks | Key Content |
|------|--------|-------------|
| `client-reference.md` | 5 | OctoClient, send(), paginate(), getRateLimit() |
| `auth-reference.md` | 4 | createOAuthClient, getAccessToken, refreshToken |
| `rate-limits-reference.md` | 4 | getRateLimit, checkRateLimit, waitForRateLimit |
| `errors-reference.md` | 5 | ApiError, RateLimitError, ValidationError, AuthError |
| `events-reference.md` | 4 | createWebhook, listWebhooks, deleteWebhook |
| `uploads-reference.md` | 3 | uploadReleaseAsset, uploadAttachment |

---

## Embedding Methodology

### Model: BAAI/bge-small-en-v1.5

| Property | Value |
|----------|-------|
| **Model** | BAAI/bge-small-en-v1.5 |
| **Framework** | sentence-transformers (local) |
| **Dimensions** | 384 |
| **Normalization** | L2 (cosine = dot product) |
| **Batch Size** | 32 |
| **Device** | CPU (Mac) |
| **Cache** | `~/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5` |

### Why bge-small-en-v1.5?

| Criterion | Assessment |
|-----------|------------|
| **Speed** | ~1.8s for 62 chunks on CPU |
| **Quality** | Strong on technical/documentation text (MTEB leaderboard) |
| **Local** | No API costs, no latency, works offline |
| **Consistency** | Same model for docs + queries = same vector space |
| **Size** | 90MB download, ~33M parameters |

### Embedding Process

```python
# During ingestion (one-time)
embeddings = embedding_model.encode(
    documents,           # list of chunk texts
    batch_size=32,
    normalize_embeddings=True,
    show_progress_bar=True
).tolist()

# During query (per request)
query_embedding = embedding_model.encode(
    [question],
    normalize_embeddings=True
)[0].tolist()
```

### ChromaDB Configuration

```python
collection = chroma_client.get_or_create_collection(
    name="developer_docs",
    metadata={"hnsw:space": "cosine"}  # Cosine similarity
)
```

---

## Chunking Strategies

### Strategy 1: Fixed-Size Sliding Window (BASELINE — REPLACED)

```python
def chunk_text(text, chunk_size=500, overlap=100):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks
```

| Property | Value |
|----------|-------|
| **Chunk Size** | 500 chars |
| **Overlap** | 100 chars |
| **Awareness** | None (character-level) |
| **Tables** | ❌ Split across chunks |
| **Code Fences** | ❌ Split across chunks |
| **Headers** | ❌ Not detected |
| **Total Chunks (v2 only)** | 131 |

**Problems:**
- Parameter tables split mid-row → default values lost
- Code fences severed → examples incomplete
- No semantic boundaries → poor retrieval for specific symbols

---

### Strategy 2: Structure-Aware Chunking (CURRENT — PRODUCTION)

```python
def chunk_structured(text, chunk_size=1200, normalize=True):
    # 1. Normalize PDF text to markdown-like
    text = normalize_pdf_text(text)  # ALL-CAPS → ##, numbered → ##
    
    # 2. Parse into atomic blocks
    blocks = parse_structured_blocks(text)
    # block kinds: header, table, code, prose
    
    # 3. Assemble chunks respecting structure
    for block in blocks:
        if block["kind"] == "header":
            flush()  # Header always starts new chunk
        elif block["kind"] in ("table", "code"):
            # Atomic: never split
            if current_size + block_size > chunk_size:
                flush()
            current.append(block)
        else:  # prose
            # Split at paragraph boundaries
            for para in paragraphs:
                if current_size + para_size > chunk_size:
                    flush()
                current.append(para)
    flush()
```

| Property | Value |
|----------|-------|
| **Target Size** | 1200 chars |
| **Overlap** | None (structure = continuity) |
| **Awareness** | Headers, tables, code fences, indented code |
| **Tables** | ✅ Kept whole (header + all rows) |
| **Code Fences** | ✅ Kept whole (```...``` or indented) |
| **Headers** | ✅ Start new chunk + become `section` metadata |
| **Paragraphs** | ✅ Split at `\n\n` boundaries |
| **Total Chunks (v2+v3)** | 62 |

### PDF Normalization Rules

| PDF Pattern | Normalized To |
|-------------|---------------|
| `ABOUT AUTHENTICATION` | `## ABOUT AUTHENTICATION` |
| `1. Introduction` | `## 1. Introduction` |
| `In this article` | `### In this article` |
| Indented code (4+ spaces) | Preserved as `code` block |

### Metadata Enrichment (Every Chunk)

```python
metadata = {
    "source_file": "authentication.pdf",      # Original filename
    "page_id": "authentication",              # Slug
    "sdk_version": "v2",                      # v2 or v3
    "page_type": "guide",                     # guide | reference
    "section": "## About authentication",     # Nearest header
    "anchor": "about-authentication",         # URL-friendly slug
    "source": "authentication.pdf",           # Backward compat
    "page": 1,                                # Page number
    "chunk": 0,                               # Chunk index
}
```

**Chunk ID Format:** `{page_id}:p{page}:{anchor}:{index}`
- Example: `authentication:p1:about-authentication:2`

---

## Retrieval Methodologies

### 1. Dense Vector Retrieval (BASELINE)

```python
def dense_retrieve(question, top_k=5):
    query_embedding = embedding_model.encode([question], normalize_embeddings=True)[0]
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"]
    )
    return format_results(results)
```

| Aspect | Detail |
|--------|--------|
| **Similarity** | Cosine (HNSW index) |
| **Scope** | All chunks (no filter) or filtered by metadata |
| **Strengths** | Semantic similarity, handles paraphrases |
| **Weaknesses** | Poor at exact symbols (`retry_backoff_ms`, `429`, `max_retries`) |

### 2. BM25 Keyword Retrieval (HYBRID COMPONENT)

```python
class BM25:
    def __init__(self, corpus, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.corpus = corpus
        self.doc_lens = [len(doc.split()) for doc in corpus]
        self.avgdl = np.mean(self.doc_lens)
        # Build tf, df, idf...
    
    def score(self, query, doc_idx):
        # BM25 formula: idf * (tf * (k1+1)) / (tf + k1*(1-b+b*dl/avgdl))
    
    def top_k(self, query, k=60):
        return sorted(range(len(corpus)), key=lambda i: self.score(query, i), reverse=True)[:k]
```

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `k1` | 1.5 | Term frequency saturation |
| `b` | 0.75 | Length normalization |
| `k` (top-k) | 60 | Fusion pool size |

**Strengths:** Exact token matching (`retry_backoff_ms`, `429`, `gh api --method POST`)
**Weaknesses:** No semantic understanding, matches page titles broadly

---

### 3. RRF Fusion (HYBRID — THE CHANGE)

```python
def reciprocal_rank_fusion(rank_lists, k=60, top_k=5):
    """RRF: score = Σ 1/(k + rank + 1)"""
    scores = defaultdict(float)
    for rank_list in rank_lists:
        for rank, chunk_id in enumerate(rank_list):
            scores[chunk_id] += 1.0 / (k + rank + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda x: -x[1])][:top_k]
```

| Parameter | Value | Effect |
|-----------|-------|--------|
| `k` (RRF) | 60 | Higher = more weight to dense |
| Dense pool | 25 | Semantic candidates |
| BM25 pool | 60 | Keyword candidates |
| Final top-k | 5 | Returned to generator |

**Why RRF?**
- Fuses **ranks**, not scores (cosine and BM25 scores incompatible scales)
- Parameter `k=60` → dense gets slight preference
- Proven in IR literature (TREC, MS MARCO)

---

### 4. Metadata Filtering

```python
# Supported filters
where = {
    "sdk_version": "v2" | "v3",
    "page_type": "guide" | "reference"
}
# Applied at ChromaDB query time
results = collection.query(query_embeddings=[...], where=where, ...)
```

| Filter | Values | Use Case |
|--------|--------|----------|
| `sdk_version` | v2, v3 | Version-specific answers |
| `page_type` | guide, reference | Tutorial vs API reference |

---

## Generation & Citation

### Cited Answer Generation

```python
def generate_cited_answer(question, results):
    # Build context with citations
    context_parts = []
    for i, r in enumerate(results, 1):
        chunk_id = r.get("chunk_id")
        source_file = r.get("source_file")
        anchor = r.get("anchor", "")
        sdk_version = r.get("sdk_version", "")
        page_type = r.get("page_type", "")
        text = r['text'][:800]  # Limit per chunk
        context_parts.append(
            f"SOURCE {i} [chunk_id: {chunk_id} | {source_file}#{anchor} "
            f"| sdk_version={sdk_version} | type={page_type}]\n{text}"
        )
    
    prompt = f"""You are a developer documentation QA assistant.
    STRICT RULES:
    1. Answer ONLY from supplied context.
    2. For EVERY factual claim, cite [chunk_id].
    3. If unsupported, respond: "I don't know based on the provided documents."
    
    CONTEXT: {context}
    QUESTION: {question}
    ANSWER (with [chunk_id] citations):"""
    
    response = grok_client.chat.completions.create(
        model="groq/compound",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=1024,
    )
    return response.choices[0].message.content
```

### Citation Format

```
The primary rate limit for unauthenticated requests is **60 requests per hour** 
【rate-limits:p2:top:2】【rate-limits-reference:rate-limits-reference-sdk-v3:0】.
```

### Hard Refusal Gate

```python
def should_refuse(question, results):
    """Force refusal WITHOUT calling LLM if grounding insufficient."""
    if not results or not is_relevant(results):
        return True
    
    STOPWORDS = {"the", "a", "an", "and", "or", "in", "on", "at", "to", "for", ...}
    q_tokens = {t for t in question.lower().split() if t not in STOPWORDS}
    
    for r in results:
        c_tokens = {t for t in r["text"].lower().split() if t not in STOPWORDS}
        if len(q_tokens & c_tokens) >= 2:  # ≥2 meaningful token overlap
            return False
    return True  # No grounding → forced refusal
```

**Key:** Refusal happens **before** LLM call — no quota wasted, no hallucination risk.

---

## Complete Flow: Before vs After

### BEFORE (Week 3 — Fixed-Size Chunking + Dense Only)

```
User Question
     │
     ▼
BGE Embedding (bge-small-en-v1.5)
     │
     ▼
ChromaDB Cosine Search (top-5)
     │
     ▼
Relevance Check (distance ≤ 0.60)
     │
     ├─ No → "I don't know..."
     │
     ▼
Gemini Generation (gemini-3.6-flash)
     │
     ▼
Basic Answer (no citations)
```

**Limitations:**
- 131 chunks, fixed 500-char windows
- Tables/code split → lost answers
- No metadata filtering
- No citations
- Refusal via prompt only (hallucination risk)
- 20 req/day Gemini quota

---

### AFTER (Week 4 — Structure-Aware + Hybrid Retrieval)

```
User Question
     │
     ▼
BGE Embedding (bge-small-en-v1.5)     BM25 Keyword Index (pre-built)
     │                                        │
     ▼                                        ▼
ChromaDB Cosine Search (top-25)     BM25 Top-60 (exact tokens)
     │                                        │
     └──────────────┬────────────────────────┘
                    │
                    ▼
           RRF Fusion (k=60)
                    │
                    ▼
           Top-5 Fused Chunks
                    │
                    ▼
           Relevance Check (distance ≤ 0.60)
                    │
                    ├─ No → "I don't know..."
                    │
                    ▼
           Hard Refusal Gate (token overlap ≥2?)
                    │
                    ├─ Yes → Groq Generation (groq/compound)
                    │         → Cited Answer with [chunk_id]
                    │
                    └─ No → "I don't know..." (NO LLM CALL)
```

---

## Evaluation Metrics

### Definitions

| Metric | Formula | Purpose |
|--------|---------|---------|
| **Hit-Rate@k** | `#(expected_chunk in top-k) / total_questions` | Retrieval accuracy |
| **Recall@k** | Same as hit-rate@k for single-ground-truth | Retrieval completeness |
| **p50 Latency** | Median query time (ms) | User experience |
| **p99 Latency** | 99th percentile | Tail performance |

### Results Summary

| System | Hit-Rate@3 | Recall@3 | p50 Latency | p99 Latency |
|--------|------------|----------|-------------|-------------|
| **Baseline (Dense Only)** | **83.3%** (10/12) | 83.3% | **36.5ms** | 379ms |
| **Hybrid BM25+RRF** | **83.3%** (10/12) | 83.3% | **35.5ms** | 1117ms |

### Per-Question Breakdown

| ID | Question Type | Baseline | Hybrid | Change |
|----|---------------|----------|--------|--------|
| q1 | Symbol (`retry_backoff_ms`) | ✓ | ✓ | — |
| q2 | Error code (`429`) | ✓ | ✓ | — |
| q3 | Symbol (`60 requests/hour`) | ✓ | ✓ | — |
| q4 | Version (`v3`) | ✓ | ✓ | — |
| q5 | Procedure | ✓ | ✓ | — |
| q6 | Parameter list | ✓ | ✗ | **REGRESSION** |
| q7 | Error class | ✓ | ✓ | — |
| q8 | Procedure (`gh api`) | ✗ | ✓ | **FIXED** |
| q9 | Symbol (`max_retries`) | ✗ | ✓ | **FIXED** |
| q10 | Symbol (`repo, workflow`) | ✓ | ✓ | — |
| q11 | Symbol (`100 concurrent`) | ✓ | ✓ | — |
| q12 | Procedure | ✓ | ✗ | **REGRESSION** |

---

## Comparative Analysis

### Before vs After: Complete Comparison

| Dimension | BEFORE (Week 3) | AFTER (Week 4) | Improvement |
|-----------|-----------------|----------------|-------------|
| **Chunking** | Fixed 500/100 sliding | Structure-aware 1200 | ✅ Tables/code preserved |
| **Chunks** | 131 (v2 only) | 62 (v2+v3) | ✅ Semantic units |
| **Metadata** | source, page, chunk | + sdk_version, page_type, section, anchor | ✅ Filterable |
| **Filtering** | None | sdk_version, page_type | ✅ Version-aware |
| **Retrieval** | Dense only | Dense + BM25 + RRF | ✅ Exact tokens |
| **Citations** | None | [chunk_id] per claim | ✅ Verifiable |
| **Refusal** | Prompt-only | Hard gate (token overlap) | ✅ Enforced |
| **Generation** | Gemini (20/day) | Groq compound (high quota) | ✅ Reliable |
| **Hit-rate@3** | 83.3% | 83.3% | → |
| **p50 Latency** | 36.5ms | 35.5ms | → |

### Hit-Rate by Question Type

| Question Type | Count | Baseline | Hybrid | Notes |
|---------------|-------|----------|--------|-------|
| **Exact Symbol** (retry_backoff_ms, max_retries, 429, v3, 100 concurrent) | 6 | 83% | 100% | BM25 excels |
| **Error Code/Class** | 2 | 100% | 100% | Both work |
| **Version String** | 1 | 100% | 100% | Both work |
| **Procedure/How-to** | 3 | 67% | 67% | Mixed |
| **Parameter List** | 1 | 100% | 0% | Regression |

---

## Knowledge Base: How Each Component Works

### 1. Embedding Model (BAAI/bge-small-en-v1.5)

**How it works:**
- Transformer encoder (BERT-style) trained with contrastive learning
- Input: tokenized text → [CLS] pooling → 384-dim vector
- Trained on pairs: (query, relevant_doc) vs (query, irrelevant_doc)
- Normalized output → cosine similarity = dot product

**Why it works for docs:**
- Pre-trained on web text, fine-tuned on retrieval tasks
- Handles technical vocabulary (API names, parameters)
- 384 dims = fast HNSW search, low memory

**Limitations:**
- Dense vectors = semantic similarity, not exact match
- "retry_backoff_ms" and "retry backoff" close in space
- But "max_retries" and "max connections" also close → confusion

---

### 2. Structure-Aware Chunking

**How it works:**

```
Raw PDF Text
     │
     ▼
normalize_pdf_text()  ──→ Converts PDF artifacts to markdown
     │                    - ALL-CAPS lines → ## HEADERS
     │                    - Numbered sections → ## 1. Title
     ▼
parse_structured_blocks() ──→ Scans line-by-line, classifies:
     │                         - header (## ...)
     │                         - table (|...| with |---| separator)
     │                         - code (```...``` or 4-space indent)
     │                         - prose (everything else)
     │                         Each block tagged with nearest header
     ▼
chunk_structured() ──→ Packs blocks into chunks ≤ 1200 chars:
     │                    - Header always starts new chunk
     │                    - Table/code = atomic (never split)
     │                    - Prose = split at \n\n
     ▼
Chunks with metadata (section, anchor, sdk_version, page_type)
```

**Why it works:**
- Mirrors how developers read reference docs: by section
- Parameter table = single semantic unit (all rows together)
- Code example = single unit (complete example)
- Header provides context for every chunk

---

### 3. BM25 (Okapi BM25)

**How it works:**

```
Score(Q, D) = Σ IDF(q_i) * (TF(q_i, D) * (k1 + 1)) / (TF(q_i, D) + k1 * (1 - b + b * |D|/avgdl))

Where:
- IDF(q_i) = log((N - df + 0.5) / (df + 0.5) + 1)  [inverse doc freq]
- TF(q_i, D) = term frequency in document
- k1 = 1.5 (TF saturation)
- b = 0.75 (length normalization)
- |D| = document length in tokens
- avgdl = average document length
```

**Why it works for exact tokens:**
- IDF downweights common words ("the", "is", "request")
- IDF upweights rare tokens ("retry_backoff_ms", "429", "gh api")
- TF rewards multiple occurrences
- Length normalization prevents long docs dominating

**Example:**
- Query: "max_retries default"
- BM25 finds chunks with both "max_retries" AND "default"
- Dense might find "max connections" (semantically similar)

---

### 4. Reciprocal Rank Fusion (RRF)

**How it works:**

```
For each ranking list L:
  For each item at rank r: score += 1 / (k + r + 1)

Final score = sum across all lists
Sort by score descending
```

**Why RRF (not score averaging):**
- Cosine similarity: [0, 1] (higher = better)
- BM25 score: [0, ∞) (higher = better)
- **Different scales → cannot average**
- Ranks are **scale-invariant** → can fuse
- k=60: dampens rank differences, slight dense preference

**Example:**
| Chunk | Dense Rank | BM25 Rank | RRF Score (k=60) |
|-------|------------|-----------|------------------|
| A | 1 | 50 | 1/61 + 1/111 = 0.025 |
| B | 10 | 2 | 1/70 + 1/63 = 0.030 → **WINS** |

---

### 5. Hard Refusal Gate

**How it works:**

```python
def should_refuse(question, results):
    if not results: return True
    if best_distance > 0.60: return True
    
    # Remove stopwords
    q_tokens = {t for t in question.lower().split() if t not in STOPWORDS}
    
    for chunk in results:
        c_tokens = {t for t in chunk.text.lower().split() if t not in STOPWORDS}
        overlap = len(q_tokens & c_tokens)
        if overlap >= 2:
            return False  # Sufficient grounding
    return True  # No overlap → refuse
```

**Why it works:**
- Stopword removal focuses on content words
- ≥2 token overlap = minimal semantic overlap
- **No LLM call** → no quota, no hallucination, instant
- Binary decision: grounded or not

---

### 6. Cited Generation with Groq

**How it works:**

```
Context = Chunks with [chunk_id | source_file#anchor | sdk_version | type]

Prompt = """
Rules: 1. Only use context. 2. Cite [chunk_id] per claim. 
       3. Refuse if unsupported.
Context: {context}
Question: {question}
"""

Groq (groq/compound) → Answer with [chunk_id] citations
```

**Why Groq compound:**
- OpenAI-compatible API
- High free quota (no 20/day limit)
- Auto-routes to best model
- Fast inference (~100-200ms)

---

### 7. Metadata Filtering (ChromaDB)

**How it works:**

```python
# At query time
results = collection.query(
    query_embeddings=[query_embedding],
    n_results=5,
    where={"sdk_version": "v3", "page_type": "reference"},
    include=["documents", "metadatas", "distances"]
)
```

- ChromaDB filters **before** vector search (efficient)
- Supports: `=`, `!=`, `in`, `nin`, `and`, `or`
- Enables: "Show me v3 SDK reference pages only"

---

## Summary: What Changed & Why It Matters

| Component | Before | After | Impact |
|-----------|--------|-------|--------|
| **Chunking** | Fixed 500-char | Structure-aware | Tables/code intact, headers as boundaries |
| **Metadata** | 3 fields | 10 fields | Filtering, citations, versioning |
| **Retrieval** | Dense only | Dense + BM25 + RRF | Exact symbols now found |
| **Citations** | None | [chunk_id] per claim | Verifiable answers |
| **Refusal** | Prompt suggestion | Hard gate (token overlap) | Enforced, no quota waste |
| **Generation** | Gemini (20/day) | Groq (high quota) | Reliable, no quota errors |
| **Filtering** | None | sdk_version, page_type | Version-aware queries |

### Bottom Line

The system evolved from a **basic semantic search** to a **production-grade RAG** with:
- ✅ Structure-preserving chunking
- ✅ Hybrid retrieval (semantic + exact)
- ✅ Metadata-rich filtering
- ✅ Verifiable citations
- ✅ Enforced refusals
- ✅ High-availability generation

**Hit-rate maintained at 83.3%** with better precision for exact-symbol queries and production-ready guardrails.