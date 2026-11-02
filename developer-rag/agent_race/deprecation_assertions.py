"""Deterministic (non-LLM) checks for "did the answer use a deprecated
method/field instead of its replacement." Week 11 production brief:
"Prefer deterministic checks for known forbidden methods and version
constraints. Use an LLM judge only where it adds value."

This mirrors the spirit of developer-rag/eval/assertions.py (deterministic
checks alongside the LLM judge), but lives in agent_race/ because this
track's evaluation suite (questions.py + grader.py) is separate from the
main app's eval/ suite. No new dependency: pure string/regex matching.
"""

import re


def uses_forbidden_term_without_replacement(answer: str, forbidden: str, required_replacement: str) -> dict:
    """FAIL if the answer's CODE SAMPLE or prose uses the deprecated/forbidden
    term as a literal field/parameter name and never mentions the required
    replacement term anywhere in the answer.

    This is deliberately conservative: it does not fail just because the
    word "assignee" appears in passing (e.g. explaining the deprecation
    itself almost always requires saying the old name) -- it only fails
    when the replacement is ABSENT, meaning the answer never actually
    told the developer what to use instead, or showed a code sample using
    only the old field.
    """
    if not answer:
        return {"passed": False, "reason": "no answer produced (empty/None)"}

    has_forbidden = re.search(rf"\b{re.escape(forbidden)}\b", answer, re.IGNORECASE) is not None
    has_replacement = re.search(rf"\b{re.escape(required_replacement)}\b", answer, re.IGNORECASE) is not None

    if has_forbidden and not has_replacement:
        return {
            "passed": False,
            "reason": (
                f"answer uses the deprecated term '{forbidden}' but never mentions the "
                f"required replacement '{required_replacement}' -- this is the deprecated-method "
                f"code-sample bug this case guards against."
            ),
        }
    if not has_replacement:
        return {
            "passed": False,
            "reason": f"answer never mentions the required replacement term '{required_replacement}' at all.",
        }
    return {"passed": True, "reason": f"replacement term '{required_replacement}' is present."}


def code_sample_uses_field(answer: str, field_name: str) -> bool:
    """True if a bare, singular use of `field_name` appears in a code-sample-
    shaped line (e.g. `"assignee": "octocat"` or `assignee=octocat`) rather
    than only in prose describing the deprecation. Used for the stricter
    check: even mentioning the replacement in prose does not excuse actually
    emitting the deprecated field in a shown code sample.
    """
    pattern = rf'["\']?{re.escape(field_name)}["\']?\s*[:=]'
    return re.search(pattern, answer, re.IGNORECASE) is not None
