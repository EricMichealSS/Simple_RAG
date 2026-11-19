"""Replaceable LLM provider boundary.

The graph depends only on the :class:`LLMClient` protocol. ``GroqChat`` is the default adapter
(OpenAI-compatible endpoint, the project's existing credential path); tests use fakes. A missing key
or provider outage raises :class:`LLMUnavailable`, which the graph turns into a degraded, extractive
answer instead of a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol

from .config import EngineConfig, get_api_key
from .pricing import cost_usd


class LLMUnavailable(RuntimeError):
    """No key, network failure, rate limit, or timeout - anything that means 'cannot generate now'."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    finish_reason: Optional[str] = None   # "stop" | "length" (cut off at max_tokens) | ...

    @property
    def cost_usd(self) -> Optional[float]:
        return cost_usd(self.model, self.prompt_tokens, self.completion_tokens)


class LLMClient(Protocol):
    model: str

    def complete(self, messages: List[Dict[str, str]], *, max_tokens: int, temperature: float = 0.0) -> LLMResult: ...


class GroqChat:
    """Default adapter. The OpenAI client is built lazily so importing this module needs no key."""

    provider = "groq"      # recorded on every generate span (an OpenAI-compatible endpoint; see config.llm_base_url)

    def __init__(self, config: EngineConfig):
        self.model = config.generation_model
        self._base_url = config.llm_base_url
        self._timeout = config.llm_timeout_s
        self._client = None

    def _get_client(self):
        if self._client is None:
            key = get_api_key()
            if not key:
                raise LLMUnavailable("no API key configured (set GROQ_API_KEY, or the legacy GROK_API_KEY, in the environment or a local .env)")
            from openai import OpenAI

            self._client = OpenAI(api_key=key, base_url=self._base_url, timeout=self._timeout, max_retries=0)
        return self._client

    def complete(self, messages: List[Dict[str, str]], *, max_tokens: int, temperature: float = 0.0) -> LLMResult:
        client = self._get_client()
        try:
            resp = client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=max_tokens, temperature=temperature
            )
        except Exception as exc:  # provider errors vary by SDK version; all mean "unavailable now"
            # Never include exc text blindly: provider errors can echo request headers. Class name only.
            raise LLMUnavailable(f"provider call failed: {type(exc).__name__}") from exc
        usage = resp.usage
        text = (resp.choices[0].message.content or "").strip()
        finish = getattr(resp.choices[0], "finish_reason", None)
        return LLMResult(text=text, prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                         model=self.model, finish_reason=finish)
