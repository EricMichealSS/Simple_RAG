import os
import sys


def pytest_configure(config):
    m = os.environ.get("MUTATION", "")
    sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
    import sdk_engine.versioning as V
    if m == "any_product_counts":       # the original: any known version label, of any product, is a claim
        orig = V.VersionCatalog.product_of
        V.VersionCatalog.product_of = lambda self, version: None if False else None
    print(f"\n*** MUTATION APPLIED: {m or 'none'} ***")
