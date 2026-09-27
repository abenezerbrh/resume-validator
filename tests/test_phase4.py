import json

import pytest
import yaml
from docx import Document

from src.claim_validator import (
    ClaimValidationError,
    parse_claims_response,
    validate_claims
)
from src.claude_tailer import ClaudeTailorError
from src.docx_generator import generate_tailored_docx
from src.job_analyzer import (
    compare_resume_file_to_job,
    compare_resume_to_job,
    extract_job_skills
)
from src.parser import get_resume_text, parse_resume
from src.phase4_summary import (
    READY_FOR_REVIEW,
    REVIEW_REQUIRED,
    get_final_status
)


def load_rules():
    with open("config/rules.yaml", "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def create_resume(path, lines):
    document = Document()

    for line in lines:
        document.add_paragraph(line)

    document.save(path)


# ---------- Phase 4.2: job alignment ----------

def test_job_alignment_uses_phase_2_matching_on_generated_docx(tmp_path):
    original = tmp_path / "resume.docx"
    tailored = tmp_path / "resume_tailored.docx"
    create_resume(original, ["Technical Skills", "Python, SQL"])

    # React only appears in the generated DOCX, not in the original resume.
    generate_tailored_docx(
        original,
        "Technical Skills\nPython, SQL, ReactJS",
        tailored
    )

    job_text = "We use React.js, Python and Docker."
    job_skills = extract_job_skills(job_text, load_rules()["job"]["skills"])

    comparison = compare_resume_file_to_job(tailored, job_skills)

    assert comparison == compare_resume_to_job(
        get_resume_text(parse_resume(tailored)),
        job_skills
    )
    assert {
        match["skill"]: match["evidence"]
        for match in comparison["matched_skills"]
    } == {"Python": "Python", "React": "React"}
    assert comparison["missing_skills"] == ["Docker"]


# ---------- Phase 4.3: claim validation ----------

def claims_json(*claims):
    return json.dumps({"claims": list(claims)})


def test_parses_structured_claims():
    response = claims_json({
        "claim": "Collaborated with developers",
        "reason": "Original does not mention developers",
        "original_evidence": "Collaborated with cross-functional stakeholders",
        "severity": "medium"
    })

    assert parse_claims_response(response) == [{
        "claim": "Collaborated with developers",
        "reason": "Original does not mention developers",
        "original_evidence": "Collaborated with cross-functional stakeholders",
        "severity": "MEDIUM"
    }]


def test_parses_claims_wrapped_in_code_fence_and_missing_evidence():
    response = "```json\n" + claims_json({
        "claim": "Deployed REST APIs using AWS",
        "reason": "AWS is not in the original resume",
        "original_evidence": None,
        "severity": "HIGH"
    }) + "\n```"

    claims = parse_claims_response(response)

    assert claims[0]["original_evidence"] == ""
    assert claims[0]["severity"] == "HIGH"


def test_parses_no_claims():
    assert parse_claims_response('{"claims": []}') == []


@pytest.mark.parametrize("response", [
    "I could not find any issues.",
    '{"claims": "none"}',
    claims_json({"claim": "Used AWS", "severity": "CRITICAL"}),
    claims_json({"reason": "no claim text", "severity": "LOW"}),
])
def test_rejects_malformed_claim_responses(response):
    with pytest.raises(ClaimValidationError):
        parse_claims_response(response)


def test_validate_claims_sends_resumes_and_job_as_context_only():
    prompts = []

    def fake_claude(prompt):
        prompts.append(prompt)
        return claims_json({
            "claim": "Designed and deployed REST APIs using AWS",
            "reason": "AWS and deployment are not in the original resume",
            "original_evidence": "Designed REST API integrations.",
            "severity": "HIGH"
        })

    claims = validate_claims(
        "Designed REST API integrations.",
        "Designed and deployed REST APIs using AWS.",
        "Experience with AWS required.",
        run=fake_claude
    )

    assert [claim["severity"] for claim in claims] == ["HIGH"]

    prompt = prompts[0]
    assert "Designed REST API integrations." in prompt
    assert "Designed and deployed REST APIs using AWS." in prompt
    assert "Experience with AWS required." in prompt
    assert "NOT evidence" in prompt


def test_validate_claims_reports_claude_errors():
    def failing_claude(prompt):
        raise ClaudeTailorError("Claude Code CLI not found.")

    with pytest.raises(ClaimValidationError, match="not found"):
        validate_claims("a", "b", "c", run=failing_claude)


# ---------- Phase 4.4: final status ----------

PASSING = [{"rule": "Email present", "status": "PASS", "message": ""}]
FAILING = PASSING + [
    {"rule": "Projects section", "status": "FAIL", "message": ""}
]


def claim(severity):
    return {
        "claim": "x",
        "reason": "",
        "original_evidence": "",
        "severity": severity
    }


def test_ready_when_checks_pass_and_no_claims():
    final = get_final_status(PASSING, [])

    assert final == {"status": READY_FOR_REVIEW, "reasons": []}


def test_low_severity_claims_do_not_require_review():
    assert get_final_status(PASSING, [claim("LOW")])["status"] == READY_FOR_REVIEW


@pytest.mark.parametrize("severity", ["MEDIUM", "HIGH"])
def test_medium_or_high_claims_require_review(severity):
    final = get_final_status(PASSING, [claim("LOW"), claim(severity)])

    assert final["status"] == REVIEW_REQUIRED
    assert final["reasons"] == ["1 MEDIUM/HIGH potential unsupported claim(s)"]


def test_validation_failures_require_review():
    final = get_final_status(FAILING, [])

    assert final["status"] == REVIEW_REQUIRED
    assert final["reasons"] == ["1 resume validation check(s) failed"]


def test_incomplete_claim_validation_requires_review():
    final = get_final_status(PASSING, None)

    assert final["status"] == REVIEW_REQUIRED
    assert final["reasons"] == ["Claim validation could not be completed"]


def test_modified_original_requires_review():
    final = get_final_status(PASSING, [], original_unchanged=False)

    assert final["status"] == REVIEW_REQUIRED
