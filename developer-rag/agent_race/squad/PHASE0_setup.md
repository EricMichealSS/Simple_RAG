# Phase 0 — Protocol and setup

**Done:** 2026-10-04

## What was done
- Re-checked the environment: Python 3.9.6 venv, chromadb 1.5.9, openai 2.48.0, sentence_transformers 5.1.2. All three docs tools (`search_docs`, `get_openapi_spec`, `check_deprecation`) run against the existing Chroma index. Week 9's newer chromadb did not break it.
- Wrote `protocol.md`: same cases, same judge, same model, identical ceilings, 3 interleaved repeats, failure definitions, and the keep/kill rule, all frozen before any run.
- Wrote `prediction.txt` with a dated prediction (multiplier 2–4x, single wins cost and latency, pass rates within one question, biggest share = manager re-reading worker results).

## Two mismatches with the task text, handled openly
1. The brief says "last week you built an orchestrator". Week 9 was MCP, so no orchestrator existed. It is built this week (Phases 2–3).
2. "Week-6 eval cases" belong to the older RAG corpus, which the agent's tools cannot answer. The same 10 questions raced in Week 7 are used for both arms. The ruler is not changed between arms, which is what the brief's rule protects.

## Edge cases considered
- The old 8,000-token cap would fail the squad on structure alone; the ceiling is raised identically for both arms.
- Rate limits from the provider are not pattern failures: they are retried and logged separately.
- No git operations are performed by the assistant.
