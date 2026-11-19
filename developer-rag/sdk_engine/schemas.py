"""Pydantic contracts shared by every node of the engine.

Model-generated decisions are *proposals*: they are parsed into these schemas and then checked
against deterministic allowlists before anything executes (see docs/week12/week12_plan.md §5).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Product(str, Enum):
    GITHUB_REST = "github_rest"
    SWAGGER_CODEGEN = "swagger_codegen"


class VersionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"        # conflicting or insufficient signals -> ask, never guess
    UNKNOWN = "unknown"            # no version evidence at all
    UNSUPPORTED = "unsupported"    # a version was named but the corpus has no docs for it


class VersionSource(str, Enum):
    EXPLICIT = "explicit"   # caller passed it
    QUESTION = "question"   # parsed from the question text
    CONFIG = "config"       # operator-configured default (lower confidence)
    NONE = "none"


class VersionResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: VersionStatus
    product: Optional[Product] = None
    version: Optional[str] = None
    source: VersionSource = VersionSource.NONE
    comparison_versions: List[str] = Field(default_factory=list)  # set only for explicit migration/compare intent
    candidates: List[str] = Field(default_factory=list)           # what we saw / what is available
    needs_clarification: bool = False
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _consistent(self) -> "VersionResolution":
        if self.status is VersionStatus.RESOLVED:
            if not self.version or self.product is None:
                raise ValueError("a RESOLVED version needs both version and product")
            if self.needs_clarification:
                raise ValueError("a RESOLVED version cannot also need clarification")
        elif self.version is not None:
            raise ValueError("only a RESOLVED resolution may carry a version")
        return self

    @property
    def is_comparison(self) -> bool:
        return len(self.comparison_versions) >= 2


class SourceType(str, Enum):
    OFFICIAL_DOC = "official_doc"
    CHANGELOG = "changelog"      # the "what changed / previous version" text
    COMMUNITY = "community"
    ISSUE = "issue"
    UPLOAD = "upload"


class Passage(BaseModel):
    """One retrieved chunk with the provenance needed to cite it."""

    chunk_id: str
    text: str
    source_name: str
    source_type: SourceType = SourceType.OFFICIAL_DOC
    corpus: Literal["official", "upload"] = "official"
    api_version: Optional[str] = None
    product: Optional[Product] = None
    page: Optional[int] = None
    anchor: str = ""
    endpoint: str = ""               # corpus endpoint / generator id, e.g. "create_issue"
    content_type: str = ""           # "spec" | "diff" for the official corpus
    has_diff: bool = False
    score: Optional[float] = None    # rerank score if reranked, else fused RRF score
    dense_distance: Optional[float] = None  # real vector distance; None when found by BM25 only (never a placeholder)
    cross_version: bool = False      # True only inside an explicitly authorised comparison
    owner_id: Optional[str] = None   # uploads only
    injection_flags: List[str] = Field(default_factory=list)

    @property
    def location(self) -> str:
        loc = self.source_name
        if self.page is not None:
            loc += f" p.{self.page}"
        if self.anchor:
            loc += f" #{self.anchor}"
        return loc


class Citation(BaseModel):
    source_name: str
    api_version: Optional[str]
    location: str
    chunk_id: str
    passage_excerpt: str = Field(max_length=400)


class DeprecationNote(BaseModel):
    """A 'what changed' finding surfaced proactively, always labelled with the version it applies to."""

    caller_version: Optional[str]
    applies_to_version: Optional[str]
    cross_version: bool                 # True: describes a NEWER version than the caller's (upgrade heads-up)
    excerpt: str = Field(max_length=700)
    citation: Citation


class Usage(BaseModel):
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: Optional[str] = None
    cost_usd: Optional[float] = None
    cost_status: Literal["calculated", "unavailable", "none"] = "none"  # never a silent 0 for an unpriced model


class CodeFinding(BaseModel):
    """One check result about one thing in a code sample. `severity`: info = supported; warn = shown to the user only;
    soft = triggers one correction retry, then is shown as a warning; hard = the answer is never delivered with it."""

    symbol: str
    kind: Literal["syntax", "route", "header", "body_key", "query_param", "flag", "kwarg", "dict_key", "required_param"]
    tier: Literal["cited", "elsewhere_in_version", "deprecated", "absent_per_docs", "other_version_only", "unverified",
                  "not_checked", "syntax_error", "missing_required"]
    severity: Literal["info", "warn", "soft", "hard"]
    detail: str = ""
    chunk_id: Optional[str] = None
    replacement: Optional[str] = None
    other_versions: List[str] = Field(default_factory=list)
    # why an identifier could not be checked (tier `not_checked` only): machine-readable, so a trace can say "unchecked because ..."
    reason_code: Optional[Literal["no_symbol_index", "lookup_unavailable", "lookup_limit"]] = None


class CodeBlockReport(BaseModel):
    index: int
    language: str                                   # normalised, or "unknown"
    lines: int
    syntax: Literal["parsed", "error", "not_checked"]
    syntax_detail: str = ""
    findings: List[CodeFinding] = Field(default_factory=list)
    unsupported_checks: List[str] = Field(default_factory=list)   # what was NOT checked for this block, and why


class CodeValidationReport(BaseModel):
    """Static, corpus-backed validation of the code samples in one answer. Nothing is executed: `parsed` means the text is
    syntactically well formed, and `cited`/`elsewhere_in_version` mean the docs mention the identifier, not that it works."""

    version: Optional[str] = None
    blocks: List[CodeBlockReport] = Field(default_factory=list)
    symbol_source: Literal["mcp", "local", "none"] = "none"     # where `check_symbol` evidence came from
    lookups: int = 0
    skipped_blocks: int = 0                                      # beyond the per-answer block limit
    unsupported_checks: List[str] = Field(default_factory=list)

    @property
    def hard(self) -> int:
        return sum(f.severity == "hard" for b in self.blocks for f in b.findings)

    @property
    def soft(self) -> int:
        return sum(f.severity == "soft" for b in self.blocks for f in b.findings)


class AnswerResult(BaseModel):
    trace_id: str
    request_id: str
    outcome: Literal["answered", "degraded", "clarify", "abstain", "error"]
    answer: str
    reason: str = ""
    clarification: Optional[str] = None
    version: Optional[VersionResolution] = None
    route: Optional["RouteDecision"] = None
    citations: List[Citation] = Field(default_factory=list)
    deprecation_notes: List[DeprecationNote] = Field(default_factory=list)
    # Evidence-backed "X is deprecated in favor of Y" statements found by the lookup_changes tool; each carries
    # the version it applies to and a chunk id. Empty when the in-process fallback served the request.
    deprecation_facts: List[Dict[str, Any]] = Field(default_factory=list)
    retrieval_via: str = ""            # "mcp" | "local" | "uploads" (which backend actually served retrieval)
    corpus: Literal["official", "upload"] = "official"
    # Security-event trail: injection-like content seen (source, pattern, severity, short redacted excerpt) and any
    # answer-side safety failure. Detection is telemetry; the guarantees are architectural (docs/week12/phase5.md).
    security: List[Dict[str, str]] = Field(default_factory=list)
    code_validation: Optional[CodeValidationReport] = None   # None when the answer contains no code block
    retrieved_chunk_ids: List[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = 0.0


class Intent(str, Enum):
    LOOKUP = "lookup"
    MIGRATION = "migration"
    DEPRECATION_CHECK = "deprecation_check"
    CODE_HELP = "code_help"
    UPLOADED_DOC = "uploaded_doc"
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN = "unknown"


class RouteDecision(BaseModel):
    intent: Intent
    needs_retrieval: bool = True
    needs_changelog: bool = False
    needs_code_validation: bool = False
    corpus: Literal["official", "upload"] = "official"
    specialists: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    made_by: Literal["rule", "model"] = "rule"
    rationale: str = ""


AnswerResult.model_rebuild()
