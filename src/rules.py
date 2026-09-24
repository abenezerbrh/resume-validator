import re


def check_required_sections(text, sections):
    results = []

    text_lower = text.lower()

    for section in sections:
        found = section.lower() in text_lower

        results.append({
            "rule": f"{section} section",
            "status": "PASS" if found else "FAIL",
            "message": (
                f"{section} section found"
                if found
                else f"{section} section missing"
            )
        })

    return results


def check_contact_information(text, required_contact):
    results = []

    checks = {
        "email": r"[\w\.-]+@[\w\.-]+\.\w+",
        "phone": r"(\+?1[\s.-]?)?(\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}",
        "linkedin": r"\blinkedin\b",
        "github": r"\bgithub\b"
    }

    for item in required_contact:
        pattern = checks.get(item)

        if pattern is None:
            continue

        found = re.search(pattern, text, re.IGNORECASE)

        results.append({
            "rule": f"{item.title()} present",
            "status": "PASS" if found else "FAIL",
            "message": (
                f"{item.title()} found"
                if found
                else f"{item.title()} not found"
            )
        })

    return results