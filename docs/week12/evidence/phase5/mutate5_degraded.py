import os
import sys


def pytest_configure(config):
    m = os.environ.get("MUTATION", "")
    sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
    import sdk_engine.answering as A
    if m == "pass_everything":          # degraded mode prints retrieved text unchecked (the original defect)
        A.safe_excerpts = lambda passages, *, limit=2, **k: (list(passages)[:limit], [])
    if m == "check_only_300_chars":     # check the short line but not the 380-char citation excerpt
        orig = A.output_safety_failures

        def short(answer, **k):
            return orig(answer[:300], **k)
        A.output_safety_failures = short
    if m == "ignore_flags":             # a flagged passage gets the user's-own-link allowance
        orig2 = A.safe_excerpts
        A.safe_excerpts = lambda passages, *, question, allowed_hosts, strict_urls, secret_values=(), limit=2: orig2(
            [p.model_copy(update={"injection_flags": []}) for p in passages], question=question, allowed_hosts=allowed_hosts,
            strict_urls=strict_urls, secret_values=secret_values, limit=limit)
    print(f"\n*** MUTATION APPLIED: {m or 'none'} ***")
