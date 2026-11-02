# After Week 10 — Full Project Walkthrough

**Purpose of this file:** one single place that explains everything built so far in this
project — the RAG app itself, and every experiment (agent vs workflow, trajectory
checking, MCP, multi-agent squad, A2A). It is written in plain English on purpose.
If anyone — a mentor, a reviewer, your future self — asks a question about this
project, the answer should already be in here. Section 13 is a ready-made FAQ for
exactly that.

---

## 0. The whole project in 60 seconds

This repo has **two layers**:VCV

1. **A real, working RAG Q&A app** (`developer-rag/app.py` and friends) — you upload a
   PDF or Markdown file, it gets chunked, embedded, and stored; you ask a question,
   it retrieves the right chunks, reranks them, and an LLM answers using only those
   chunks, with citations.
2. **A series of weekly experiments** (`developer-rag/agent_race/`) built on top of a
   *second, separate* document set (GitHub API + Swagger Codegen docs), used to
   answer harder engineering questions: *does this task need an autonomous agent, or
   is a fixed pipeline enough? Can I trust the agent's reasoning path, not just its
   final answer? Can I add new tools without touching code (MCP)? Does a team of
   agents beat one agent (multi-agent + A2A)?*

Every experiment was run as a **fair race with a decision rule written down before
seeing the results**, and every finding — including the ones that made the fancier
approach look bad — was kept and reported honestly. That discipline matters more
than any single number in here.

---

## 1. Part A — The RAG Q&A App (what a user actually runs)

This is the system behind `app.py`. A user opens a Streamlit page, uploads a
document, and asks questions about it.

### 1.1 Document ingestion — how a file gets into the system

There are two ways documents get in:

- **Live upload (what the app actually uses):** `ingest_live.py` →
  `ingest_uploaded_file()`. When you upload a `.pdf` or `.md` file in the sidebar,
  this function:
  1. Reads the file (`pypdf` for PDF, plain decode for Markdown).
  2. Splits it into chunks (see chunking below).
  3. Builds a stable ID for each chunk, like `getting-started:p2:authentication:0`
     (`file : page : section-anchor : chunk-number`).
  4. **Checks for duplicates** — if a chunk with that exact ID is already in the
     database, it's skipped. Re-uploading the same file does nothing.
  5. Turns the new chunks into embeddings and saves them into ChromaDB.
  6. Tells the app to reload its search index (`refresh_indexes()`), so the new
     content is searchable immediately.
- **Batch/offline rebuild:** `ingest_improved.py` — a standalone script that wipes
  the whole database and rebuilds it from a folder of PDFs (`DOCUMENTS_DIR`), using
  the same chunking and embedding logic. Used for "reindex everything from scratch,"
  not for the live app.
- There are also older/experimental ingestion scripts (`ingest.py`,
  `ingest_combined.py`) kept around from earlier weeks as comparison points — they
  are not what the live app uses today.

### 1.2 Chunking strategy — how a document gets cut into pieces

Defined in `chunkers.py`. Two strategies exist; **only the second one is actually
used**:

1. **`chunk_text()` — fixed-size sliding window (the old baseline).**
   Just cuts the text every 800 characters with 150 characters of overlap between
   consecutive chunks (so a sentence that gets cut in half still appears whole in
   the next chunk too). Simple, but it can slice a table or a code example right
   down the middle.

2. **`chunk_structured()` — structure-aware chunking (what's actually used).**
   This is smarter:
   - First, `normalize_pdf_text()` cleans up PDF text into something that looks
     like Markdown (turns ALL-CAPS lines and "1. Introduction"-style lines into
     proper `## Headers`).
   - Then `parse_structured_blocks()` walks through the text and tags every piece
     as a **header**, a **table**, a **code block**, or **prose** (normal
     paragraphs), using pattern matching (regex).
   - Then `chunk_structured()` packs these pieces into chunks of about 1,200
     characters, with two hard rules: **never split a table or a code block in the
     middle**, and **always start a new chunk at a header**. Long prose gets split
     at paragraph breaks instead.
   - `anchor_for_section()` turns each header into a clean slug (like
     `authentication-methods`) used for citations.

   **Why this is better:** if a user asks "what does this parameter table look
   like," a fixed-size chunker might hand back half a table with no header to say
   what it's a table of. The structure-aware chunker guarantees the table and its
   context stay together.

### 1.3 Embedding model — turning text into searchable vectors

- **Model used: `BAAI/bge-small-en-v1.5`**, run locally via the `sentence-transformers`
  Python library (no API call, no internet needed once downloaded).
- Produces 384-number vectors, normalized to unit length.
- **The exact same model is used for documents during ingestion AND for the user's
  question during search.** This is important — if you embed with two different
  models, the numbers aren't comparable and search breaks silently. The code even
  has a comment reminding future editors of this.
- **Why this model:** it's free, small (loads fast, runs fast on a CPU, no GPU
  needed), and punches well above its size on retrieval benchmarks. Good choice for
  a project with no dedicated embedding budget or infrastructure.
- **Leftover dead config:** `config.py` still defines `EMBEDDING_MODEL =
  "gemini-embedding-001"`, but nothing in the code actually uses it anymore — it's
  a stale leftover from an earlier design that should be deleted.
- **Better alternatives, if you wanted to spend money/compute:** OpenAI's
  `text-embedding-3-small`/`-large` (hosted, usually higher quality, costs per
  call, needs network); `bge-base-en-v1.5` or `bge-large-en-v1.5` (same family,
  bigger and more accurate, slower).

### 1.4 Vector database — where the chunks and their vectors live

- **ChromaDB**, used as a `PersistentClient` (saves to disk at `./data/chroma`, not
  just in memory).
- One main collection, `developer_docs`, configured for **cosine similarity**
  search (`hnsw:space: cosine` — HNSW is the fast approximate-nearest-neighbor
  search algorithm Chroma uses under the hood).
- **Why Chroma:** it's simple, embeds easily into a Python app with zero external
  server to run, and is a very common choice for small-to-medium local RAG
  projects. For a much bigger document set or production multi-user system, you'd
  look at something like Pinecone, Qdrant, Weaviate, or pgvector instead — Chroma's
  local file-based storage doesn't scale to huge concurrent workloads as cleanly.

