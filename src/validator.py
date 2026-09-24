from src.rules import (
    check_required_sections,
    check_contact_information
)


def validate_resume(parsed_resume, rules):
    text = "\n".join(
        paragraph["text"]
        for paragraph in parsed_resume["paragraphs"]
    )

    results = []

    results.extend(
        check_required_sections(
            text,
            rules["resume"]["required_sections"]
        )
    )

    results.extend(
        check_contact_information(
            text,
            rules["resume"]["required_contact"]
        )
    )

    return results