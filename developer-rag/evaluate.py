import json

from rag import (
    retrieve,
    generate_answer,
    is_relevant
)


with open(
    "evaluation_questions.json",
    "r",
    encoding="utf-8"
) as file:

    questions = json.load(
        file
    )


correct_retrieval = 0

correct_unknown = 0

answerable_count = 0

unknown_count = 0


for item in questions:

    question = item["question"]

    expected_source = (
        item["expected_source"]
    )

    should_answer = (
        item["should_answer"]
    )


    print(
        "\n" + "=" * 70
    )

    print(
        "QUESTION:"
    )

    print(
        question
    )


    results = retrieve(
        question,
        top_k=5
    )


    relevant = is_relevant(
        results
    )


    if should_answer:

        answerable_count += 1


        if expected_source:

            found_source = any(
                result["source"]
                == expected_source
                for result in results
            )


            if found_source:

                correct_retrieval += 1

                print(
                    "✓ Expected source retrieved"
                )

            else:

                print(
                    "✗ Expected source NOT retrieved"
                )


        answer = generate_answer(
            question,
            results
        )


        print(
            "\nANSWER:"
        )

        print(
            answer
        )


    else:

        unknown_count += 1


        answer = generate_answer(
            question,
            results
        )


        print(
            "\nANSWER:"
        )

        print(
            answer
        )


        if (
            "I don't know"
            in answer
        ):

            correct_unknown += 1

            print(
                "✓ Correctly rejected"
            )

        else:

            print(
                "✗ Hallucination / "
                "incorrect acceptance"
            )


print(
    "\n\n" + "=" * 70
)

print(
    "EVALUATION SUMMARY"
)

print(
    "=" * 70
)


if answerable_count:

    print(
        "Retrieval accuracy:",
        f"{correct_retrieval}"
        f"/{answerable_count}"
    )


if unknown_count:

    print(
        "Unknown-question handling:",
        f"{correct_unknown}"
        f"/{unknown_count}"
    )