"""One tiny request to read the provider's rate-limit headers / error text for the answer model and the judge model. Prints no key."""
import json
import os
import sys

sys.path.insert(0, "C:/Projects/AI Learning/Simple_RAG/developer-rag")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("C:/Projects/AI Learning/Simple_RAG/developer-rag/.env", override=False)
from openai import OpenAI  # noqa: E402

key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROK_API_KEY")
client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1", timeout=30.0, max_retries=0)
out = {}
for model in ("openai/gpt-oss-120b", "openai/gpt-oss-20b"):
    try:
        raw = client.chat.completions.with_raw_response.create(model=model, messages=[{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=16)
        h = {k: v for k, v in raw.headers.items() if k.lower().startswith(("x-ratelimit", "retry-after"))}
        out[model] = {"status": 200, "headers": h}
    except Exception as exc:
        resp = getattr(exc, "response", None)
        h = {k: v for k, v in (resp.headers.items() if resp is not None else []) if k.lower().startswith(("x-ratelimit", "retry-after"))}
        body = getattr(exc, "body", None)
        out[model] = {"status": getattr(exc, "status_code", None), "error_class": type(exc).__name__, "message": (body or {}).get("message") if isinstance(body, dict) else str(exc)[:300], "headers": h}
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            out[model]["message"] = body["error"].get("message")
print(json.dumps(out, indent=1))
