import streamlit as st

from rag import (
    retrieve,
    generate_answer
)


st.set_page_config(
    page_title="Ask My Developer Docs",
    page_icon="📚",
    layout="wide"
)


st.title(
    "📚 Ask My Developer Docs"
)


st.write(
    "Ask questions about the provided "
    "developer documentation. "
    "Answers are grounded only in "
    "the indexed documents."
)


question = st.text_input(
    "Ask a question",
    placeholder=(
        "Example: "
        "How do I authenticate with the API?"
    )
)


top_k = st.slider(
    "Number of retrieved chunks (Top-K)",
    min_value=1,
    max_value=10,
    value=5
)


if st.button(
    "🔍 Ask",
    type="primary"
):


    if not question.strip():

        st.warning(
            "Please enter a question."
        )

        st.stop()


    with st.spinner(
        "Searching documentation..."
    ):

        results = retrieve(
            question,
            top_k=top_k
        )


    if not results:

        st.error(
            "I don't know based on "
            "the provided documents."
        )

        st.stop()


    answer = generate_answer(
        question,
        results
    )


    st.subheader(
        "Answer"
    )


    st.write(
        answer
    )


    st.subheader(
        "Sources"
    )


    shown_sources = set()


    for result in results:

        source_key = (
            result["source"],
            result["page"]
        )


        if source_key in shown_sources:
            continue


        shown_sources.add(
            source_key
        )


        st.write(
            f"📄 **{result['source']}** "
            f"— Page {result['page']}"
        )


    with st.expander(
        "🔎 Show retrieved chunks"
    ):

        for index, result in enumerate(
            results,
            start=1
        ):

            st.markdown(
                f"### Retrieved Chunk {index}"
            )


            st.write(
                f"**Document:** "
                f"{result['source']}"
            )


            st.write(
                f"**Page:** "
                f"{result['page']}"
            )


            st.write(
                f"**Distance:** "
                f"{result['distance']:.4f}"
            )


            st.write(
                result["text"]
            )