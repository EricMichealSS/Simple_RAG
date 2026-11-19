import os
import re
import sys


def pytest_configure(config):
    m = os.environ.get("MUTATION", "")
    sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
    import sdk_engine.codecheck as C
    if m == "physical_lines":           # the original behaviour: split by physical line, quotes ignored
        C._logical_lines = lambda text: re.sub(r"\\[ \t]*\r?\n", " ", text).splitlines()
    print(f"\n*** MUTATION APPLIED: {m or 'none'} ***")
