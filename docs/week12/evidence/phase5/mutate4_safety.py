import os
import sys


def pytest_configure(config):
    m = os.environ.get("MUTATION", "")
    sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
    import sdk_engine.answering as A
    import sdk_engine.injection as I
    if m == "safety_checks_off":
        A.output_safety_failures = lambda *a, **k: []
    if m == "sources_are_trusted_for_hosts":      # re-introduce the exact bug found while writing the adversarial tests
        orig = A.output_safety_failures

        def lenient(answer, *, passages, question, system_prompt, canary="", secret_values=(), allowed_hosts=(), strict_urls=True):
            hosts = set(allowed_hosts)
            for p in passages:
                hosts |= {mm.group(1).lower() for mm in A._URL_RE.finditer(p.text)}
            return orig(answer, passages=passages, question=question, system_prompt=system_prompt, canary=canary,
                        secret_values=secret_values, allowed_hosts=tuple(hosts), strict_urls=False)
        A.output_safety_failures = lenient
    if m == "no_invisible_stripping":
        A.strip_invisible = lambda t: (t, 0)
    if m == "scanner_off":
        I.scan_text = lambda t: []
    if m == "canary_not_in_prompt":
        A._canary_line = lambda c: ""
    if m == "images_allowed_from_sources":
        A._AUTOFETCH_RE = A.re.compile(r"(?!x)x")        # never matches: auto-fetch constructs are not checked
    print(f"\n*** MUTATION APPLIED: {m or 'none'} ***")
