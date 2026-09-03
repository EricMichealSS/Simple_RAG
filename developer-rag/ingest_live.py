"""Live ingestion for UI file uploads — adds docs to existing ChromaDB collection."""

import io
import re

import chromadb
from pypdf import PdfReader

from chunkers import chunk_structured, anchor_for_section
from config import CHROMA_PATH, COLLECTION_NAME


def _page_id_for(filename: str) -> str:
    return re.sub(r"\.(pdf|md)$", "", filename)


def _process_pdf(file_bytes: bytes, filename: str, sdk_version: str, page_type: str):
    reader = PdfReader(io.BytesIO(file_bytes))
    page_id = _page_id_for(filename)
    chunks = []
    chunk_index = 0
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if not text or not text.strip():
            continue
        for sc in chunk_structured(text.strip(), normalize=True):
            section = str(sc.get("section", "") or "")[:200]
            anchor = anchor_for_section(sc.get("section"))
            chunks.append({
                "id": f"{page_id}:p{page_num}:{anchor}:{chunk_index}",
                "text": sc["text"],
                "metadata": {
                    "source_file": filename,
                    "page_id": page_id,
                    "sdk_version": sdk_version,
                    "page_type": page_type,
                    "section": section,
                    "anchor": anchor,
                    "source": filename,
                    "page": page_num,
                    "chunk": chunk_index,
                },
            })
            chunk_index += 1
    return chunks


def _process_md(file_bytes: bytes, filename: str, sdk_version: str, page_type: str):
    text = file_bytes.decode("utf-8")
    page_id = _page_id_for(filename)
    chunks = []
    for chunk_index, sc in enumerate(chunk_structured(text, normalize=True)):
        section = str(sc.get("section", "") or "")[:200]
        anchor = anchor_for_section(sc.get("section"))
        chunks.append({
            "id": f"{page_id}:{anchor}:{chunk_index}",
            "text": sc["text"],
            "metadata": {
                "source_file": filename,
                "page_id": page_id,
                "sdk_version": sdk_version,
                "page_type": page_type,
                "section": section,
                "anchor": anchor,
                "source": filename,
                "page": 1,
                "chunk": chunk_index,
            },
        })
    return chunks


def ingest_uploaded_file(
    file_bytes: bytes,
    filename: str,
    sdk_version: str = "uploaded",
    page_type: str = "uploaded",
    embedding_model=None,
):
    """
    Chunk, embed, and insert an uploaded file into the existing ChromaDB collection.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        filename:   Original filename (used to detect .pdf / .md and to build chunk IDs).
        sdk_version: Metadata tag — "v2" or "v3".
        page_type:   Metadata tag — "guide" or "reference".
        embedding_model: SentenceTransformer instance (pass the already-loaded one to
                         avoid loading a second copy of the model).

    Returns:
        (chunks_added: int, chunks_skipped: int)
    """
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        chunks = _process_pdf(file_bytes, filename, sdk_version, page_type)
    elif ext == "md":
        chunks = _process_md(file_bytes, filename, sdk_version, page_type)
    else:
        raise ValueError(f"Unsupported file type: .{ext}  (only .pdf and .md are accepted)")

    if not chunks:
        return 0, 0

    # Connect to persistent store and get (or create) the collection
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    coll = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # Avoid inserting duplicates — check which IDs already exist
    all_ids = [c["id"] for c in chunks]
    existing_ids = set(coll.get(ids=all_ids)["ids"])
    new_chunks = [c for c in chunks if c["id"] not in existing_ids]
    skipped = len(chunks) - len(new_chunks)

    if not new_chunks:
        return 0, skipped

    # Use the caller-supplied model (already loaded in rag_hybrid) or fall back to loading one
    if embedding_model is None:
        from sentence_transformers import SentenceTransformer
        embedding_model = SentenceTransformer("BAAI/bge-small-en-v1.5")

    texts = [c["text"] for c in new_chunks]
    embeddings = embedding_model.encode(
        texts,
        batch_size=16,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()

    coll.add(
        ids=[c["id"] for c in new_chunks],
        documents=texts,
        embeddings=embeddings,
        metadatas=[c["metadata"] for c in new_chunks],
    )

    return len(new_chunks), skipped
