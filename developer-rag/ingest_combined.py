"""Re-ingest ALL docs: v2 PDFs + v3 SDK markdown with structure-aware chunking + full metadata."""

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
    V3_DOCS_DIR,
    V3_SDK_VERSION,
    DEFAULT_SDK_VERSION,
    DEFAULT_PAGE_TYPE,
    PAGE_TYPE_MAP,
)

embedding_model = SentenceTransformer("BAAI/bge-small-en-v1.5")


def page_id_for(filename):
    return re.sub(r"\.(pdf|md)$", "", filename)


def extract_pdf_pages(filepath):
    """Extract text from PDF, page by page."""
    reader = PdfReader(filepath)
    pages = []
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if text and text.strip():
            pages.append((page_num, text.strip()))
    return pages


def extract_md_text(filepath):
    """Extract full text from markdown file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def build_chunks_from_pdf(filename, pages):
    """Build chunks from PDF pages using structure-aware chunking."""
    page_id = page_id_for(filename)
    page_type = PAGE_TYPE_MAP.get(filename, DEFAULT_PAGE_TYPE)

    chunks = []
    chunk_index = 0

    for page_num, page_text in pages:
        structured_chunks = chunk_structured(page_text, normalize=True)

        for struct_chunk in structured_chunks:
            section = struct_chunk.get("section", "") or ""
            if not isinstance(section, str):
                section = str(section)
            if len(section) > 200:
                section = section[:200]

            anchor = anchor_for_section(struct_chunk.get("section"))

            chunks.append({
                "id": f"{page_id}:p{page_num}:{anchor}:{chunk_index}",
                "text": struct_chunk["text"],
                "metadata": {
                    "source_file": filename,
                    "page_id": page_id,
                    "sdk_version": DEFAULT_SDK_VERSION,
                    "page_type": page_type,
                    "section": section,
                    "anchor": anchor,
                    "source": filename,
                    "page": int(page_num),
                    "chunk": int(chunk_index),
                },
            })
            chunk_index += 1

    return chunks


def build_chunks_from_md(filename, text):
    """Build chunks from markdown using structure-aware chunking."""
    page_id = page_id_for(filename)

    structured_chunks = chunk_structured(text, normalize=True)

    chunks = []
    for chunk_index, struct_chunk in enumerate(structured_chunks):
        section = struct_chunk.get("section", "") or ""
        if not isinstance(section, str):
            section = str(section)
        if len(section) > 200:
            section = section[:200]

        anchor = anchor_for_section(struct_chunk.get("section"))

        chunks.append({
            "id": f"{page_id}:{anchor}:{chunk_index}",
            "text": struct_chunk["text"],
            "metadata": {
                "source_file": filename,
                "page_id": page_id,
                "sdk_version": V3_SDK_VERSION,
                "page_type": "reference",
                "section": section,
                "anchor": anchor,
                "source": filename,
                "page": 1,
                "chunk": chunk_index,
            },
        })

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
    print("RE-INGESTING: v2 PDFs + v3 SDK MARKDOWN")
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

    all_chunks = []

    # --- Process v2 PDFs ---
    pdf_files = sorted([f for f in os.listdir(DOCUMENTS_DIR) if f.lower().endswith(".pdf")])
    print(f"\nProcessing {len(pdf_files)} v2 PDFs...")

    for filename in pdf_files:
        print(f"  {filename}")
        filepath = os.path.join(DOCUMENTS_DIR, filename)
        pages = extract_pdf_pages(filepath)
        chunks = build_chunks_from_pdf(filename, pages)
        print(f"    {len(chunks)} chunks")
        all_chunks.extend(chunks)

    # --- Process v3 markdown ---
    md_files = sorted([f for f in os.listdir(V3_DOCS_DIR) if f.lower().endswith(".md")])
    print(f"\nProcessing {len(md_files)} v3 markdown pages...")

    for filename in md_files:
        print(f"  {filename}")
        filepath = os.path.join(V3_DOCS_DIR, filename)
        text = extract_md_text(filepath)
        chunks = build_chunks_from_md(filename, text)
        print(f"    {len(chunks)} chunks")
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

    # Show stats
    v2_count = sum(1 for c in all_chunks if c["metadata"]["sdk_version"] == "v2")
    v3_count = sum(1 for c in all_chunks if c["metadata"]["sdk_version"] == "v3")
    print(f"Total chunks: {len(all_chunks)} (v2: {v2_count}, v3: {v3_count})")
    print(f"Database: {CHROMA_PATH}")

    # Show sample
    sample = collection.get(limit=5, include=["metadatas"])
    for cid, meta in zip(sample["ids"], sample["metadatas"]):
        print(f"  {cid} | sdk={meta.get('sdk_version')} type={meta.get('page_type')} anchor={meta.get('anchor')}")


if __name__ == "__main__":
    main()