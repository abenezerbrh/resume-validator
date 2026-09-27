import yaml
from docx import Document

from src.docx_generator import generate_tailored_docx
from src.parser import parse_resume
from src.validator import validate_resume, validate_resume_file


RESUME_LINES = [
    "Jane Doe",
    "jane@example.com | 416-555-0100 | LinkedIn | GitHub",
    "Profile",
    "Software engineering student.",
    "Education",
    "BEng, Software Engineering",
    "Experience",
    "Developer Intern",
    "Projects",
    "Resume Validator",
    "Technical Skills",
    "Languages: Python, SQL",
]


def load_rules():
    with open("config/rules.yaml", "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def create_resume(path):
    document = Document()

    for line in RESUME_LINES:
        document.add_paragraph(line)

    document.save(path)


def statuses(results):
    return {result["rule"]: result["status"] for result in results}


def test_validate_resume_file_matches_phase_1_validation(tmp_path):
    resume = tmp_path / "resume.docx"
    create_resume(resume)
    rules = load_rules()

    assert validate_resume_file(resume, rules) == validate_resume(
        parse_resume(resume),
        rules
    )


def test_validates_generated_tailored_docx(tmp_path):
    original = tmp_path / "resume.docx"
    tailored = tmp_path / "resume_tailored.docx"
    create_resume(original)
    original_bytes = original.read_bytes()
    rules = load_rules()

    # Tailored text that dropped the Projects section.
    tailored_text = "\n".join(
        line for line in RESUME_LINES
        if line not in ("Projects", "Resume Validator")
    )

    generate_tailored_docx(original, tailored_text, tailored)

    original_results = statuses(validate_resume_file(original, rules))
    tailored_results = statuses(validate_resume_file(tailored, rules))

    assert all(status == "PASS" for status in original_results.values())
    assert tailored_results["Projects section"] == "FAIL"
    assert all(
        status == "PASS"
        for rule, status in tailored_results.items()
        if rule != "Projects section"
    )

    assert original.read_bytes() == original_bytes
