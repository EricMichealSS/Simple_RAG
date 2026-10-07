# Void first attempt (kept as evidence, not used in any result)

Race attempt 1 (gpt-oss-120b) was invalid for two infrastructure reasons, found only after it ran:
1. Groq free tier caps gpt-oss-120b at 200,000 tokens/day (and 8,000/min). The attempt used ~228k tokens and 29 of 60 runs ended as HTTP 429 `infra_error` with no answer.
2. Squad-only bug in MY code: the manager tool schema had optional enum fields, and the model sent `endpoint: null`, which the provider rejected with HTTP 400 (2 runs). That would have biased the race against the squad.
No verdict was drawn from these runs. Fixes: rate-limit waits retried and excluded from latency, optional enums removed, resumable runner, quota-aware model choice (see PHASE4 doc).
