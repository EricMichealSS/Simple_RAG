"""Per-token pricing with an explicit "unpriced" state.

Rates are copied from ``agent_race/pricing.py`` (Groq's published per-token prices, looked up
2026-09-10 according to that file; not re-verified here). Unlike the legacy function this one never
raises and never invents a rate: an unknown model returns ``None`` and callers must record cost as
UNAVAILABLE rather than zero.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# model -> (USD per 1M input tokens, USD per 1M output tokens)
PRICE_PER_MILLION: Dict[str, Tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
}


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    rates = PRICE_PER_MILLION.get(model)
    if rates is None:
        return None
    return (prompt_tokens / 1_000_000) * rates[0] + (completion_tokens / 1_000_000) * rates[1]
