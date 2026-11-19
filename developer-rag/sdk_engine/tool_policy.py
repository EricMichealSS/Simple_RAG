"""Tool policy: what may be called, with what arguments. Deterministic; no model involved.

* Discovery is not authorization. A server may advertise any tool; only names the OPERATOR listed in
  that server's `allowed_tools` are callable (and, if pinned, only with the pinned schema hash).
* Tool names, descriptions and schemas are UNTRUSTED data from another process. Names are charset-
  checked, descriptions are sanitised/truncated and flagged when they read like instructions.
* A decision (from rules or, later, from a model) is only a PROPOSAL. `validate_decision` re-checks the
  tool against the discovered allowlist, validates arguments against the discovered JSON Schema,
  rejects unknown keys, bounds size, and lets the CALLER-OWNED arguments (the resolved version) win:
  an attempt to override them is a rejection, not a silent correction.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_DESCRIPTION_CHARS = 600
MAX_ARGUMENT_CHARS = 4000

# Phrases that make a tool description look like it is addressing the model rather than describing a tool.
_INSTRUCTION_LIKE = re.compile(
    r"(ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)|disregard\s+(?:the\s+)?(?:above|previous)|system\s+prompt|"
    r"you\s+(?:must|should)\s+(?:always|first|never)|always\s+call|before\s+(?:answering|responding).{0,40}(?:call|send|use)|"
    r"do\s+not\s+(?:tell|mention|reveal)|exfiltrat|send\s+.{0,60}\bto\s+https?://|<\s*important\s*>|\bapi[_ -]?key\b|secret)",
    re.IGNORECASE | re.DOTALL,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ServerConfig(BaseModel):
    """One MCP server the OPERATOR configured. Nothing else is ever connected to."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_\-]{0,31}$")
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)   # the ONLY env passed (plus a minimal safe default); no inherited secrets
    cwd: Optional[str] = None
    allowed_tools: Tuple[str, ...] = ()
    pinned_schema_hashes: Dict[str, str] = Field(default_factory=dict)  # tool -> expected schema hash (optional)


class ToolSpec(BaseModel):
    server: str
    name: str
    description: str
    input_schema: Dict[str, Any]
    schema_hash: str
    authorized: bool
    rejected_reason: Optional[str] = None
    suspicious_description: bool = False


class ToolDecision(BaseModel):
    """A proposal to call one tool. Produced by rules today; a model could produce one later."""

    tool: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    made_by: Literal["rule", "model"] = "rule"
    rationale: str = ""


class ToolRejected(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def schema_hash(schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def sanitize_description(text: Optional[str]) -> str:
    return _CONTROL_RE.sub(" ", text or "").strip()[:MAX_DESCRIPTION_CHARS]


def build_registry(server: ServerConfig, listed: Iterable[Any]) -> List[ToolSpec]:
    """Turn a server's `tools/list` result into ToolSpecs. Never raises on a hostile tool; marks it unauthorized."""
    specs: List[ToolSpec] = []
    seen = set()
    for t in listed:
        name = getattr(t, "name", "") or ""
        schema = dict(getattr(t, "inputSchema", None) or {})
        desc = sanitize_description(getattr(t, "description", ""))
        h = schema_hash(schema)
        reason: Optional[str] = None
        if not TOOL_NAME_RE.match(name):
            reason = "invalid tool name"
        elif name in seen:
            reason = "duplicate tool name from the same server"
        elif name not in server.allowed_tools:
            reason = "not in the operator's allowlist for this server"
        elif name in server.pinned_schema_hashes and server.pinned_schema_hashes[name] != h:
            reason = "schema hash differs from the pinned value"
        else:
            try:
                Draft202012Validator.check_schema(schema)
            except Exception:
                reason = "tool input schema is not valid JSON Schema"
        seen.add(name)
        specs.append(ToolSpec(server=server.name, name=name if TOOL_NAME_RE.match(name) else "invalid", description=desc,
                              input_schema=schema, schema_hash=h, authorized=reason is None, rejected_reason=reason,
                              suspicious_description=bool(_INSTRUCTION_LIKE.search(desc))))
    return specs


def validate_decision(
    decision: ToolDecision,
    registry: Sequence[ToolSpec],
    *,
    enforced: Optional[Mapping[str, Any]] = None,
) -> Tuple[ToolSpec, Dict[str, Any]]:
    """Return (tool, validated_arguments) or raise ToolRejected. `enforced` holds caller-owned arguments
    (e.g. the resolved version) that a decision may not contradict."""
    enforced = dict(enforced or {})
    matches = [t for t in registry if t.name == decision.tool]
    if not matches:
        raise ToolRejected("unknown_tool", f"tool {decision.tool!r} was not discovered")
    tool = matches[0]
    if not tool.authorized:
        raise ToolRejected("unauthorized_tool", f"tool {tool.name!r} is not authorized: {tool.rejected_reason}")

    args = dict(decision.arguments)
    for key, value in enforced.items():
        if key in args and args[key] != value:
            raise ToolRejected("enforced_argument_override",
                               f"argument {key!r} is owned by the caller's resolved context and may not be changed by a decision")
        args[key] = value

    schema = tool.input_schema
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolRejected("unknown_argument", f"arguments not in the tool schema: {unknown}")
    if len(json.dumps(args, default=str)) > MAX_ARGUMENT_CHARS:
        raise ToolRejected("arguments_too_large", f"arguments exceed {MAX_ARGUMENT_CHARS} characters")
    errors = sorted(Draft202012Validator(schema).iter_errors(args), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = ".".join(str(p) for p in e.path) or "(root)"
        raise ToolRejected("invalid_arguments", f"argument {where}: {e.message[:200]}")
    return tool, args
