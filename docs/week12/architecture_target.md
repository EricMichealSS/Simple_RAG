# Week 12 — Architecture (as built), with the target it was designed to

Date: 2026-11-04 · This is the diagram of the **implemented** system, drawn from `sdk_engine/graph.py` (the edges below are the `add_conditional_edges` calls), `mcp_client.py`, `mcp_server.py`, `factory.py` and the span names in the traces. Where the Phase 0 plan (`week12_plan.md` §5) differs from what exists, §4 says so.

## 1. Component boundaries, data flow, model calls, storage, MCP hops, cost boundaries

```mermaid
flowchart LR
  user([developer]) -->|"CLI: query / upload / health ..."| cli

  subgraph P1["Process 1: python -m sdk_engine (the engine)"]
    direction TB
    cli["cli.py\nvalidated config (ConfigError)"] --> engine["Engine.ask\nLangGraph StateGraph (graph.py)\n14 nodes, bounded loops"]
    engine --> ver["versioning.py\nresolve version BEFORE retrieval"]
    engine --> mcpc["McpToolClient\ndiscovery + allowlist + schema check\nper-call timeout, output cap"]
    engine --> fb["in-process ChromaRetriever\n(fallback if a tool call fails)"]
    engine --> up["UploadStore\n(in-process; never over MCP)"]
    engine --> llmc["GroqChat (llm.py)\nONE call per answer, + 1 retry max"]
    engine --> chk["answering.py / codecheck.py / injection.py / redaction.py\nverification, output safety, code validation"]
    engine --> tr["trace.py: spans, one trace id"]
  end

  subgraph P2["Process 2: MCP server 'sdk-docs' (stdio, spawned by the client)"]
    direction TB
    srv["mcp_server.py\nsearch_docs / lookup_changes / check_symbol\nno LLM, no write, no shell, no file tool"]
    emb2["local embedding model\n(query vectors)"]
    srv --> emb2
  end

  mcpc <==>|"MCP JSON-RPC over stdio\nversion REQUIRED on every call\ntrace id in request _meta"| srv

  subgraph STORE["Storage (all local; data/ is git-ignored)"]
    chroma[("data/chroma\ncollection race_docs\n39 chunks, 4 versions")]
    sqlite[("data/uploads/uploads.sqlite3\nprivate, per owner,\nphysically separate from the index")]
    spans[("data/traces/spans.jsonl\nserver_spans.jsonl")]
    runs[("data/eval_runs/*")]
  end

  srv -->|"read, version filter inside the query"| chroma
  fb -->|read| chroma
  up <--> sqlite
  tr --> spans
  srv --> spans

  llmc ==>|"HTTPS: the only API-billed call\nopenai/gpt-oss-120b via Groq"| groq(["Groq API"])

  subgraph EVAL["sdk_eval (not on the user path)"]
    ev["cases, golden run, load test,\nregression RED/GREEN"]
    judge["judge / grader: gpt-oss-20b\n(billed separately, reported separately)"]
  end
  ev --> engine
  judge ==> groq
  ev --> runs

  classDef paid fill:#fde7e7,stroke:#b00020;
  classDef local fill:#e8f4ea,stroke:#2e7d32;
  class llmc,groq,judge paid;
  class ver,fb,up,chk,srv,emb2,tr,mcpc local;
```

Cost boundary: only the red nodes touch a paid API. Routing, version resolution, retrieval (local embeddings + Chroma), MCP calls, verification, code checks and tracing are local compute with **no API charge** (they cost CPU time, not dollars; the engine never reports them as 0 USD, it labels them "no API charge (local compute)"). Judge and grader calls exist only in `sdk_eval`, are priced from their own token counts, and are never added to a user query's cost.

Model calls, exactly where they are: **1** in `generate` per answer (a second only when verification rejects the draft and a retry is allowed: 1 of 45 questions in the golden run). **0** in routing, version resolution, tool selection, retrieval, `assess`, `verify`, code validation, `finalize`. Embeddings are a local model (`BAAI/bge-small-en-v1.5`): queries in the MCP server and in the in-process fallback, uploaded text at ingest, the corpus at indexing.

MCP hops per official-corpus question: `retrieve` → `search_docs` (×1, or ×2 for a version comparison), `changelog` → `lookup_changes` (×1 when an endpoint is detected or the question has change intent), `code_validation` → `check_symbol` (per identifier, only when the answer contains a fenced block). Uploaded-document questions make **no** MCP call.

