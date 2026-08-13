import chromadb

from google import genai
from sentence_transformers import SentenceTransformer

from config import (
    GEMINI_API_KEY,
    GENERATION_MODEL,
    CHROMA_PATH,
    COLLECTION_NAME,
    TOP_K,
    RELEVANCE_THRESHOLD,
)


# ============================================================
# Gemini client
# Used ONLY for answer generation
# ============================================================

gemini_client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# Local embedding model
# Used for BOTH documents and questions
# ============================================================

embedding_model = SentenceTransformer(
    "BAAI/bge-small-en-v1.5"
)


# ============================================================
# ChromaDB
# ============================================================

chroma_client = chromadb.PersistentClient(
    path=CHROMA_PATH
)

collection = chroma_client.get_collection(
    name=COLLECTION_NAME
)


# ============================================================
# RETRIEVAL
# ============================================================

def retrieve(
    question,
    top_k=TOP_K
):
    """
    Convert the question into a local BGE embedding
    and retrieve the most similar document chunks.
    """

    # IMPORTANT:
    # Use the SAME embedding model used during ingestion.
    query_embedding = embedding_model.encode(
        [question],
        normalize_embeddings=True
    )[0].tolist()

    results = collection.query(
        query_embeddings=[
            query_embedding
        ],
        n_results=top_k,
        include=[
            "documents",
            "metadatas",
            "distances"
        ]
    )

    retrieved = []

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for document, metadata, distance in zip(
        documents,
        metadatas,
        distances
    ):
        retrieved.append(
            {
                "text": document,
                "source": metadata["source"],
                "page": metadata["page"],
                "chunk": metadata["chunk"],
                "distance": distance,
            }
        )

    return retrieved


# ============================================================
# RELEVANCE CHECK
# ============================================================

def is_relevant(results):

    if not results:
        return False

    best_distance = results[0]["distance"]

    print(
        f"Best retrieval distance: {best_distance:.4f}"
    )

    return (
        best_distance
        <= RELEVANCE_THRESHOLD
    )


# ============================================================
# BUILD CONTEXT
# ============================================================

def build_context(results):

    context_parts = []

    for index, result in enumerate(
        results,
        start=1
    ):

        context_parts.append(
            f"""
SOURCE {index}

Document:
{result['source']}

Page:
{result['page']}

Content:
{result['text']}
"""
        )

    return "\n".join(
        context_parts
    )


# ============================================================
# GENERATE ANSWER
# ============================================================

def generate_answer(
    question,
    results
):

    if not is_relevant(results):

        return (
            "I don't know based on "
            "the provided documents."
        )

    context = build_context(
        results
    )

    prompt = f"""
You are a developer documentation
question-answering assistant.

Your ONLY source of truth is the
documentation supplied in CONTEXT.

STRICT RULES:

1. Answer ONLY from the supplied context.

2. Do not use your general knowledge.

3. Do not guess.

4. Do not invent API endpoints.

5. Do not invent parameters.

6. Do not invent configuration values.

7. Do not invent code examples.

8. If the answer cannot be supported
   by the supplied context, respond:

"I don't know based on the provided documents."

9. Keep the answer concise and technically
   accurate.

10. Always mention the source document
    and page used for the answer.

DOCUMENTATION CONTEXT:

{context}

USER QUESTION:

{question}

ANSWER:
"""

    response = (
        gemini_client
        .models
        .generate_content(
            model=GENERATION_MODEL,
            contents=prompt
        )
    )

    return response.text