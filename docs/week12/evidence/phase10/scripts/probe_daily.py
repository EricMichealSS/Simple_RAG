"""One request of realistic size (~1.7k prompt tokens, max_tokens 16) on the ANSWER model, to see which limit rejects it. Prints status + the provider's message, never the key."""
import json
import os

from dotenv import load_dotenv

load_dotenv("C:/Projects/AI Learning/Simple_RAG/developer-rag/.env", override=False)
from openai import OpenAI  # noqa: E402

client = OpenAI(api_key=os.environ.get("GROQ_API_KEY") or os.environ.get("GROK_API_KEY"), base_url="https://api.groq.com/openai/v1", timeout=45.0, max_retries=0)
filler = ("The Swagger Codegen reference documents options for generators, configuration files and command line flags. " * 80)
out = []
for model, size in (("openai/gpt-oss-120b", 1), ("openai/gpt-oss-120b", 1)):
    try:
        raw = client.chat.completions.with_raw_response.create(model=model, messages=[{"role": "user", "content": filler + "\nReply with the single word: ok"}], max_tokens=16)
        r = raw.parse()
        out.append({"model": model, "status": 200, "usage": [r.usage.prompt_tokens, r.usage.completion_tokens],
                    "remaining_tokens_header": raw.headers.get("x-ratelimit-remaining-tokens"), "remaining_requests_header": raw.headers.get("x-ratelimit-remaining-requests")})
    except Exception as exc:
        body = getattr(exc, "body", None)
        msg = body.get("error", {}).get("message") if isinstance(body, dict) and isinstance(body.get("error"), dict) else str(exc)[:300]
        resp = getattr(exc, "response", None)
        out.append({"model": model, "status": getattr(exc, "status_code", None), "message": msg,
                    "retry_after": resp.headers.get("retry-after") if resp is not None else None})
print(json.dumps(out, indent=1))
