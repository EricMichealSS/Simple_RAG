"""Ingest the 3 Week 7 reference PDFs into their own ChromaDB collection.

Separate from the Week 5/6 `developer_docs` collection entirely (different
domain — GitHub multi-version + Swagger Codegen v2/v3 migration docs). Uses
the same chunker (`chunkers.chunk_structured`) and embedding model as the
main app for consistency, but hand-authored per-page metadata (see
page_metadata.json) rather than auto-detected version/endpoint tags, since
there are only 24 pages total and getting the version/endpoint tagging
wrong would silently break both get_openapi_spec and check_deprecation.
"""

import json
import re
import sys
from pathlib import Path

import chromadb
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).parent.parent))
from chunkers import chunk_structured, anchor_for_section  # noqa: E402

RACE_DIR = Path(__file__).parent
DOCS_DIR = RACE_DIR / "documents"
CHROMA_PATH = str(Path(__file__).parent.parent / "data" / "chroma")
COLLECTION_NAME = "race_docs"

PAGE_META = json.loads((RACE_DIR / "page_metadata.json").read_text(encoding="utf-8"))

embedding_model = SentenceTransformer("BAAI/bge-small-en-v1.5")


def extract_pdf_pages(filepath):
    reader = PdfReader(filepath)
    pages = []
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if text and text.strip():
            pages.append((page_num, text.strip()))
    return pages


def page_id_for(filename):
    return re.sub(r"\.pdf$", "", filename)


# Exact heading text each doc family uses to introduce the version-diff
# paragraph -- confirmed present verbatim on every has_diff page by a full
# manual read of all 24 pages. Splitting on these separates "current spec"
# text from "what changed" text so get_openapi_spec and check_deprecation
# return genuinely different content instead of the same whole page twice.
DIFF_MARKERS = ["Previous version (", "Compared to v2"]


def _split_spec_and_diff(page_text: str):
    """Return (spec_text, diff_text). diff_text is '' if no marker found."""
    for marker in DIFF_MARKERS:
        idx = page_text.find(marker)
        if idx != -1:
            return page_text[:idx].strip(), page_text[idx:].strip()
    return page_text.strip(), ""


def build_chunks(filename, pages):
    page_id = page_id_for(filename)
    meta_for_file = PAGE_META[filename]
    chunks = []
    chunk_index = 0

    for page_num, page_text in pages:
        page_meta = meta_for_file.get(str(page_num), {})
        has_diff = page_meta.get("has_diff", False)

        if has_diff:
            spec_text, diff_text = _split_spec_and_diff(page_text)
            groups = [("spec", spec_text)]
            if diff_text:
                groups.append(("diff", diff_text))
            else:
                # Marker expected but not found -- fail loudly rather than
                # silently making check_deprecation return nothing for this page.
                raise ValueError(f"{filename} p{page_num}: has_diff=True but no diff marker found in text")
        else:
            groups = [("spec", page_text)]

        for content_type, group_text in groups:
            structured = chunk_structured(group_text, normalize=True)
            for struct_chunk in structured:
                section = struct_chunk.get("section") or ""
                if len(section) > 200:
                    section = section[:200]
                anchor = anchor_for_section(struct_chunk.get("section"))

                chunks.append({
                    "id": f"{page_id}:p{page_num}:{content_type}:{anchor}:{chunk_index}",
                    "text": struct_chunk["text"],
                    "metadata": {
                        "source_file": filename,
                        "page": page_num,
                        "anchor": anchor,
                        "content_type": content_type,
                        "api_version": page_meta.get("api_version", "unknown"),
                        "endpoint": page_meta.get("endpoint", "unknown"),
                        "has_diff": (content_type == "diff"),
                    },
                })
                chunk_index += 1
    return chunks


def main():
    print("Removing any existing race_docs collection...")
    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )

    all_chunks = []
    for filename in sorted(PAGE_META.keys()):
        if filename.startswith("_") or filename.startswith("known_"):
            continue
        filepath = DOCS_DIR / filename
        pages = extract_pdf_pages(filepath)
        chunks = build_chunks(filename, pages)
        print(f"  {filename}: {len(pages)} pages -> {len(chunks)} chunks")
        all_chunks.extend(chunks)

    ids = [c["id"] for c in all_chunks]
    documents = [c["text"] for c in all_chunks]
    metadatas = [c["metadata"] for c in all_chunks]

    print(f"\nEmbedding {len(all_chunks)} chunks...")
    embeddings = embedding_model.encode(documents, batch_size=32, normalize_embeddings=True, show_progress_bar=True).tolist()

    collection.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
    print(f"\nDone. {collection.count()} chunks in '{COLLECTION_NAME}' at {CHROMA_PATH}")


if __name__ == "__main__":
    main()