### 1.5 Retrieval / search methodology — how a question finds the right chunks

This is the most sophisticated part of the app, in `rag_hybrid.py`. There are two
search "paths," chosen automatically:

**Path 1 — a metadata filter is given** (e.g. "only search v2 docs"):
→ Dense vector search only (with the filter applied) → reranked → return top-k.
(BM25 keyword search can't apply a metadata filter, so it's skipped in this case.)

**Path 2 — no filter (the normal case, hybrid search):**
1. **Dense search** — embed the question, ask ChromaDB for the top 25 most
   similar chunks by vector distance.
2. **BM25 keyword search** — a classic keyword-matching algorithm (counts how
   often query words appear, weighted by how rare/important those words are). This
   project has its **own hand-written BM25 implementation** (a small `BM25` class
   in `rag_hybrid.py`, not an external library) built once at startup over every
   chunk in the database. Returns the top 60 matches.
3. **Reciprocal Rank Fusion (RRF)** — merges the two ranked lists into one. Instead
   of combining raw scores (which aren't on the same scale between dense and
   BM25), RRF gives each chunk a score based only on *its rank* in each list:
   `score += 1 / (60 + rank + 1)`. A chunk that ranks well in *both* lists wins.
   This produces a single fused candidate pool.
4. That fused pool goes to **reranking** (next section).

**In plain words:** dense search finds things that *mean* the same thing as the
question (good for paraphrased questions); BM25 finds things that *literally
contain the same words* (good for exact terms like a function name or an error
code). Combining both and fusing the rankings gets the best of both — this is
called **hybrid search**, a well-known best practice in modern RAG systems.

### 1.6 Reranking — yes, this project has one

- **Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`**, loaded lazily (only the first
  time it's actually needed, to save startup time).
- A **cross-encoder** is different from the embedding model: instead of turning
  the question and each chunk into separate vectors and comparing them
  mathematically (fast but approximate), it reads the question *and* the chunk
  **together, as one input**, and outputs a single relevance score. This is slower
  per-pair but much more accurate — which is why it's only run on the ~25-85
  candidates already shortlisted by hybrid search, not the whole database.
- `rerank_with_cross_encoder()` scores every (question, chunk) pair, sorts by
  score descending, and keeps only the final `top_k` (what the user asked for,
  e.g. 5).
- **Why add this:** vector search alone is "good enough," but a reranker
  meaningfully improves precision at the top of the list, which matters a lot
  because only the top few chunks actually make it into the LLM's prompt.
- **Alternatives:** `bge-reranker-base`/`-large` (newer, often stronger, similar
  local-model tradeoffs); a hosted API like Cohere's Rerank (better quality,
  costs money, adds network latency); or skipping reranking entirely and just
  pulling more candidates into the prompt (cheaper, usually worse).

### 1.7 The relevance gate — when the app refuses to answer

`is_relevant()` decides if the retrieved chunks are even good enough to bother
answering from:
- If a `rerank_score` exists (it normally does, since reranking always runs), the
  gate passes only if the **top result's rerank score is above 0.0**.
- Otherwise (fallback), it compares the top result's raw distance against
  `RELEVANCE_THRESHOLD = 0.60`.
- If the gate fails, the app returns a fixed **"I don't know based on the provided
  documents."** without ever calling the LLM — this both saves money and prevents
  the model from guessing/hallucinating an answer with no real support.

### 1.8 Answer generation — the final step

Handled in `app.py`:
- `_build_prompt()` assembles the context: for each retrieved chunk, truncates it
  to **600 characters**, and stops adding chunks once the **total context hits
  1,800 characters** (to stay well under the LLM provider's request-size limits).
  Each chunk is labeled with a `chunk_id` the model must cite, e.g.
  `[my-doc:p1:introduction:0]`.
- The prompt is a strict "answer only from context, say 'I don't know' if you
  can't, cite your sources" instruction set — classic grounded-RAG prompting to
  minimize hallucination.
- `stream_answer()` sends this to **Groq**, model **`groq/compound`**, through the
  OpenAI-compatible SDK (`base_url=https://api.groq.com/openai/v1`), with
  `temperature=0.1` and streaming turned on — so the answer appears token-by-token
  in the UI instead of all at once.
- **If the API call fails** (rate limit, payload too large, etc.), the app doesn't
  crash — it falls back to showing the raw retrieved chunks directly to the user,
  labeled as a degraded response. This graceful-degradation behavior was added
  *after* real failures were observed and documented (see Section 10, Mode 1).
- **Why Groq + `groq/compound`:** fast, cheap hosted inference with an
  OpenAI-compatible API (easy to swap providers later), and it supports
  streaming. An earlier version of this app used **Gemini (`gemini-3.6-flash`)**
  instead — that's now legacy/replaced.
- **Better alternatives:** a stronger model like GPT-4o-mini or Claude Haiku would
  likely follow the strict grounding instructions a bit more reliably (at a
  higher cost per call); a local model via Ollama would remove API cost and rate
  limits entirely at the expense of speed and needing your own GPU.

### 1.9 State management in the app

- **The vector database itself** (ChromaDB on disk) is the main persistent state —
  what's been ingested survives app restarts.
- **The in-memory BM25 index** is rebuilt from the database every time
  `refresh_indexes()` runs (after a new upload) — it is *not* persisted separately;
  it's always derived fresh from Chroma.
- **Query embedding cache:** `app.py` wraps query embedding in
  `functools.lru_cache(maxsize=256)` so asking the same question twice (or moving
  the Top-K slider and re-asking) doesn't re-run the embedding model.
- **Interaction logging:** every question/answer cycle is appended to
  `traces.jsonl` — one JSON line per query with timing, retrieved chunk metadata,
  rerank scores, and the full raw answer. This log is what later feeds the
  evaluation scripts (Section 2) and the failure-taxonomy work (Section 10).
- **UI state:** handled by Streamlit's own model — the page re-runs top-to-bottom
  on every button click; there's no custom session object being manually managed
  beyond the `@st.cache_resource` Groq client.

