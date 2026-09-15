"""Retrieval module — Structure-aware RAG with metadata filters."""

import chromadb

from sentence_transformers import SentenceTransformer

from config import (
    CHROMA_PATH,
    COLLECTION_NAME,
    TOP_K,
    RELEVANCE_THRESHOLD,
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
    top_k=TOP_K,
    collection_name=COLLECTION_NAME,
    where=None,
):
    """
    Convert the question into a local BGE embedding
    and retrieve the most similar document chunks.

    ``collection_name`` selects which index to search
    (e.g. the benchmark collections) and ``where`` applies
    a ChromaDB metadata filter (e.g. {"page_type": "reference"}).
    """

    target = (
        collection
        if collection_name == COLLECTION_NAME
        else chroma_client.get_collection(
            name=collection_name
        )
    )

    # IMPORTANT:
    # Use the SAME embedding model used during ingestion.
    query_embedding = embedding_model.encode(
        [question],
        normalize_embeddings=True
    )[0].tolist()

    query_kwargs = {
        "query_embeddings": [
            query_embedding
        ],
        "n_results": top_k,
        "include": [
            "documents",
            "metadatas",
            "distances"
        ],
    }

    if where:
        query_kwargs["where"] = where

    results = target.query(**query_kwargs)

    retrieved = []

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]
    ids = results["ids"][0]

    for chunk_id, document, metadata, distance in zip(
        ids,
        documents,
        metadatas,
        distances
    ):
        retrieved.append(
            {
                "chunk_id": chunk_id,
                "text": document,
                "source": metadata.get("source", ""),
                "source_file": metadata.get("source_file", ""),
                "page_id": metadata.get("page_id", ""),
                "page": metadata.get("page", 1),
                "chunk": metadata.get("chunk", 0),
                "sdk_version": metadata.get("sdk_version", ""),
                "page_type": metadata.get("page_type", ""),
                "anchor": metadata.get("anchor", ""),
                "section": metadata.get("section", ""),
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
# BUILD CONTEXT (legacy, used by old generate_answer)
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