"""Streamlit app — Developer Docs Q&A with Structure-Aware Chunking & Metadata Filters."""

import streamlit as st

from rag_hybrid import (
    retrieve,
    is_relevant,
)
from config import (
    GROK_API_KEY,
    GENERATION_MODEL,
    COLLECTION_NAME,
)
from openai import OpenAI


st.set_page_config(
    page_title="Ask My Developer Docs",
    page_icon="📚",
    layout="wide",
)


@st.cache_resource
def get_grok_client():
    return OpenAI(
        api_key=GROK_API_KEY,
        base_url="https://api.groq.com/openai/v1",
        timeout=60.0,
    )


grok_client = get_grok_client()


# ============================================================
# Cited answer generation with error handling
# ============================================================

def generate_cited_answer(question, results):
    """Generate answer with [chunk_id] citations per claim.
    Falls back to retrieval-only display on error.
    """

    if not results:
        return "I don't know based on the provided documents."

    if not is_relevant(results):
        return "I don't know based on the provided documents."

    # Limit context size to avoid 413 Request Entity Too Large (Groq compound model)
    MAX_CHUNK_CHARS = 1200  # Full chunk fits
    MAX_TOTAL_CHARS = 2000  # 1-2 chunks

    context_parts = []
    total_chars = 0
    for i, result in enumerate(results, start=1):
        chunk_id = result.get("chunk_id", "unknown")
        source_file = result.get("source_file", result.get("source", "unknown"))
        anchor = result.get("anchor", "")
        sdk_version = result.get("sdk_version", "")
        page_type = result.get("page_type", "")
        
        text = result['text'][:MAX_CHUNK_CHARS]
        if total_chars + len(text) > MAX_TOTAL_CHARS:
            break
        total_chars += len(text)
        
        context_parts.append(
            f"SOURCE {i} [chunk_id: {chunk_id} | {source_file}#{anchor} | sdk_version={sdk_version} | type={page_type}]\n"
            f"{text}"
        )

    context = "\n\n".join(context_parts)

    prompt = f"""
You are a developer documentation question-answering assistant.

Your ONLY source of truth is the documentation supplied in CONTEXT.

STRICT RULES:
1. Answer ONLY from the supplied context.
2. Do not use your general knowledge.
3. Do not guess.
4. Do not invent API endpoints, parameters, or code examples.
5. If the answer cannot be supported by the supplied context, respond:
   "I don't know based on the provided documents."
6. For EVERY factual claim in your answer, cite the source chunk_id
   in square brackets, e.g. [rate-limits:p2:primary-rate-limits:2].
7. Keep the answer concise and technically accurate.

CONTEXT:
{context}

USER QUESTION:
{question}

ANSWER (with [chunk_id] citations):
"""

    try:
        response = grok_client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[
                {"role": "system", "content": "You are a developer documentation QA assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1024,
        )
        return response.choices[0].message.content
    except Exception as e:
        # Clean error fallback
        chunks_md = "\n\n---\n\n".join(
            f"**Source {i}** `[{r.get('chunk_id', 'unknown')}]` "
            f"({r.get('source_file', 'unknown')}#{r.get('anchor', '')})  \n"
            f"{r['text'][:300]}..."
            for i, r in enumerate(results, 1)
        )
        return (
            f"⚠️ **Generation unavailable ({type(e).__name__}).**\n\n"
            f"Error: {str(e)}\n\n"
            f"Here are the most relevant retrieved chunks — you can read the answer directly:\n\n"
            f"{chunks_md}"
        )


# ============================================================
# UI
# ============================================================

st.title("📚 Ask My Developer Docs")
st.caption("Powered by **structure-aware chunking** + **metadata filters** (page_type)")

st.write(
    "Ask questions about the GitHub REST API documentation. "
    "Answers are grounded only in the indexed documents with citations."
)

# --- Sidebar: Search Configuration ---
with st.sidebar:
    st.header("🔧 Search Settings")

    st.info("📚 Searching: **developer_docs** (structure-aware chunking, v2 + v3 metadata)")

    # Metadata filters
    st.subheader("Metadata Filters")
    col1, col2 = st.columns(2)
    with col1:
        sdk_version_filter = st.selectbox(
            "sdk_version",
            options=["All", "v2", "v3"],
            index=0,
            help="Filter by SDK version: v2 = GitHub REST API, v3 = OctoKit SDK"
        )
    with col2:
        page_type_filter = st.selectbox(
            "page_type",
            options=["All", "guide", "reference"],
            index=0,
            help="Filter by document type: 'guide' = tutorials/overviews, 'reference' = API endpoint listings"
        )

    # Build where filter
    where_filter = {}
    if sdk_version_filter != "All":
        where_filter["sdk_version"] = sdk_version_filter
    if page_type_filter != "All":
        where_filter["page_type"] = page_type_filter

    if not where_filter:
        where_filter = None

    top_k = st.slider(
        "Top-K chunks",
        min_value=1,
        max_value=10,
        value=5,
    )

    st.divider()
    st.caption(
        "**Chunking Strategy:** Structure-aware (splits on headers, "
        "keeps tables & code blocks whole). "
        "**Corpus:** 5 GitHub REST API PDFs (v2) + 6 OctoKit SDK v3 reference pages."
    )

    # Quick stats
    with st.expander("📊 Index Stats"):
        st.markdown("""
        - **Documents:** 5 PDFs (v2) + 6 Markdown (v3)
        - **Chunks:** 62 (37 v2 + 25 v3, structure-aware)
        - **Metadata per chunk:** 10 fields
        - **Filterable:** sdk_version (v2/v3), page_type (guide/reference)
        - **Embedding:** BGE-small-en-v1.5 (local)
        - **Generation:** Groq (compound)
        """)


# --- Main Input ---
question = st.text_input(
    "Ask a question",
    placeholder=(
        "Examples:\n"
        "• What is the primary rate limit for unauthenticated requests?\n"
        "• How do I authenticate with a personal access token?\n"
        "• What does a 401 error mean?\n"
        "• How does pagination work in the REST API?"
    ),
)

if st.button("🔍 Ask", type="primary", use_container_width=True):

    if not question.strip():
        st.warning("Please enter a question.")
        st.stop()

    with st.spinner("Searching documentation..."):
        try:
            results = retrieve(
                question,
                top_k=top_k,
                collection_name=COLLECTION_NAME,
                where=where_filter,
            )
        except Exception as e:
            st.error(f"❌ **Retrieval Error:** {type(e).__name__}: {str(e)}")
            st.stop()

    if not results:
        st.error("No results found. Try adjusting filters or rephrasing your question.")
        st.stop()

    # --- Answer ---
    st.subheader("Answer")
    with st.spinner("Generating answer..."):
        answer = generate_cited_answer(question, results)
    st.markdown(answer)

    # --- Sources with rich metadata ---
    st.subheader("Sources")

    for result in results:
        chunk_id = result.get("chunk_id", "unknown")
        source_file = result.get("source_file", result.get("source", "unknown"))
        page_id = result.get("page_id", "")
        anchor = result.get("anchor", "")
        sdk_version = result.get("sdk_version", "")
        page_type = result.get("page_type", "")
        distance = result.get("distance", 0)

        # Build a nice header
        meta_parts = []
        if sdk_version:
            meta_parts.append(f"`sdk_version={sdk_version}`")
        if page_type:
            meta_parts.append(f"`type={page_type}`")
        if page_id:
            meta_parts.append(f"`page_id={page_id}`")
        if anchor:
            meta_parts.append(f"`#{anchor}`")

        meta_str = " · ".join(meta_parts)

        with st.container(border=True):
            st.markdown(
                f"**📄 {source_file}**  \n"
                f"`chunk_id: {chunk_id}`  \n"
                f"{meta_str}  \n"
                f"Distance: `{distance:.4f}`"
            )

            # Show text preview
            with st.expander("Show chunk text"):
                st.code(result["text"], language="markdown")

    # --- Debug: Raw retrieved metadata ---
    with st.expander("🔧 Debug: Raw Retrieved Metadata"):
        for i, r in enumerate(results):
            st.json({
                "rank": i + 1,
                "chunk_id": r.get("chunk_id"),
                "source_file": r.get("source_file"),
                "source": r.get("source"),
                "page_id": r.get("page_id"),
                "page": r.get("page"),
                "chunk": r.get("chunk"),
                "sdk_version": r.get("sdk_version"),
                "page_type": r.get("page_type"),
                "anchor": r.get("anchor"),
                "section": r.get("section"),
                "distance": r.get("distance"),
            })