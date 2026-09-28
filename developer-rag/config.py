import os

from dotenv import load_dotenv


load_dotenv()


GROK_API_KEY = os.getenv("GROK_API_KEY")

if not GROK_API_KEY:
    raise ValueError(
        "GROK_API_KEY is missing. "
        "Add it to your .env file."
    )


EMBEDDING_MODEL = "gemini-embedding-001"

# Groq model (OpenAI-compatible API)
GENERATION_MODEL = "groq/compound"


CHROMA_PATH = "./data/chroma"

COLLECTION_NAME = "developer_docs"


DOCUMENTS_DIR = "./documents"

# Week 3 / Task Set E additions ----------------------------------------

# The 6 new v3 SDK reference pages (markdown).
V3_DOCS_DIR = "./documents/v3-sdk"

V3_SDK_VERSION = "v3"

# Structure-aware chunker target size. Larger than the fixed window so a
# parameter table plus its section header fit in one chunk.
STRUCTURED_CHUNK_SIZE = 1200

# Benchmark collections: same 6 pages, one chunking strategy each.
BENCHMARK_COLLECTION_CURRENT = "v3_benchmark_current"

BENCHMARK_COLLECTION_STRUCTURED = "v3_benchmark_structured"


# Improved chunking settings
CHUNK_SIZE = 800          # Increased for better context
CHUNK_OVERLAP = 150       # Increased overlap

TOP_K = 5


# Metadata defaults for existing corpus
DEFAULT_SDK_VERSION = "v2"
DEFAULT_PAGE_TYPE = "guide"

# Page type mapping for existing PDFs
PAGE_TYPE_MAP = {
    "getting-started.pdf": "guide",
    "authentication.pdf": "guide",
    "rate-limits.pdf": "guide",
    "using-rest-api.pdf": "guide",
    "issues.pdf": "reference",
}


# This will be tuned experimentally.
# Do not assume this value is optimal.
RELEVANCE_THRESHOLD = 0.60