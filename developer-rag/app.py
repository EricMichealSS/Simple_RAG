"""Streamlit app — Upload-driven RAG Q&A (hybrid + rerank, always)."""

import time
import functools

import streamlit as st

from rag_hybrid import (
    retrieve,
    is_relevant,
    refresh_indexes,
    collection,
    _embedding_model,
)
from ingest_live import ingest_uploaded_file
from config import GROK_API_KEY, GENERATION_MODEL, COLLECTION_NAME
from openai import OpenAI


st.set_page_config(
    page_title="Doc Q&A",
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
# Cached query embedding
# ============================================================

@functools.lru_cache(maxsize=256)
def _embed_query(question: str):
    return _embedding_model.encode([question], normalize_embeddings=True)[0].tolist()


# ============================================================
# Streaming answer generation
# ============================================================

def _build_prompt(question, results):
    MAX_CHUNK_CHARS = 600   # trim each chunk
    MAX_TOTAL_CHARS = 1800  # total context cap Groq accepts

    context_parts = []
    total_chars = 0
    for i, result in enumerate(results, start=1):
        chunk_id = result.get("chunk_id", "unknown")
        source_file = result.get("source_file", result.get("source", "unknown"))
        anchor = result.get("anchor", "")

        text = result["text"][:MAX_CHUNK_CHARS]
        if total_chars + len(text) > MAX_TOTAL_CHARS:
            break
        total_chars += len(text)

        context_parts.append(
            f"SOURCE {i} [chunk_id: {chunk_id} | {source_file}#{anchor}]\n{text}"
        )

    context = "\n\n".join(context_parts)
    return f"""You are a document question-answering assistant.

Your ONLY source of truth is the documentation supplied in CONTEXT.

STRICT RULES:
1. Answer ONLY from the supplied context.
2. Do not use your general knowledge.
3. Do not guess or invent facts.
4. If the answer cannot be supported by the context, respond exactly:
   "I don't know based on the provided documents."
5. For EVERY factual claim, cite the source chunk_id in square brackets,
   e.g. [my-doc:p1:introduction:0].
6. Keep the answer concise and accurate.

CONTEXT:
{context}

USER QUESTION:
{question}

ANSWER (with [chunk_id] citations):"""


def stream_answer(question, results):
    if not results or not is_relevant(results):
        yield "I don't know based on the provided documents."
        return

    prompt = _build_prompt(question, results)

    try:
        stream = grok_client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[
                {"role": "system", "content": "You are a document QA assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=512,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as e:
        chunks_md = "\n\n---\n\n".join(
            f"**Source {i}** `[{r.get('chunk_id', '?')}]` ({r.get('source_file', '?')})\n{r['text'][:300]}..."
            for i, r in enumerate(results, 1)
        )
        yield (
            f"⚠️ **Generation unavailable ({type(e).__name__}).**\n\n"
            f"Error: {str(e)}\n\n"
            f"Relevant chunks:\n\n{chunks_md}"
        )


# ============================================================
# UI
# ============================================================

st.title("📚 Document Q&A")
st.caption("Upload a PDF or Markdown file — then ask anything about it.")

# --- Sidebar ---
with st.sidebar:
    st.header("📤 Upload Document")
    st.caption("Supported: `.pdf`, `.md`")

    uploaded_file = st.file_uploader("Choose a file", type=["pdf", "md"])

    if uploaded_file is not None:
        if st.button("Ingest Document", type="primary", use_container_width=True):
            with st.spinner(f"Processing `{uploaded_file.name}`..."):
                try:
                    t0 = time.perf_counter()
                    added, skipped = ingest_uploaded_file(
                        file_bytes=uploaded_file.read(),
                        filename=uploaded_file.name,
                        embedding_model=_embedding_model,
                    )
                    refresh_indexes()
                    elapsed_ms = (time.perf_counter() - t0) * 1000

                    if added > 0:
                        st.success(
                            f"✅ **{added} chunks** indexed from `{uploaded_file.name}`"
                            + (f" · {skipped} duplicates skipped" if skipped else "")
                            + f"\n\n⏱ {elapsed_ms:.0f} ms"
                        )
                        st.rerun()
                    else:
                        st.info(f"`{uploaded_file.name}` already indexed ({skipped} chunks present).")
                except Exception as e:
                    st.error(f"❌ Ingestion failed: {e}")

    st.divider()

    # Index stats + per-file chunk breakdown
    try:
        total = collection.count()
    except Exception:
        total = 0

    st.metric("Indexed chunks", total)

    if total > 0:
        try:
            metas = collection.get(include=["metadatas"])["metadatas"]
            files = sorted({m.get("source_file", "?") for m in metas})
            st.markdown("**Indexed files:**")
            for f in files:
                chunk_count = sum(1 for m in metas if m.get("source_file") == f)
                with st.expander(f"📄 {f} — {chunk_count} chunks"):
                    file_metas = [m for m in metas if m.get("source_file") == f]
                    for m in file_metas:
                        section = m.get("section") or m.get("anchor") or "—"
                        st.markdown(f"- `{section}`")
        except Exception:
            pass

    st.divider()
    top_k = st.slider("Top-K chunks", 1, 10, 5)


# --- Main area ---
if total == 0:
    st.info("👈 **Upload a document** from the sidebar to get started.")
    st.stop()

question = st.text_input(
    "Ask a question about your documents",
    placeholder="e.g. What are the authentication methods supported?",
)

if st.button("🔍 Ask", type="primary", use_container_width=True):
    if not question.strip():
        st.warning("Please enter a question.")
        st.stop()

    # Retrieval — cached embedding, hybrid + rerank always
    with st.spinner("Searching..."):
        try:
            t0 = time.perf_counter()
            query_vec = _embed_query(question)
            results = retrieve(
                question,
                top_k=top_k,
                collection_name=COLLECTION_NAME,
                where=None,
                use_hybrid=True,
                use_rerank=True,
                _query_embedding=query_vec,
            )
            retrieval_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            st.error(f"❌ Retrieval error: {type(e).__name__}: {e}")
            st.stop()

    if not results:
        st.warning("No results found. Try rephrasing your question.")
        st.stop()

    # Streaming answer
    st.subheader("Answer")
    t1 = time.perf_counter()
    st.write_stream(stream_answer(question, results))
    gen_ms = (time.perf_counter() - t1) * 1000

    st.caption(
        f"⏱ Retrieval: **{retrieval_ms:.0f} ms** (hybrid + rerank) · "
        f"Generation: **{gen_ms:.0f} ms**"
    )

    # Sources
    st.subheader("Sources")
    for result in results:
        chunk_id = result.get("chunk_id", "?")
        source_file = result.get("source_file", result.get("source", "?"))
        anchor = result.get("anchor", "")
        distance = result.get("distance", 0)
        rerank_score = result.get("rerank_score")

        score_line = f"Distance: `{distance:.4f}`"
        if rerank_score is not None:
            score_line += f" · Rerank: `{rerank_score:.4f}`"

        with st.container(border=True):
            st.markdown(
                f"**📄 {source_file}**  \n"
                f"`{chunk_id}`"
                + (f"  \n`#{anchor}`" if anchor else "")
                + f"  \n{score_line}"
            )
            with st.expander("Show chunk text"):
                st.code(result["text"], language="markdown")

    with st.expander("🔧 Debug: Raw metadata"):
        for i, r in enumerate(results):
            st.json({k: r.get(k) for k in [
                "chunk_id", "source_file", "page_id", "page", "chunk",
                "anchor", "section", "distance", "rerank_score",
            ] if r.get(k) is not None} | {"rank": i + 1})
