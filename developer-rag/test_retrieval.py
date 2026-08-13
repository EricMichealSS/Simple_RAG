from rag import retrieve


questions = [
    "How do I authenticate with the API?",
    "What is the primary rate limit for unauthenticated users?",
    "How does pagination work in the REST API?",
]


for question in questions:

    print("\n")
    print("=" * 80)
    print("QUESTION:")
    print(question)
    print("=" * 80)

    results = retrieve(question)

    if not results:
        print("NO RESULTS")
        continue

    for i, result in enumerate(
        results,
        start=1
    ):

        print("\n")
        print(f"RESULT {i}")
        print("-" * 80)

        print(
            "Source:",
            result["source"]
        )

        print(
            "Page:",
            result["page"]
        )

        print(
            "Chunk:",
            result["chunk"]
        )

        print(
            "Distance:",
            result["distance"]
        )

        print("\nTEXT:")
        print(
            result["text"][:1000]
        )