## 2. The state machine (every edge is in `graph.py`)

```mermaid
flowchart TD
  S([START]) --> V[validate_request\nsize, encoding, counts what would be redacted]
  V -->|error| E1([END: error])
  V --> SC[select_corpus\nofficial or upload]
  SC -->|clarify| E2([END: clarify])
  SC -->|upload| RU[route_upload] --> RTU[retrieve_upload\nowner-scoped, in-process]
  RTU -->|error| E1
  RTU --> AS
  SC -->|official| RV[resolve_version\nexplicit > question > operator default > ask]
  RV -->|unsupported / unknown| CL[clarify] --> E3([END])
  RV --> RO[route\nrules: intent, endpoints, needs_changelog]
  RO -->|out of scope| AB[abstain] --> E4([END])
  RO --> RT[retrieve\nMCP search_docs, version-scoped]
  RT -->|error| E1
  RT -->|needs_changelog| CH[changelog\nMCP lookup_changes, labelled cross-version scope]
  RT --> AS
  CH --> AS[assess\nendpoint coverage or distance gate]
  AS -->|insufficient, retries left| RT
  AS -->|insufficient, none left| AB
  AS --> GN[generate\nthe ONE model call]
  GN -->|degraded: no model or call failed| FN[finalize]
  GN --> VF[verify\ncitations in scope, version claims,\noutput safety, code validation]
  VF -->|ok| FN
  VF -->|rejected, retry left| GN
  VF -->|security failure or retries exhausted| AB
  FN --> E5([END: answered / degraded])
```

## 3. One request across the MCP hop (what the trace shows)

```mermaid
sequenceDiagram
  participant U as caller
  participant G as graph (process 1)
  participant C as McpToolClient
  participant S as sdk-docs server (process 2)
  participant D as Chroma (read)
  participant M as Groq (gpt-oss-120b)
  U->>G: ask(question, version?)  [trace id minted: request span]
  G->>G: validate, select corpus, resolve version (no model)
  G->>C: search_docs(query, version, top_k)  [mcp.call span]
  C->>S: tools/call + _meta{trace_id, parent_span_id}
  S->>D: query with where={api_version: version}  [mcp.tool.search_docs span, same trace id]
  D-->>S: passages (scope re-checked on the way out)
  S-->>C: {ok, passages (capped, truncated flag)}
  C-->>G: validated, sanitised evidence
  G->>C: lookup_changes(version, endpoint?)
  C->>S: tools/call
  S-->>C: diff-chunk findings
  G->>G: assess sufficiency (no model)
  G->>M: ONE chat completion (system policy + evidence marked as data)
  M-->>G: draft answer
  G->>G: repair hyphen look-alikes, verify citations / versions / safety, validate code
  G-->>U: answer + citations + code report + trace id
```

If a tool call fails (timeout, error, schema rejection) the graph serves the same request from the in-process retriever, counts it as `mcp_fallbacks`, and the trace says so; the fallback is not a security event.

## 4. Where the build differs from the Phase 0 plan

| Plan (`week12_plan.md` §5) | As built |
|---|---|
| separate `redact_for_logging`, `classify_intent`, `discover_mcp_tools` nodes | redaction is applied at the span sink (`trace.py`, before anything is written) and counted in `validate_request`; intent is decided inside `route`; discovery happens once when the client starts (an allowlist + schema hash, re-validated on every call), not as a graph node |
| `validate_code` node after generate | code validation runs **inside `verify`** (so a SOFT finding can use the same single retry) |
| `finalize_telemetry` | `finalize` plus the request summary on the root span |
| model-proposed routing only if the rules prove inadequate | rules only; no model decides anything but the answer text |
| supervisor / specialist agents | specialist *roles* are labelled nodes and tool calls; no autonomous agents (Week 10's squad did not pay for itself) |
| shared index for uploads | physically separate SQLite store; the MCP server never opens it |

## 5. What the diagram does not show and the system does not have

No authentication (the owner id is caller-supplied), no network service (a CLI and a stdio server), no cache, no queue, no rate limiter of its own, no horizontal scaling story beyond "run more processes against a read-only index" (Phase 10). Known weaknesses, with evidence: `known_weaknesses.md`.
