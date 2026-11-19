"""Prompt-injection DETECTION for telemetry, plus invisible-text sanitising.

This module is deliberately NOT a defence on its own. Pattern matching is incomplete by nature, so nothing in the engine
depends on it for safety. The guarantees come from the architecture (see docs/week12/phase5.md):

  * policy lives in the system message; retrieved/uploaded/tool text is quoted DATA inside delimiters it cannot close;
  * the version scope, tool allowlist, owner scope and tool arguments are enforced by code the text cannot reach;
  * the answer is checked deterministically afterwards (citations, versions, canary / system-prompt leak, URLs, secrets).

What detection adds: a security-event trail (which chunk, which kind of attempt, how severe) so an operator can see attacks
that the architecture neutralised, and a `flagged` marker on the quoted document so the model is reminded. Text is never
dropped because of a flag: legitimate documentation can discuss prompt injection, and silently deleting evidence would be
its own failure mode.

Scanned text is capped and excerpts are redacted and short, so a flag can never carry a secret into a log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from .redaction import redact_text

MAX_SCAN_CHARS = 20000
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}

# Zero-width characters, bidi overrides/isolates and other format characters that can hide text from a human reviewer.
_INVISIBLE = dict.fromkeys(
    list(range(0x200B, 0x2010)) + list(range(0x202A, 0x202F)) + list(range(0x2060, 0x2065)) + list(range(0x2066, 0x206A))
    + [0xFEFF, 0x00AD, 0x180E], None)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_P = re.compile
# (name, severity, compiled regex). Kept conservative: each was checked against the real corpus for false positives.
PATTERNS: List[Tuple[str, str, "re.Pattern[str]"]] = [
    ("override_instructions", "high", _P(
        r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all|any|your|the)\b[^.\n]{0,30}"
        r"\b(?:instructions?|rules?|prompts?|guidelines?|polic(?:y|ies)|restrictions?|directives?)\b", re.I)),
    ("role_reassignment", "medium", _P(
        r"\byou\s+are\s+now\s+(?:a|an|the|in)\b|\bnew\s+(?:system\s+)?instructions?\s*:|\bfrom\s+now\s+on,?\s+you\b|"
        r"\b(?:developer|god|admin|debug)\s+mode\b|\bjailbreak\b|\bpretend\s+(?:to\s+be|you\s+are)\b", re.I)),
    ("system_marker", "medium", _P(
        # line start, OR right after a sentence boundary ("... a string. SYSTEM: ignore ..."); never mid-sentence, so
        # "Operating system: Linux" does not match.
        r"(?im)^\s*(?:#{1,4}\s*)?(?:system|assistant|developer)\s*(?:prompt|message)?\s*:|[.!?]\s+(?:system|assistant|developer)\s*(?:prompt|message)?\s*:|"
        r"<\|im_(?:start|end)\|>|\[/?INST\]|<</?SYS>>|</?system>")),
    ("reveal_secrets_or_prompt", "high", _P(
        r"\b(?:reveal|print|show|output|repeat|display|leak|dump|tell\s+me)\b[^.\n]{0,40}\b(?:system\s+prompt|your\s+instructions|"
        r"hidden\s+instructions|api[\s_-]?keys?|secrets?|credentials|environment\s+variables|passwords?)\b", re.I)),
    ("exfiltration", "high", _P(
        r"\b(?:send|post|upload|forward|email|transmit|exfiltrate|leak)\b[^.\n]{0,80}\b(?:api[\s_-]?keys?|secrets?|tokens?|passwords?|"
        r"credentials|system\s+prompt|conversation|chat\s+history|the\s+user'?s?\s+(?:data|files?|documents?))\b[^.\n]{0,80}"
        r"\b(?:to|at|via)\b[^.\n]{0,30}(?:https?://|\bwebhook\b|\bserver\b)", re.I)),
    ("tool_coercion", "high", _P(
        # Only the word "tool": "call the send function" / "run the following command" are ordinary SDK documentation.
        r"\b(?:call|invoke|use|run|execute|trigger)\s+(?:the\s+|your\s+)?(?!(?:following|next|above|below|this|that|same|given|latter|new)\b)"
        r"[\w\-]+\s+tool\b|"
        r"\bcurl\b[^\n|]*\|\s*(?:sh|bash)\b|\brm\s+-rf\b|\bpowershell\b[^\n]*-enc", re.I)),
    ("hide_from_user", "medium", _P(
        r"\b(?:do\s+not|don'?t|never)\s+(?:tell|mention|reveal|show|inform)\b[^.\n]{0,30}\b(?:the\s+)?user\b|"
        r"\bwithout\s+(?:telling|informing)\s+the\s+user\b|\bkeep\s+this\s+(?:secret|hidden)\b", re.I)),
    ("delimiter_breakout", "medium", _P(r"</?\s*documents?\b[^>]*>|<\s*/?\s*untrusted", re.I)),
    ("external_image", "low", _P(r"!\[[^\]]*\]\(\s*https?://", re.I)),
]


@dataclass(frozen=True)
class InjectionFlag:
    pattern: str
    severity: str
    excerpt: str     # short, redacted, for the security log only


def strip_invisible(text: str) -> Tuple[str, int]:
    """Remove zero-width / bidi / stray control characters. Returns (clean_text, how_many_were_removed)."""
    cleaned = text.translate(_INVISIBLE)
    cleaned = _CONTROL.sub("", cleaned)
    return cleaned, len(text) - len(cleaned)


def scan_text(text: str) -> List[InjectionFlag]:
    """Pattern-scan (a prefix of) `text`. Returns one flag per distinct pattern that matched."""
    if not text:
        return []
    sample = text[:MAX_SCAN_CHARS]
    flags: List[InjectionFlag] = []
    # Patterns run on the text with invisible characters REMOVED: "ig<ZWSP>nore previous instructions" must not evade them.
    clean, hidden = strip_invisible(sample)
    if hidden:
        flags.append(InjectionFlag("invisible_text", "medium", f"{hidden} invisible/format characters"))
    for name, severity, rx in PATTERNS:
        m = rx.search(clean)
        if m:
            snippet = " ".join(clean[max(0, m.start() - 15): m.end() + 15].split())
            flags.append(InjectionFlag(name, severity, redact_text(snippet)[:90]))
    return flags


def max_severity(flags: Iterable[InjectionFlag]) -> str:
    best = max((SEVERITY_ORDER[f.severity] for f in flags), default=0)
    return {0: "none", 1: "low", 2: "medium", 3: "high"}[best]


def flag_names(flags: Iterable[InjectionFlag]) -> List[str]:
    return [f.pattern for f in flags]


def summarize(flags_by_source: Dict[str, Sequence[InjectionFlag]]) -> List[Dict[str, str]]:
    """Security-event records (no content beyond the short redacted excerpt) for spans and the result."""
    out: List[Dict[str, str]] = []
    for source, flags in flags_by_source.items():
        for f in flags:
            out.append({"source": source, "pattern": f.pattern, "severity": f.severity, "excerpt": f.excerpt})
    return out
