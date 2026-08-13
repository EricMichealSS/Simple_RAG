import os

from dotenv import load_dotenv


load_dotenv()


GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY"
)


if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY is missing. "
        "Add it to your .env file."
    )


EMBEDDING_MODEL = "gemini-embedding-001"

GENERATION_MODEL = "gemini-3.6-flash"


CHROMA_PATH = "./data/chroma"

COLLECTION_NAME = "developer_docs"


DOCUMENTS_DIR = "./documents"


CHUNK_SIZE = 500

CHUNK_OVERLAP = 100

TOP_K = 5


# This will be tuned experimentally.
# Do not assume this value is optimal.
RELEVANCE_THRESHOLD = 0.60