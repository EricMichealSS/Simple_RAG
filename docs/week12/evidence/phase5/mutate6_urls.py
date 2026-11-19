import os
import re
import sys


def pytest_configure(config):
    m = os.environ.get("MUTATION", "")
    sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
    import sdk_engine.answering as A
    if m == "host_grammar_off":          # back to "suffix-match whatever the authority text is"
        A._PLAIN_HOST_RE = re.compile(r".+", re.S)
        A._PLAIN_URL_RE = re.compile(r"https?://([^/?#]+)(?:[/?#].*)?", re.S | re.I)
    if m == "network_path_off":          # //host, \\host, https:host in a markdown link destination are not flagged
        A._NETWORK_PATH_RE = re.compile(r"(?!x)x")
    if m == "old_autofetch_regex":       # the original: only absolute https?:// destinations are even looked at
        A._AUTOFETCH_RE = re.compile(
            r"!\[[^\]]*\]\(\s*(https?://[^)\s]+)"
            r"|<\s*(?:img|iframe|script|embed|object|video|audio|link|source)\b[^>]*?\b(?:src|href|data)\s*=\s*[\"']?(https?://[^\"'\s>]+)",
            re.IGNORECASE)
    print(f"\n*** MUTATION APPLIED: {m or 'none'} ***")
