"""If the limiter pre-checks input + max_tokens against the 8,000 tokens/minute bucket, a request with a tiny prompt and max_tokens just over the
limit is rejected although it would use ~100 tokens. Judge model only. Prints the status and the provider's message, never the key."""
import json
import os

from dotenv import load_dotenv

load_dotenv("C:/Projects/AI Learning/Simple_RAG/developer-rag/.env", override=False)
from openai import OpenAI  # noqa: E402

client = OpenAI(api_key=os.environ.get("GROQ_API_KEY") or os.environ.get("GROK_API_KEY"), base_url="https://api.groq.com/openai/v1", timeout=30.0, max_retries=0)
out = []
for mt in (7000, 7950, 9000):
    try:
        r = client.chat.completions.create(model="openai/gpt-oss-20b", messages=[{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=mt)
        out.append({"max_tokens": mt, "status": 200, "usage": [r.usage.prompt_tokens, r.usage.completion_tokens]})
    except Exception as exc:
        body = getattr(exc, "body", None)
        msg = body.get("error", {}).get("message") if isinstance(body, dict) and isinstance(body.get("error"), dict) else str(exc)[:200]
        out.append({"max_tokens": mt, "status": getattr(exc, "status_code", None), "message": msg})
print(json.dumps(out, indent=1))
