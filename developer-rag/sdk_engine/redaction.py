"""Conservative secret / PII redaction, applied BEFORE anything is written to a log or trace.

Design goals, in priority order:

1. Never leak: API keys, bearer tokens, auth headers, passwords, credentialled connection strings,
   private-key blocks, emails, card numbers (Luhn-checked), US SSN-shaped numbers, phone numbers.
2. Never corrupt telemetry: version strings (``2025-06-01``, ``3.x``), chunk ids
   (``doc:p5:spec:create-an-issue:3``), counters (``max_tokens=512``) must survive untouched.
   Redaction that mangles the fields we filter traces by would defeat its purpose.
3. Be explicit about what is NOT covered: person names, street addresses, free-form personal
   facts and secrets with no recognisable shape are NOT detected by regex. Callers must not rely
   on this module to anonymise arbitrary prose, and raw prompts/uploads are not logged by default.

Pure functions, stdlib only, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

REDACTED = "[REDACTED"

# Keys whose VALUE is always replaced wholesale when redacting structured data.
SENSITIVE_KEYS = frozenset(
    {
        "authorization", "proxy-authorization", "x-api-key", "x-auth-token", "api_key", "apikey",
        "api-key", "password", "passwd", "pwd", "secret", "client_secret", "access_token",
        "auth_token", "refresh_token", "token", "private_key", "grok_api_key", "openai_api_key",
        "hf_token", "credentials",
    }
)


def _tag(kind: str) -> str:
    return f"{REDACTED}:{kind}]"


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


# (kind, compiled pattern, replacer(match) -> str). Order matters: most specific first.
_RULES: List[Tuple[str, "re.Pattern[str]", Callable[["re.Match[str]"], str]]] = []


def _rule(kind: str, pattern: str, flags: int = 0, repl: Callable[["re.Match[str]"], str] | None = None):
    compiled = re.compile(pattern, flags)
    _RULES.append((kind, compiled, repl or (lambda m, k=kind: _tag(k))))


# 1. Private key blocks (multi-line).
_rule("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)

# 2. Credentials inside connection strings: scheme://user:pass@host  ->  scheme://[REDACTED:credentials]@host
_rule(
    "connection-string",
    r"\b([a-z][a-z0-9+.\-]*://)([^\s:/@]+):([^\s@/]+)@",
    re.IGNORECASE,
    lambda m: f"{m.group(1)}{_tag('credentials')}@",
)

# 3. Auth-ish header / field with its value (keeps the key name, hides the value up to a delimiter).
_rule(
    "auth-header",
    r"\b(authorization|proxy-authorization|x-api-key|x-auth-token)(\"?'?\s*[:=]\s*\"?'?)([^\r\n\"',;}]+)",
    re.IGNORECASE,
    lambda m: f"{m.group(1)}{m.group(2)}{_tag('auth')}",
)

# 4. Bearer tokens appearing outside a header.
_rule("bearer", r"\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}", re.IGNORECASE)

# 5. Well-known provider key shapes.
# NOTE: kind labels are used as keys in telemetry counters ({"provider-key": 1}); a label must never
# equal a name in SENSITIVE_KEYS or redact_obj would blank the counter (guarded by a test).
_rule("provider-key", r"\bgsk_[A-Za-z0-9]{20,}")            # Groq
_rule("provider-key", r"\bsk-[A-Za-z0-9_\-]{20,}")          # OpenAI-style
_rule("provider-key", r"\bxai-[A-Za-z0-9]{20,}")            # xAI
_rule("provider-key", r"\bAIza[0-9A-Za-z_\-]{30,}")         # Google
_rule("provider-key", r"\bgh[pousr]_[A-Za-z0-9]{30,}")      # GitHub tokens
_rule("provider-key", r"\bgithub_pat_[A-Za-z0-9_]{30,}")    # GitHub fine-grained PAT
_rule("provider-key", r"\bhf_[A-Za-z0-9]{30,}")             # Hugging Face
_rule("provider-key", r"\bAKIA[0-9A-Z]{16}\b")              # AWS access key id
_rule("provider-key", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")   # Slack
_rule("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")

# 6. name = value / name: value assignments for secret-ish names. `\b` keeps `max_tokens`,
#    `total_tokens`, `prompt_tokens` safe because `_` is a word character.
_rule(
    "secret-value",
    r"([\"']?)\b(password|passwd|pwd|secret|api[_\-]?key|apikey|access[_\-]?token|auth[_\-]?token|"
    r"refresh[_\-]?token|client[_\-]?secret|token)\b([\"']?)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&)}\]]+)",
    re.IGNORECASE,
    lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{_tag('secret')}",
)

# 7. PII.
_rule("email", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}\b")
_rule("ssn", r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
# Phone: deliberately strict shapes only (+CC, or 3-3-4 with separators) so ISO dates never match.
_rule("phone", r"(?<![\d-])(?:\+\d{1,3}[ \-.]?)?\(?\d{3}\)?[ \-.]\d{3}[ \-.]\d{4}(?![\d-])")


_CARD_RE = re.compile(r"(?<![\d-])(?:\d[ \-]?){13,19}(?![\d-])")


def _redact_cards(text: str, counts: Dict[str, int]) -> str:
    def repl(m: "re.Match[str]") -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            counts["card"] = counts.get("card", 0) + 1
            return _tag("card")
        return m.group(0)

    return _CARD_RE.sub(repl, text)


@dataclass
class RedactionResult:
    text: str
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def redact_with_counts(text: str) -> RedactionResult:
    """Redact `text`; report how many substitutions of each kind were made (never the values)."""
    if not isinstance(text, str) or not text:
        return RedactionResult(text if isinstance(text, str) else "", {})
    counts: Dict[str, int] = {}
    out = text
    for kind, pattern, repl in _RULES:
        def _counting(m: "re.Match[str]", _repl=repl, _kind=kind) -> str:
            counts[_kind] = counts.get(_kind, 0) + 1
            return _repl(m)

        out = pattern.sub(_counting, out)
    out = _redact_cards(out, counts)
    return RedactionResult(out, counts)


SECRET_KINDS = frozenset({"provider-key", "jwt", "private-key", "connection-string", "bearer"})


def find_secrets(text: str, kinds: Iterable[str] = SECRET_KINDS) -> List[str]:
    """The literal secret-shaped substrings in `text` (strict shapes only, so ordinary documentation prose never matches)."""
    wanted = set(kinds)
    found: List[str] = []
    for kind, pattern, _ in _RULES:
        if kind in wanted:
            found.extend(m.group(0) for m in pattern.finditer(text))
    return found


def redact_text(text: str) -> str:
    return redact_with_counts(text).text


def redact_obj(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact strings inside dicts/lists/tuples. Values under sensitive KEYS are replaced
    wholesale. Non-string scalars pass through. Depth-limited to stay safe on cyclic/huge input."""
    if _depth > 20:
        return _tag("max-depth")
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS and v not in (None, "", False):
                out[k] = _tag("key-name")
            else:
                out[k] = redact_obj(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, _depth + 1) for v in obj]
    return obj


def truncate(text: str, limit: int) -> str:
    """Cap logged text; mark the cut so a truncated value is never mistaken for a complete one."""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"
