"""Re-ingest existing PDFs with structure-aware chunking + full metadata.

Run this once to rebuild the index with improved chunking and metadata.
"""

import os
import re
import shutil

import chromadb

from pypdf import PdfReader

from sentence_transformers import SentenceTransformer

from chunkers import chunk_structured, anchor_for_section

from config import (
    CHROMA_PATH,
    COLLECTION_NAME,
    DOCUMENTS_DIR,
    DEFAULT_SDK_VERSION,
    DEFAULT_PAGE_TYPE,
    PAGE_TYPE_MAP,
    EMBEDDING_MODEL,
)

embedding_model = SentenceTransformer("BAAI/bge-small-en-v1.5")


def page_id_for(filename):
    return re.sub(r"\.pdf$", "", filename)


def extract_pdf_text(filepath):
    """Extract text from PDF, page by page."""
    reader = PdfReader(filepath)
    pages = []
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if text and text.strip():
            pages.append((page_num, text.strip()))
    return pages


def build_chunks(filename, pages):
    """Build chunks from PDF pages using structure-aware chunking."""
    page_id = page_id_for(filename)
    page_type = PAGE_TYPE_MAP.get(filename, DEFAULT_PAGE_TYPE)

    chunks = []
    chunk_index = 0

    for page_num, page_text in pages:
        # Structure-aware chunking
        structured_chunks = chunk_structured(page_text, normalize=True)

        for struct_chunk in structured_chunks:
            anchor = anchor_for_section(struct_chunk.get("section"))

            section = struct_chunk.get("section", "") or ""
            # Ensure section is a valid string for ChromaDB
            if not isinstance(section, str):
                section = str(section)
            # Truncate if too long
            if len(section) > 200:
                section = section[:200]

            chunks.append({
                "id": f"{page_id}:p{page_num}:{anchor}:{chunk_index}",
                "text": struct_chunk["text"],
                "metadata": {
                    "source_file": str(filename),
                    "page_id": str(page_id),
                    "sdk_version": str(DEFAULT_SDK_VERSION),
                    "page_type": str(page_type),
                    "section": section,
                    "anchor": str(anchor),
                    "source": str(filename),
                    "page": int(page_num),
                    "chunk": int(chunk_index),
                },
            })
            chunk_index += 1

    return chunks


def validate_metadata(chunks):
    for chunk in chunks:
        if not chunk["metadata"].get("source_file"):
            raise ValueError(f"FAILED INGEST: chunk {chunk['id']} has no source_file")
    return True


def embed(texts):
    embeddings = embedding_model.encode(
        texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    return embeddings.tolist()


def main():
    print("=" * 70)
    print("RE-INGESTING EXISTING PDFs WITH STRUCTURE-AWARE CHUNKING")
    print("=" * 70)

    # Remove old database
    if os.path.exists(CHROMA_PATH):
        print("\nRemoving old ChromaDB...")
        shutil.rmtree(CHROMA_PATH)

    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    files = sorted([f for f in os.listdir(DOCUMENTS_DIR) if f.lower().endswith(".pdf")])

    all_chunks = []

    for filename in files:
        print(f"\nProcessing: {filename}")
        filepath = os.path.join(DOCUMENTS_DIR, filename)
        pages = extract_pdf_text(filepath)
        print(f"  Extracted {len(pages)} pages")

        chunks = build_chunks(filename, pages)
        print(f"  Created {len(chunks)} structure-aware chunks")
        all_chunks.extend(chunks)

    print(f"\nTotal chunks: {len(all_chunks)}")

    validate_metadata(all_chunks)

    # Batch embed and insert
    ids = [c["id"] for c in all_chunks]
    documents = [c["text"] for c in all_chunks]
    metadatas = [c["metadata"] for c in all_chunks]

    print("\nCreating embeddings...")
    embeddings = embed(documents)

    print("Inserting into ChromaDB...")
    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    print("\n" + "=" * 70)
    print("INGESTION COMPLETE")
    print("=" * 70)
    print(f"Documents: {len(files)}")
    print(f"Chunks: {len(all_chunks)}")
    print(f"Database: {CHROMA_PATH}")

    # Show sample
    sample = collection.get(limit=3, include=["metadatas"])
    for cid, meta in zip(sample["ids"], sample["metadatas"]):
        print(f"  {cid} | sdk_version={meta.get('sdk_version')} | page_type={meta.get('page_type')} | anchor={meta.get('anchor')}")


if __name__ == "__main__":
    main()