"""
The Phase 1-5 resume workflow as reusable steps, shared by main.py and the
Telegram bot. Nothing here prints; callers format the results.
"""

import hashlib
from pathlib import Path

import yaml

from src.application_tracker import (
    ApplicationError,
    ApplicationNotFoundError,
    find_application,
    link_resume_version,
    load_version_metadata
)
from src.claim_validator import ClaimValidationError, validate_claims
from src.claude_tailer import tailor_resume
from src.job_analyzer import (
    compare_resume_file_to_job,
    compare_resume_to_job,
    extract_job_skills
)
from src.parser import get_resume_text, load_job_posting, parse_resume
from src.phase4_summary import get_final_status
from src.resume_versioning import save_tailored_version
from src.validator import validate_resume, validate_resume_file


RULES_PATH = Path("config") / "rules.yaml"

DEFAULT_RESUME_SETTING = "DEFAULT_RESUME_PATH"


class TailoringInputError(ApplicationError):
    pass


def load_rules(path=RULES_PATH):
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def file_sha256(file_path):
    with open(file_path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def analyze(resume_path, job_path, rules):
    """Phase 1 validation and Phase 2 job analysis of the original resume."""
    resume = parse_resume(resume_path)
    job_text = load_job_posting(job_path)
    job_skills = extract_job_skills(job_text, rules["job"]["skills"])
    resume_text = get_resume_text(resume)

    return {
        "resume_results": validate_resume(resume, rules),
        "resume_text": resume_text,
        "job_text": job_text,
        "job_skills": job_skills,
        "comparison": compare_resume_to_job(resume_text, job_skills),
    }


def tailor(analysis):
    """Phase 3: ask Claude for the tailored resume text (ClaudeTailorError)."""
    comparison = analysis["comparison"]

    return tailor_resume(
        analysis["resume_text"],
        analysis["job_text"],
        comparison["matched_skills"],
        comparison["missing_skills"]
    )


def tailor_and_validate(resume_path, job_id, analysis, rules):
    """
    Phase 3 tailoring, Phase 5 versioning, and Phase 4 validation of the
    new version. Raises ClaudeTailorError, DocxGenerationError, or
    VersionExistsError if the tailored resume cannot be created.
    """
    original_hash = file_sha256(resume_path)
    tailored_text = tailor(analysis)

    return save_and_validate(
        resume_path,
        job_id,
        tailored_text,
        analysis,
        rules,
        original_hash
    )


def save_and_validate(
    resume_path,
    job_id,
    tailored_text,
    analysis,
    rules,
    original_hash
):
    """Phase 5 versioning, then Phase 4 validation of the new version."""
    version = save_tailored_version(resume_path, tailored_text, job_id)
    output_path = Path(version["tailored_docx"])

    tailored_results = validate_resume_file(output_path, rules)

    # Phase 4.2 and 4.3 use the generated DOCX as the source of truth.
    tailored_comparison = compare_resume_file_to_job(
        output_path,
        analysis["job_skills"]
    )

    claims = None
    claim_error = None

    try:
        claims = validate_claims(
            analysis["resume_text"],
            get_resume_text(parse_resume(output_path)),
            analysis["job_text"]
        )
    except ClaimValidationError as error:
        claim_error = error

    original_unchanged = file_sha256(resume_path) == original_hash

    return {
        "tailored_text": tailored_text,
        "version": version,
        "output_path": output_path,
        "tailored_results": tailored_results,
        "tailored_comparison": tailored_comparison,
        "claims": claims,
        "claim_error": claim_error,
        "original_unchanged": original_unchanged,
        "final": get_final_status(tailored_results, claims, original_unchanged),
    }


def resolve_resume_path(application, default_resume_path=None):
    """
    The applications table stores the job posting but not the resume, so
    use the original resume recorded by the linked Phase 5 version, or
    else the configured default resume.
    """
    if application["resume_version_path"]:
        version_dir = Path(application["resume_version_path"]).parent

        try:
            metadata = load_version_metadata(version_dir)
        except ApplicationError:
            metadata = {}

        if metadata.get("original_resume_path"):
            return Path(metadata["original_resume_path"])

    if default_resume_path:
        return Path(default_resume_path)

    raise TailoringInputError(
        f"No resume is known for '{application['job_id']}'. "
        f"Set {DEFAULT_RESUME_SETTING} to the resume to tailor."
    )


def tailor_application(conn, job_id, default_resume_path=None, rules=None):
    """
    Tailor the resume for an existing tracked application and link the new
    version to it. Never creates an application or marks it Applied.
    """
    application = find_application(conn, job_id)

    if application is None:
        raise ApplicationNotFoundError(
            f"No application found for job_id '{job_id}'."
        )

    resume_path = resolve_resume_path(application, default_resume_path)
    job_path = Path(application["job_posting_path"])

    if not resume_path.is_file():
        raise TailoringInputError(f"Resume file not found: {resume_path}")

    if not job_path.is_file():
        raise TailoringInputError(f"Job posting not found: {job_path}")

    rules = rules or load_rules()
    analysis = analyze(resume_path, job_path, rules)
    result = tailor_and_validate(resume_path, job_id, analysis, rules)

    result["application"] = link_resume_version(conn, job_id, result["version"])
    result["resume_path"] = resume_path

    return result
