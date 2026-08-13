from rag import (
    retrieve,
    generate_answer
)


question = (
    "How do I make chocolate cake?"
)


results = retrieve(
    question,
    top_k=5
)


answer = generate_answer(
    question,
    results
)


print(
    "\n================================"
)

print(
    "ANSWER"
)

print(
    "================================"
)

print(
    answer
)


print(
    "\n================================"
)

print(
    "SOURCES"
)

print(
    "================================"
)


seen = set()


for result in results:

    key = (
        result["source"],
        result["page"]
    )


    if key in seen:
        continue


    seen.add(key)


    print(
        f"- {result['source']} "
        f"(page {result['page']})"
    )