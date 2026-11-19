"""Does the provider's tokens-per-minute accounting charge the reserved max_tokens or only the tokens actually used?
Two near-identical tiny requests on the JUDGE model (its own bucket), differing only in max_tokens. Prints header deltas only, never the key."""
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv("C:/Projects/AI Learning/Simple_RAG/developer-rag/.env", override=False)
from openai import OpenAI  # noqa: E402

client = OpenAI(api_key=os.environ.get("GROQ_API_KEY") or os.environ.get("GROK_API_KEY"), base_url="https://api.groq.com/openai/v1", timeout=30.0, max_retries=0)
MODEL = "openai/gpt-oss-20b"


def call(max_tokens):
    raw = client.chat.completions.with_raw_response.create(model=MODEL, messages=[{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=max_tokens)
    resp = raw.parse()
    h = raw.headers
    return {"max_tokens": max_tokens, "remaining_tokens_header": h.get("x-ratelimit-remaining-tokens"), "usage": [resp.usage.prompt_tokens, resp.usage.completion_tokens]}


rows = []
for mt in (16, 1500, 16, 1500):
    rows.append(call(mt))
    time.sleep(0.4)                  # the bucket refills ~133 tokens/s, so deltas below ~100 are noise; the question is whether a 1,500 reservation shows
print(json.dumps(rows, indent=1))
