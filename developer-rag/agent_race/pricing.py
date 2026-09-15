"""Published Groq per-token pricing, used to compute cost/question for both
systems. Looked up 2026-09-10 (Groq's own pricing page + OpenRouter/Requesty
model pages, which mirror Groq's published rates for hosted OSS models).

    gpt-oss-120b: $0.15 / 1M input tokens, $0.60 / 1M output tokens
    gpt-oss-20b:  $0.075 / 1M input tokens, $0.30 / 1M output tokens
"""

MODEL = "openai/gpt-oss-120b"

PRICE_PER_MILLION = {
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
    "openai/gpt-oss-20b": {"input": 0.075, "output": 0.30},
}


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = PRICE_PER_MILLION[model]
    return (prompt_tokens / 1_000_000) * rates["input"] + (completion_tokens / 1_000_000) * rates["output"]
