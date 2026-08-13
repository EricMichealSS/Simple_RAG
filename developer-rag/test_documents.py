import os

from pypdf import PdfReader

from config import DOCUMENTS_DIR


def main():

    files = os.listdir(
        DOCUMENTS_DIR
    )

    pdf_files = [
        file
        for file in files
        if file.lower().endswith(".pdf")
    ]

    if not pdf_files:

        print(
            "No PDF files found in "
            f"{DOCUMENTS_DIR}"
        )

        return


    for filename in pdf_files:

        filepath = os.path.join(
            DOCUMENTS_DIR,
            filename
        )

        reader = PdfReader(filepath)

        print("=" * 70)

        print(
            f"Document: {filename}"
        )

        print(
            f"Pages: {len(reader.pages)}"
        )


        for page_number, page in enumerate(
            reader.pages[:2],
            start=1
        ):

            text = page.extract_text()

            print(
                f"\n--- Page {page_number} ---"
            )

            print(
                (text or "")[:1000]
            )


if __name__ == "__main__":
    main()