import os
import shutil

import chromadb

from pypdf import PdfReader

from google import genai
from google.genai import types
from sentence_transformers import SentenceTransformer

from chunkers import chunk_text

from config import (
    GEMINI_API_KEY,
    EMBEDDING_MODEL,
    CHROMA_PATH,
    COLLECTION_NAME,
    DOCUMENTS_DIR,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)

embedding_model = SentenceTransformer(
    "BAAI/bge-small-en-v1.5"
)

client = genai.Client(
    api_key=GEMINI_API_KEY
)


def load_documents():

    all_chunks = []

    files = sorted(
        os.listdir(
            DOCUMENTS_DIR
        )
    )


    for filename in files:

        if not filename.lower().endswith(
            ".pdf"
        ):
            continue


        filepath = os.path.join(
            DOCUMENTS_DIR,
            filename
        )


        print(
            f"Reading: {filename}"
        )


        reader = PdfReader(
            filepath
        )


        for page_number, page in enumerate(
            reader.pages,
            start=1
        ):

            text = page.extract_text()


            if not text:
                continue


            text = text.strip()


            chunks = chunk_text(
                text
            )


            for chunk_number, chunk in enumerate(
                chunks
            ):

                all_chunks.append(
                    {
                        "text": chunk,
                        "source": filename,
                        "page": page_number,
                        "chunk": chunk_number,
                    }
                )


    return all_chunks


def create_embeddings(texts):
    """Create embeddings locally using BGE."""

    print(f"Creating local embeddings for {len(texts)} chunks...")

    embeddings = embedding_model.encode(
        texts,
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    return embeddings.tolist()


def create_database():

    documents = load_documents()


    if not documents:

        raise ValueError(
            "No document chunks were created."
        )


    print(
        f"\nTotal chunks: "
        f"{len(documents)}"
    )


    # Remove previous database.
    if os.path.exists(CHROMA_PATH):

        print(
            "\nRemoving old ChromaDB..."
        )

        shutil.rmtree(
            CHROMA_PATH
        )


    chroma_client = chromadb.PersistentClient(
        path=CHROMA_PATH
    )


    collection = (
    chroma_client
    .get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "hnsw:space": "cosine"
        }
    )
)


    texts = [
        doc["text"]
        for doc in documents
    ]


    embeddings = create_embeddings(
        texts
    )


    ids = [
        f"chunk_{index}"
        for index in range(
            len(documents)
        )
    ]


    metadatas = [
        {
            "source": doc["source"],
            "page": doc["page"],
            "chunk": doc["chunk"],
        }
        for doc in documents
    ]


    collection.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas
    )


    print(
        "\n================================"
    )

    print(
        "INGESTION COMPLETE"
    )

    print(
        "================================"
    )

    print(
        "Documents:",
        len(
            set(
                doc["source"]
                for doc in documents
            )
        )
    )

    print(
        "Chunks:",
        len(documents)
    )

    print(
        "Database:",
        CHROMA_PATH
    )


if __name__ == "__main__":
    create_database()