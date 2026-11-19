"""Week 12 SDK documentation answer engine.

A thin, additive layer over the existing project. It does NOT modify the legacy retrieval
(`rag_hybrid.retrieve`) or agent (`agent_race/agent.run_agent`) code, both of which GitNexus
impact analysis rates HIGH / CRITICAL for in-place edits (many callers, Week 6 eval flows).

Module map (each importable without network, API key, or ML models unless stated):

    config      EngineConfig - paths and limits; no secrets read at import time
    redaction   conservative secret / PII redaction applied before anything is logged
    trace       one trace id per request, spans with parent ids, JSONL sink (redacted)
    schemas     Pydantic contracts shared by every node
    versioning  caller SDK-version resolver (explicit > question > config > ask; never guesses)
    retrieval   version-scoped retriever: the version filter is a metadata `where`, not a prompt
"""

__all__ = ["config", "redaction", "trace", "schemas", "versioning", "retrieval"]