### 1.10 End-to-end: what happens when a user asks a question

```
User types a question and clicks "Ask"
        │
        ▼
_embed_query(question)              — embed with BGE (cached)
        │
        ▼
rag_hybrid.retrieve(...)
   ├── dense search (top 25, ChromaDB)
   ├── BM25 search (top 60, hand-rolled index)
   ├── Reciprocal Rank Fusion  → merged candidate pool
   └── cross-encoder reranking → final top-k results
        │
        ▼
is_relevant(results)?  ── NO ──► "I don't know based on the provided documents."
        │ YES
        ▼
_build_prompt(question, results)    — context capped at 1800 chars, cite chunk_ids
        │
        ▼
stream_answer() → Groq groq/compound → tokens streamed live to the UI
        │
        ▼
write_trace(...) → traces.jsonl      (for later evaluation)
        │
        ▼
UI shows: streamed answer, deduplicated source list, debug panel with
distances + rerank scores
```

---

## 2. Part B — The Evaluation System (how we know the RAG app actually works)

Building the app isn't enough — this project also built a way to **measure**
whether it's actually any good, instead of eyeballing a few answers.

- **`taxonomy.md` (Week 5):** 20 real traces were read by hand and sorted into 5
  named failure modes (full detail in Section 10 — this feeds directly into "how
  to improve the system").
- **`eval/cases.json` (Week 6):** 26 test questions, each tagged with which
  failure mode it's checking, including several **regression cases copied
  verbatim from real failed traces** — so a future code change can't silently
  reintroduce an old bug without a test catching it.
- **`eval/assertions.py`:** 3 **deterministic** (non-LLM) checks that replace
  parts of what used to be graded subjectively by an LLM judge: does a code
  sample actually parse (`node --check`)? Does every endpoint/method mentioned
  actually exist in the real docs? Is the API version stated when the question is
  ambiguous between v2/v3? Plus a 4th check, `distance_not_placeholder`, which is
  a regression guard — it currently fails on every single case, confirming a bug
  (Section 10, Mode 5) is still unfixed.
- **`eval/judge.py` — LLM-as-judge:** the remaining subjective question
  ("is this answer actually helpful and correct?") is graded by a separate,
  **smaller/cheaper model, `openai/gpt-oss-20b`**, run via Groq. Crucially, this
  judge was **validated against real human judgment before being trusted**:
  first run agreed with hand-labels 80.8% of the time; a second, "improved"
  version of the judge prompt was tried and actually *dropped* agreement to
  76.9% — this regression was reported honestly and the original (v1) judge was
  kept in production rather than quietly accepting the "improved" one.
- **`eval/ragas_bonus.py`:** hand-implemented versions of two well-known RAG
  metrics — **faithfulness** (are the answer's claims actually backed by the
  retrieved text?) and **context precision** (are the retrieved chunks actually
  relevant?) — without pulling in the full `ragas` Python package, to avoid
  dependency conflicts with the project's pinned ML libraries.
- **`eval/run_eval.py`:** the single command that runs everything — fresh
  retrieval + generation on every case, applies the deterministic assertions,
  grades with the validated judge, and prints a pass-rate-by-mode table.

**Why pass-rate-by-mode matters more than one overall number:** an overall score
of "54% pass" sounds mediocre but uniform. The by-mode breakdown showed baseline
questions passing 73% of the time while three specific failure modes
(cross-document questions, wrong-chunk retrieval, false refusals) were sitting at
**0%** — a single average would have completely hidden that most of the real
damage was concentrated in a few specific, fixable scenarios.

---

## 3. Part C — Agent vs. Workflow (Week 7)

This is the first of the agent experiments, run on a **separate** document set
(GitHub REST API docs + Swagger Codegen docs) via `agent_race/`.

**The question asked:** for a multi-step task (find the current spec → check what
changed between versions → answer), do you actually need an autonomous AI agent
that decides its own steps, or does a fixed, hard-coded sequence of steps do just
as well, faster and cheaper?

**Two systems built, sharing the same 3 tools, same model, same output format:**
- **`agent.py` — a hand-built ReAct agent** (no framework like LangChain used —
  built from scratch to understand exactly what's happening). "ReAct" means:
  think → call a tool if needed → read the result → repeat until it can answer.
  Has **4 safety budgets** checked every loop: max iterations (8), max cumulative
  tokens (8,000 — summed across every lap, not just the last call), max cost
  ($0.01), max wall-clock time (60s). If any budget is hit, it stops cleanly
  instead of running forever.
- **`workflow.py` — a fixed 4-step pipeline.** No LLM ever decides what happens
  next — it always runs `search_docs` → `get_openapi_spec` → `check_deprecation`
  → one final "write the answer" LLM call, in that exact order, every time.

**The three tools (`tools.py`):** `search_docs` (fuzzy/semantic search),
`get_openapi_spec` (exact current parameter table for one version+endpoint),
`check_deprecation` (exact "what changed" lookup). The `api_version` and
`endpoint` arguments are **closed enums**, not free text — this was a deliberate
fix after an earlier draft of `check_deprecation`'s tool description overlapped
with `get_openapi_spec` and used free-text arguments, which would have let the
model send unmatchable paraphrases like `"v2025"` or `"latest"` (full before/after
story in `tool_diff.md`).

**Result — the workflow won on every single metric:**

| Metric | Agent | Workflow |
|---|---|---|
| Pass rate | 80% | **100%** |
| p50 latency | 25.9s | **6.6s** |
| Total tokens (10 questions) | 49,477 | **10,690** |
| Cost per question | $0.00094 | **$0.00025** |

The agent's two failures were both "negative" questions (where the correct answer
is "this isn't in the docs") — its freedom to keep retrying `search_docs` with
reworded queries became a liability, burning through its token budget instead of
concluding "not found," while the workflow's fixed 4 steps naturally terminate in
a clean refusal.

**Verdict:** ship the workflow. None of the 10 test questions — including 4
deliberately chosen "dependency" questions that need information from one step to
answer the next — actually needed an agent's freedom to improvise. The decision
rule used: *"does the right sequence of steps change depending on the question?"*
If the answer is no, a fixed pipeline is strictly better — cheaper, faster, and
just as (or more) correct.

---

## 4. Part D — Trajectory Evaluation (Week 8)

**The question asked:** Week 7 only checked *whether the final answer was right*.
But a right answer reached by skipping the one authoritative tool is a ticking
time bomb — it'll silently break the day the real data changes. Can the agent's
**reasoning path**, not just its output, be checked automatically?

- **`trajectory_specs.py`:** for each of the 10 questions, the *correct* set of
  tools, the *allowed* tools, valid orderings, and the minimum number of steps
  were written down **before** running anything.
- **`trajectory_eval.py`** then runs the agent and checks the actual tool-call
  sequence against that spec, producing 4 numbers: outcome pass rate, trajectory
  pass rate, tool-choice accuracy, argument validity.

**The real finding — a 0.0 "gap" that almost hid a real bug:**
Outcome pass rate and trajectory pass rate were both 0.8 — on paper, "no gap, all
good." But they were **failing on different questions**. Two specific cases
(`easy_02`, `medium_01`) got the *correct answer* using only the fuzzy
`search_docs` preview text — **without ever calling the authoritative
`get_openapi_spec` tool.** The answer happened to be right this time because the
preview snippet contained enough text, but that's luck, not grounding — the
instant the underlying docs change, this path breaks with no warning. Classified
as `incomplete_grounding` in a formal 6-mode failure taxonomy
(`failure_modes.py`) built for this subsystem.

**The mitigation:** one single sentence was added to the agent's system prompt,
telling it that `search_docs` is only a preview and it must still call the
authoritative tool before finalizing an answer.

**The honestly-reported cost of that one-line fix:**
`incomplete_grounding` dropped from 2 to **0** (fixed, as intended) — but overall
**outcome pass rate dropped from 0.8 to 0.6**, because the longer prompt (resent
every single loop lap) pushed two *previously-passing* questions over their
token budget, so they got no answer at all instead of a slightly-ungrounded one.
This is reported as a genuine regression, not glossed over — fixing the
reasoning-path bug made the token budget the next bottleneck. The documented
next step (not yet done) is to raise `max_tokens` slightly rather than shipping
the prompt change alone.

**Takeaway for anyone building agents:** outcome-only grading is not enough.
Always check the path too, and always measure the price of any fix you make —
"it fixed the thing I was looking at" and "it didn't break something else" are
two different claims that need two different pieces of evidence.

---

## 5. Part E — MCP: Model Context Protocol (Week 9)

**What MCP is, in plain English:** MCP is an open standard that lets an AI agent
discover and call tools from any compliant "server" at runtime, instead of the
tools being hardcoded Python functions imported directly into the agent's code.
Think of it like a USB-C port for AI tools — any MCP-compliant server plugs into
any MCP-compliant agent with no custom wiring.

**The question asked:** can a new capability (a "package registry" lookup tool,
simulating something DevRel might ask for later) be added **without touching the
agent's code at all**?

**What was built:**
- **`docs_search_server.py`** — a real MCP **server**, built with the
  `mcp.server.fastmcp.FastMCP` library, running over **stdio** (the server is a
  subprocess the client starts and talks to over its standard input/output pipes,
  not a network daemon). It wraps the same 3 docs tools from Week 7, exposing them
  with proper typed schemas (`Literal[...]` enums for `api_version`/`endpoint`, so
  the protocol layer itself rejects bad values before the tool's own code even
  runs).
- **`package_registry_server.py`** — a **second**, clearly-labeled-as-simulated
  MCP server exposing 3 new tools (`get_package_version`, `get_release_date`,
  `check_package_deprecated`) backed by a static JSON file, standing in for a
  real external package registry.
- **`mcp_agent.py`** — the MCP **client/host**. Same ReAct-loop shape and
  budgets as `agent.py`, but instead of importing tool functions directly, it:
  1. Reads `mcp_config.json` to find out which servers exist.
  2. Connects to each one (`ClientSession`) and calls `list_tools()` to discover
     what they offer, live, at startup.
  3. Converts each discovered tool's schema into the OpenAI function-calling
     format the LLM expects.
  4. When the model wants to call a tool, routes the call to whichever server
     actually owns it (`MCPToolRouter`), via `session.call_tool()`.

**The core proof — and it checked out exactly as hoped:**

```
1 server configured  → 3 tools discovered  (search_docs, get_openapi_spec, check_deprecation)
2 servers configured → 6 tools discovered  (+ get_package_version, get_release_date, check_package_deprecated)

mcp_agent.py diff between the 1-server and 2-server runs: 0 bytes changed.
mcp_config.json diff: +4 lines (one new server entry) — the only thing that changed.
```

Verified with a real question — *"Is version 2.4.0 of the octokit package
deprecated, and if so what should I upgrade to?"* — the agent genuinely called
the new server's tools (`get_package_version` then `check_package_deprecated`)
and answered correctly, proving the new capability was really used, not just
theoretically available.

**Two real bugs found while building this (reported, not hidden):**
1. A tool that returns a Python `list` serializes over MCP as **one separate
   message block per list item**, not one JSON array — this silently broke
   `search_docs` until it was caught and the client-side unwrapping code was
   fixed.
2. Typing arguments as strict enums (good practice, kept from Week 7) means the
   MCP protocol layer itself rejects a bad value *before* the tool's own
   "helpful recoverable error message" code ever runs for that specific
   input shape — so some of that error-handling code turned out to be
   unreachable via the real protocol path, and the evidence for it had to be
   honestly re-targeted to a failure shape that actually is reachable.

**`wire_capture.py`** also hand-captured the raw underlying JSON-RPC 2.0 protocol
messages (bypassing the SDK on purpose) — the handshake (`initialize`), the
"I'm ready" notification, and a real `tools/call` — fully annotated, to show
exactly what's happening at the network/subprocess level with no LLM involved in
that exchange at all (the LLM only decides *which* tool to call, on the host
side; the wire protocol itself is plain data).

**Model used:** `openai/gpt-oss-120b` via Groq — same model, same budgets as
Week 7's agent, since this week was testing the *plumbing*, not reasoning
quality.

---

## 6. Part F — Multi-Agent Squad and A2A (Week 10)

This is the most elaborate experiment: **does a team of specialized agents beat
one single agent, or does it just cost more?**

### 6.1 The architecture — "orchestrator-worker" (a manager + 2 specialists)

```
                    developer question
                           │
                 ┌─────────▼─────────┐
                 │   ORCHESTRATOR    │   the "manager" — has NO document
                 │   (manager)       │   tools of its own. Its only two
                 └────┬─────────┬────┘   "tools" are the two workers below.
      delegate:       │         │       delegate:
      request +       │         │       request + api_version + endpoint
      api_version +   │         │
      endpoint   ┌────▼──────┐ ┌▼────────────────┐
                 │ VERSION/  │ │  CODE-SAMPLE     │
                 │ DEPRECATION│ │  WORKER          │
                 │ WORKER    │ │  tool:           │
                 │ tools:    │ │  get_openapi_spec│
                 │ search_   │ └──────────────────┘
                 │ docs,     │
                 │ check_dep │
                 │ recation  │
                 └───────────┘
      each worker replies: {"status": 200, "body": "short text report"}
```

- **Orchestrator** (`orchestrator.py`): reads the question, decides which
  worker(s) to call and with what arguments, reads their reports, and writes the
  final answer.
- **Version/deprecation worker:** specializes in "what changed between
  versions" — tools `search_docs` + `check_deprecation`.
- **Code-sample worker:** specializes in "what does the current spec/parameters
  look like" — tool `get_openapi_spec` only.

**Important: all three roles use the SAME underlying model**, not different
models per role. Specialization comes entirely from **different system prompts +
different tool subsets**, not from picking a smarter or dumber model for each
role. This was deliberate: changing the model *and* the team structure at the
same time would make it impossible to tell which one caused any difference in
results. (A cheaper model per worker is flagged as a good follow-up experiment,
just not this one.)

### 6.2 How the orchestrator actually splits the work

- It is a **single sequential loop** (a ReAct loop, same shape as every other
  agent in this project) — **not** parallel. There's no asyncio, no threading, no
  multiprocessing. One lap at a time, up to 8 laps, up to 6 total delegations.
- Each lap: the manager LLM sees the full conversation so far plus the two
  worker "tools" (`ask_version_worker`, `ask_code_worker`) and decides, in
  normal function-calling style, whether to delegate (and to whom, with what
  arguments) or to write the final answer.
- **There's no separate "planning" step that decomposes the task in advance** —
  decomposition is emergent: the LLM figures out, lap by lap, what it still
  needs and who to ask for it.

### 6.3 Agent-to-agent communication (the "A2A" part)

- **Topology: hub-and-spoke only.** Workers never talk to each other — every
  message goes manager↔worker.
- **The contract is deliberately shaped like an HTTP response**, even though
  everything runs in the same Python process with no real network hop:
  `{"status": <int>, "body": ...}` — 200 for success, 500 for a simulated
  failure, 429 for "delegation limit reached," etc. This makes "a worker failed"
  look and feel exactly like a real microservice failure would, which is exactly
  what's needed to test how the manager behaves when something breaks.
- **Manager → worker:** a **brand-new, empty conversation** every time — the
  worker's own system prompt plus the manager's request, with `api_version` and
  `endpoint` explicitly appended so the target version can never get lost in
  translation. **The manager's own history is never handed down to the worker.**
- **Worker → manager:** only the worker's **final short text report** comes
  back. The raw document text the worker fetched along the way **never leaves
  the worker** — this was a deliberate design choice specifically to keep token
  costs down (no point re-sending a whole page of documentation up to the
  manager when a two-sentence summary will do).
- This "minimal task down, summary up" design was measured, not just claimed:
  every single model call anywhere in the system is labeled with exactly which
  hand-off triggered it, and the token cost of each hand-off type is tracked
  separately (see `handoffs.log` / `multiplier.txt`).

### 6.4 State management in the squad

- **No shared database or memory object.** State is just Python conversation-
  history lists, scoped per agent, per call:
  - The manager keeps and re-sends its *entire* history every lap (this is
    normal for any ReAct loop and wasn't specially optimized).
  - Each worker gets a **fresh, short-lived** history every single time it's
    delegated to — it has no memory of any previous delegation.
- **Fault-injection state** (used only for the failure test) lives in one
  simple global dictionary in `workers.py` that can be flipped on/off between
  test runs.
- **Everything that needs to survive across runs is a flat file on disk** —
  `race_runs.json` (one row per finished race run, enabling the race to be
  *resumed* after an interruption without re-running finished work),
  `traces.jsonl` (one line per individual model call, the raw data everything
  else is computed from).
- **`metering.py`** is the shared "measuring tape" used by every call in the
  system — it wraps every model call, records who called it, why (the hand-off
  label), how many tokens/dollars/seconds it cost, and deliberately **excludes
  time spent waiting on provider rate limits** from the latency numbers, so a
  slow API minute doesn't get mistaken for a slow agent.

### 6.5 The race result

| Metric (7 cases both arms completed) | Single agent | Squad |
|---|---|---|
| Pass count | 7/7 | 7/7 (tied — no accuracy gain) |
| Total tokens | 21,118 | 25,129 (**1.2x**) |
| Cost per question | $0.00030 | $0.00040 (**1.3x**) |

The squad was only cheaper on one question out of seven; on every other question,
all the manager↔worker back-and-forth was pure overhead, because these questions
were simple look-ups with nothing to usefully split up between specialists.

### 6.6 The failure test — "retried, then lied"

The version worker was deliberately made to return an HTTP 500 error. The
manager was given **zero failure-handling code and zero failure-handling
instructions** on purpose — the experiment wanted to see what it does *naturally*.

**What happened:** the manager called the broken worker again and again (6
times) until it hit its own delegation limit, then — having never once gotten a
real, verified result — **confidently wrote an answer as if the check had
succeeded.** This is classified as "**lied**": a confident, fluent, wrong answer
presented with no hedging, as opposed to "**degraded**" (which would have been
an honest "I couldn't verify this"). The failure is dangerous specifically
because it's *silent* — the wrong answer looks exactly like a correct one.

**Recommended (but deliberately not built, since this week was about measuring,
not patching) fix:** bounded retries with backoff on real server errors only,
then an explicit "version check unavailable" result the manager is *required* to
pass along (e.g. a mandatory `verified_version` field it can't skip), a circuit
breaker after repeated failures, and an alert whenever an answer goes out while
a dependency was known to be down.

### 6.7 The verdict — and the decision rule behind it

A **keep/kill rule was written down and frozen before a single race question
was run** (to prevent "the squad lost, let me find a reason to keep it anyway"
reasoning after the fact). The rule: keep the squad only if **all three** hold —
it beats the single agent by at least one more passed question, its cost and
latency are each no more than 2x the single agent's, and the failure test does
not end in "lied." A tie on accuracy counts as an automatic kill, because there's
nothing gained to justify the extra cost.

**Result: KILL.** Rule 1 failed (tied, no accuracy gain), rule 2 technically
passed (1.3x cost is under the 2x bar), rule 3 failed (it lied). Sunk-cost bias —
"but I already built it" — is explicitly named and rejected as a reason to keep
the squad anyway.

**What would change this verdict later:** an explicit "unverified" flag in the
worker contract, bounded retry with a circuit breaker, a proper 3-repeat race on
a paid API tier (this one only managed 1 repeat due to free-tier quota limits —
honestly disclosed, not hidden), and — most importantly — **a task that actually
has independent sub-parts to split up.** These 10 questions were mostly simple
look-ups; a multi-agent team is more likely to pay off on a task that genuinely
parallelizes.

### 6.8 A2A protocol notes (bonus — no live A2A server was actually built)

A2A ("Agent-to-Agent") is a different open standard from MCP:
**MCP connects an agent to tools** (a function it can call); **A2A connects an
agent to other autonomous agents**, each with their own task lifecycle that
might take a while or need to ask a clarifying question back.

A2A defines standard task states: `submitted`, `working`, `completed`, `failed`,
`canceled`, `rejected`, `input-required`, `auth-required`.

**Where the failure-test case should have ended up, in proper A2A terms:** it
should have ended in **`failed`**, not `input-required`. `input-required` is for
when *the user* can supply the missing piece — but the question already named
both versions being compared, so there was nothing to ask the user for. A 500 is
a server fault, not missing information. The actual bug, described in A2A
language, is that the squad's task finished as `completed` **while claiming
success after a failed sub-task** — exactly the dangerous "lied" behavior from
Section 6.6, now named in protocol terms.

**What A2A buys over a plain REST call to a worker:** (1) a standard,
discoverable contract — an "AgentCard" advertises what an agent can do and how,
and defined task states mean "the worker is down" and "the worker needs more
information from you" are structurally different and can't be confused; (2)
built-in support for long-running work and structured hand-offs (task IDs,
status, messages, artifacts), where a bare REST call only ever gives you a body
or an error code.

---

## 7. All Models Used — Full Table, With Reasoning

| Purpose | Model | Where it's used | Why this one | Stronger / alternative options |
|---|---|---|---|---|
| Embeddings | `BAAI/bge-small-en-v1.5` (local) | Ingestion + query embedding, everywhere (RAG app and agent_race) | Free, runs on CPU, fast, no API key needed, strong accuracy for its small size | OpenAI `text-embedding-3-small/large` (hosted, usually better quality, costs money); `bge-base`/`bge-large` (bigger, slower, more accurate) |
| Reranking | `cross-encoder/ms-marco-MiniLM-L-6-v2` | `rag_hybrid.py` | Small, fast, well-established baseline reranker, cheap to run after hybrid search narrows candidates | `bge-reranker-base/large` (often stronger); Cohere Rerank API (hosted, costs money, usually higher quality) |
| Answer generation (production RAG app) | `groq/compound` via Groq | `app.py`, `eval/generate.py` | Fast + cheap hosted inference, OpenAI-compatible API, supports streaming | GPT-4o-mini / Claude Haiku (likely follows strict grounding rules more reliably, costs more); local Ollama model (free, slower, no rate limits) |
| Answer generation (earlier/legacy) | `gemini-3.6-flash` | Old version of `rag.py` (now superseded) | Was the original choice before the switch to Groq | — |
| LLM-as-judge (RAG app eval) | `openai/gpt-oss-20b` via Groq | `eval/judge.py` | Deliberately smaller/cheaper and *different* from the generation model — a judge should never grade its own work | GPT-4o as judge (stronger but costs much more) |
| Agent / Workflow / MCP agent | `openai/gpt-oss-120b` via Groq | `agent.py`, `workflow.py`, `mcp_agent.py` (Weeks 7–9) | Kept identical across every role each week, on purpose, so the *pattern* being tested (agent vs. workflow) is the only variable that changes | A stronger frontier model would likely win on accuracy but costs much more per token |
| Multi-agent squad (manager + both workers) | `openai/gpt-oss-20b` via Groq (after a documented mid-experiment switch, see Section 6) | `orchestrator.py`, `workers.py` | Same model for every role, again to isolate "one agent vs. a team" as the only variable; switched from 120b to 20b mid-week only because of a free-tier quota limit, fully disclosed | A cheaper/smaller model per *worker* role specifically (not the manager) is a flagged follow-up idea, not yet tried |
| Judge for agent_race experiments | `openai/gpt-oss-20b` (Weeks 7–9) then `openai/gpt-oss-120b` (Week 10, after the race model itself moved to 20b) | `grader.py` | Always kept different from whichever model is under test, so grading is never self-grading | — |

---

## 8. State Management — One Consolidated View

| Subsystem | What "state" means here | How it's kept |
|---|---|---|
| RAG app | The document index itself | ChromaDB, saved to disk (`./data/chroma`), survives restarts |
| RAG app | Keyword search index | Rebuilt in memory from Chroma every time new docs are ingested (`refresh_indexes()`) — never saved separately |
| RAG app | Repeated-question speed-up | `functools.lru_cache` on the query embedding step only |
| RAG app | A record of every Q&A for later review | Appended to `traces.jsonl`, one JSON line per question |
| Single agent / workflow | What's happened in the current run | One Python list of messages, held only in memory for the duration of that one function call — gone once it returns |
| MCP agent | A live connection to each tool server | Opened at the start of a run, closed (`aclose()`) at the end — never persisted |
| Multi-agent squad — manager | The whole conversation across all its delegation laps | One growing in-memory messages list, re-sent in full every lap (this is the normal, "expensive" part of any ReAct loop) |
| Multi-agent squad — each worker | Its own task | A brand-new, short in-memory messages list every single time it's delegated to — no memory between delegations |
| Multi-agent squad — fault testing | "Is this worker currently broken, and how?" | One simple global on/off switch (a Python dict), reset between tests |
| Multi-agent squad — race history | Every finished race run, so the race can be resumed if interrupted | Flat JSON files (`race_runs.json`, `traces.jsonl`) — written after every single run, not just at the end |

**The general pattern across this whole project:** nothing here uses a database,
a message queue, or an external state store. Every "memory" is either a plain
Python list/dict that lives and dies with one function call, or a flat JSON/JSONL
file on disk that the *next* script reads back in. That's a perfectly reasonable
choice at this scale, and it's also exactly why every run is so easy to inspect
and debug — but it's worth knowing it's a deliberate simplicity choice, not an
accident (see Section 9 for where this would need to change to scale up).

---

## 9. Topics Covered In This Document (quick index)

- The live RAG app: document upload, structure-aware chunking, local BGE
  embeddings, ChromaDB vector storage, hybrid (dense + BM25 + RRF) retrieval,
  cross-encoder reranking, a relevance gate, grounded + cited answer generation
  via Groq, and how all of that is wired together end-to-end.
- How the RAG app is actually tested: a hand-built failure taxonomy, a 26-case
  mode-tagged eval set, deterministic (non-LLM) assertions, a human-validated
  LLM judge, and hand-rolled RAGAS-style faithfulness/precision metrics.
- A head-to-head race between a free-roaming AI agent and a fixed pipeline for
  a multi-step lookup task, and why the fixed pipeline won on every metric.
- Checking not just an agent's final answer but the *path* it took to get
  there, and a real example of a one-line fix that solved one problem while
  quietly causing another.
- What the Model Context Protocol (MCP) is, how a real MCP server and client
  were built, and proof that a brand-new tool capability was added with a
  4-line config change and zero code changes.
- A multi-agent "manager + specialists" system, exactly how work gets
  delegated and results get passed back, how a deliberately-broken worker
  exposed a dangerous "confidently wrong" failure mode, and the pre-registered
  decision rule that led to killing the multi-agent design (for now).
- The Agent-to-Agent (A2A) protocol's task-state model, and how it differs
  from MCP.
- Every model used anywhere in the project, matched to its exact purpose and
  the reasoning behind picking it.
- How state/memory is handled (or deliberately not persisted) across every
  part of the system.
- A prioritized list of concrete next steps to improve the live RAG app
  (below).

---

## 10. Known Bugs and Improvement List (prioritized, from real measurements)

These come directly from real findings in `taxonomy.md`, the Week 6 eval
report, and the Week 10 squad report — not guesses. Ordered roughly by impact.

1. **Fix the Groq 413 / rate-limit failures (was ~20% of all traces).**
   Add retry-with-backoff specifically for 413 ("payload too large") and
   rate-limit errors, and consider lowering `MAX_TOTAL_CHARS` in `app.py`'s
   prompt builder so requests stay further under the provider's size limit.
   This is the single highest-impact fix — it's the difference between a user
   getting an answer at all or not.

2. **Fix the still-unfixed distance-metadata bug.** In `rag_hybrid.py`'s hybrid
   search path, when Reciprocal Rank Fusion merges dense and BM25 candidates,
   the code currently lets the BM25 candidates' placeholder distance (`1.0`)
   overwrite the real dense-search distance for any chunk that appears in both
   lists. This doesn't hurt answer quality (reranking still orders things
   correctly), but it makes the "distance" shown in the debug UI useless for
   actually diagnosing retrieval problems. Confirmed still broken as of the
   last eval run — every single case shows this bug.

3. **Investigate the relevance-gate false refusal.** At least one real case was
   found where the correct chunk was retrieved at rank 1 but scored a negative
   rerank score, causing a confident "I don't know" for a question the system
   actually could answer. Consider a slightly more lenient threshold, or a
   fallback that checks distance when the rerank score is borderline instead of
   a hard cutoff at exactly 0.0.

4. **Chunk-truncation is quietly losing some correct facts.** The eval work
   found real cases where the right fact sat inside a retrieved chunk, but past
   the 600-character-per-chunk cutoff used when building the LLM's prompt — so
   the model correctly said "I don't know" based on what it was actually shown,
   even though the full document did contain the answer. A cheap fix: raise
   `MAX_CHUNK_CHARS`, or better, trim each chunk down to only the paragraph
   that actually matched the query instead of a flat character cutoff from the
   start.

5. **Cross-document questions can fail structurally.** When a question needs
   information from two different source files, the retrieval pool sometimes
   only surfaces chunks from one of them, making a correct comparison
   impossible no matter how good the reranker is. Worth trying: retrieve more
   candidates per source file before fusion, or run retrieval once per
   mentioned document when the question clearly references more than one.

6. **Clean up dead configuration.** `config.py` still defines
   `EMBEDDING_MODEL = "gemini-embedding-001"`, which nothing uses anymore — it's
   confusing for anyone reading the code later and should just be deleted.

7. **The LLM judge needs a v3 iteration.** The v2 judge prompt (with added
   few-shot examples) actually made agreement with human judgment *worse*
   (80.8% → 76.9%) because it over-corrected and started crediting facts that
   only appeared in an error-fallback dump, not in anything the model actually
   asserted. A real v3 should add one precise rule: only credit facts the
   answer itself actually states, never facts that only appear in a dumped
   raw-chunk fallback after a generation error.

8. **Don't bring the multi-agent squad back without first fixing its failure
   behavior.** If multi-agent is revisited, the Week 10 findings are explicit
   about what must change first: a worker contract with a mandatory
   "unverified" flag the manager can't ignore, bounded retries with a circuit
   breaker, and — most importantly — pick a task that actually has independent
   sub-parts to split across workers, since simple look-up questions showed
   pure overhead with zero benefit.

9. **Consider a stronger reranker or a hosted rerank API** if answer quality
   needs to improve further — `cross-encoder/ms-marco-MiniLM-L-6-v2` is a solid
   baseline but not the strongest option available today.

10. **Move evaluation into an automated check (CI).** `eval/run_eval.py`
    already does everything needed in one command; wiring it into a CI step
    (or at least a required pre-merge check) would turn the existing
    regression-case set into an actual safety net instead of a script someone
    has to remember to run.

---

## 11. Frequently Asked Questions (FAQ)

**Q: What embedding model do you use, and why?**
`BAAI/bge-small-en-v1.5`, run locally via `sentence-transformers`. It's free, has
no API key or network dependency, is fast enough to run on a normal CPU, and
performs well for its small size. The exact same model embeds both documents
and questions, which is required for the similarity search to work correctly.

**Q: What's your chunking strategy?**
Structure-aware chunking (`chunk_structured()` in `chunkers.py`). It detects
headers, tables, and code blocks in the text and packs them into ~1,200-
character chunks without ever splitting a table or code block in half, and
always starts a new chunk at a header. A simpler fixed-size sliding-window
chunker (`chunk_text()`) also exists as a baseline/comparison but isn't what the
live app uses.

**Q: What vector database do you use?**
ChromaDB, running as a local persistent client (saved to `./data/chroma`), with
cosine similarity search.

**Q: What's your retrieval/search methodology — is it just vector search?**
No — it's hybrid search: dense vector search (top 25) combined with a
hand-written BM25 keyword search (top 60), merged with Reciprocal Rank Fusion,
then narrowed further by cross-encoder reranking before the final top-k goes to
the LLM.

**Q: Do you have reranking?**
Yes — `cross-encoder/ms-marco-MiniLM-L-6-v2`, applied to the fused hybrid search
candidates before the final top-k is chosen.

**Q: What LLM generates the final answer, and why?**
`groq/compound` via Groq's OpenAI-compatible API, chosen for speed, low cost,
and streaming support. An earlier version used Gemini (`gemini-3.6-flash`)
before switching.

**Q: How do you know the RAG app actually works?**
A dedicated evaluation system: a 26-case mode-tagged test set (including real
regression cases from past failures), 3 deterministic/automated checks, and an
LLM judge that was validated against real human judgment before being trusted
in production.

**Q: Have you implemented MCP (Model Context Protocol)?**
Yes. Two real MCP servers were built (one wrapping the existing docs tools, one
simulating a package-registry service) and a real MCP client/agent
(`mcp_agent.py`) that discovers tools live at runtime instead of hardcoding
them. Proven result: adding a whole new server and 3 new tools required a
4-line config change and zero lines changed in the agent's own code.

**Q: Have you built a multi-agent system? How does it work?**
Yes — an orchestrator-worker pattern: one manager agent with no tools of its
own, delegating to two specialist workers (a version/deprecation worker and a
code-sample worker), each with their own narrow toolset and system prompt. The
manager runs a sequential (not parallel) loop, decides who to delegate to each
step, and assembles the final answer from the workers' short text reports.

**Q: How do the agents pass context to each other (and how do you manage the
"re-send" cost)?**
Each delegation to a worker starts a completely fresh conversation (no shared
history) containing only the manager's request plus the explicit
`api_version`/`endpoint`, so the target version is never lost. Workers send back
only a short final report — the raw documents they fetched never leave the
worker. The manager's own history does grow and get re-sent every lap (normal
for a ReAct loop), and every single model call in the whole system is labeled
by which hand-off triggered it, so the exact token cost of every kind of
hand-off is visible and measured, not just assumed.

**Q: Does the multi-agent team actually beat the single agent?**
No. On the measured questions, both got the same number of correct answers, but
the team used 1.2x the tokens and 1.3x the cost for zero accuracy gain. A
pre-registered decision rule (written before seeing results) led to the verdict
"KILL" — keep the single agent — until the team can prove a real accuracy gain
on a task that actually benefits from being split up.

**Q: What happened when you deliberately broke a worker?**
The manager (which has no failure-handling code on purpose) retried the broken
worker repeatedly, hit its delegation limit, and then confidently gave a wrong
answer as if the check had succeeded — never admitting the check had failed.
This "retried, then lied" behavior is the strongest reason not to use the
current multi-agent design in production as-is.

**Q: What is A2A, and how is it different from MCP?**
MCP connects an agent to *tools* (functions it can call). A2A connects an agent
to *other agents*, each with a formal task lifecycle (submitted, working,
completed, failed, input-required, etc.) that can take time or ask a question
back. This project analyzed A2A's task-state model and found that its own
failed-worker case should have ended in a `failed` state rather than the
`completed`-with-a-wrong-answer state it actually produced — but did not stand
up a live A2A server.

**Q: Did you use a framework like LangChain, CrewAI, or AutoGen?**
No — every agent loop (single agent, workflow, MCP agent, orchestrator, and
both workers) was hand-built from scratch on purpose, specifically so every
tool call, hand-off, and token could be seen and measured directly instead of
being hidden inside a framework's internals.

**Q: How is state/memory managed across all of this?**
There's no database or external memory store anywhere in the project. Live
conversation state is just a plain Python list held in memory for the duration
of one run; anything that needs to survive across runs (the document index
itself, race results, interaction logs) is either ChromaDB on disk or flat
JSON/JSONL files.

**Q: What's the single biggest thing to fix next in the RAG app?**
The Groq request-size/rate-limit failures, which used to account for roughly
1 in 5 real queries getting no generated answer at all — see Section 10 for the
full prioritized list.

---

*This document reflects the project state after Week 10. If the project keeps
evolving, treat this file as a living summary — re-read the relevant weekly
report (`week7_report.md` through `squad/week10_report.md`, plus
`taxonomy.md` and the `eval/` reports) before assuming any number here is still
current.*
