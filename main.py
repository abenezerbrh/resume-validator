import hashlib
import sys
from pathlib import Path

import psycopg
import yaml

from src.parser import parse_resume, get_resume_text
from src.validator import validate_resume, validate_resume_file
from src.parser import load_job_posting
from src.job_analyzer import (
    extract_job_skills,
    compare_resume_to_job,
    compare_resume_file_to_job
)
from src.claude_tailer import tailor_resume, ClaudeTailorError
from src.docx_generator import DocxGenerationError
from src.resume_versioning import (
    job_id_from_path,
    make_job_id,
    save_tailored_version,
    VersionExistsError
)
from src.claim_validator import validate_claims, ClaimValidationError
from src.phase4_summary import get_final_status, READY_FOR_REVIEW
from src.application_tracker import (
    ApplicationError,
    find_application,
    link_resume_version,
    open_tracker
)
from src.database import DatabaseConfigError

def load_rules():
    with open("config/rules.yaml", "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def file_sha256(file_path):
    with open(file_path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def print_report(results, file_path, title="RESUME VALIDATION REPORT"):
    passed = sum(
        1 for result in results
        if result["status"] == "PASS"
    )

    failed = sum(
        1 for result in results
        if result["status"] == "FAIL"
    )

    print("\n" + "=" * 50)
    print(f"           {title}")
    print("=" * 50)

    print(f"\nFile: {file_path}")

    print("\nSUMMARY")
    print("-------")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

    print("\nRESULTS")
    print("-------")

    for result in results:
        symbol = "✓" if result["status"] == "PASS" else "✗"

        print(
            f"{symbol} {result['rule']}: "
            f"{result['message']}"
        )

    print("\n" + "=" * 50)


def print_job_alignment(job_skills, comparison):
    print("\n" + "=" * 50)
    print("           PHASE 4 JOB ALIGNMENT")
    print("=" * 50)

    print("\nSkills found in job:")

    for skill in job_skills:
        print(f"• {skill}")

    print("\nMatched by tailored resume:")

    for match in comparison["matched_skills"]:
        print(
            f"✓ {match['skill']} "
            f"(found: {match['evidence']})"
        )

    print("\nStill missing from tailored resume:")

    for skill in comparison["missing_skills"]:
        print(f"✗ {skill}")

    print("\n" + "=" * 50)


def print_claim_validation(claims, error=None):
    print("\n" + "=" * 50)
    print("         PHASE 4 CLAIM VALIDATION")
    print("=" * 50)

    if error:
        print(f"\n⚠ Claim validation could not be completed: {error}")
    elif not claims:
        print("\n✓ No unsupported claims detected")
    else:
        print("\nPotential unsupported claims (for human review):")

        for number, claim in enumerate(claims, start=1):
            print(f"\n{number}. [{claim['severity']}] {claim['claim']}")
            print(f"   Why: {claim['reason']}")
            print(
                "   Original evidence: "
                f"{claim['original_evidence'] or 'None found'}"
            )

    print("\n" + "=" * 50)


def print_phase4_summary(validation_results, comparison, claims, final):
    passed = sum(
        1 for result in validation_results
        if result["status"] == "PASS"
    )
    failed = len(validation_results) - passed

    print("\n" + "=" * 50)
    print("             PHASE 4 FINAL RESULT")
    print("=" * 50)

    print("\nRESUME VALIDATION")
    print("-----------------")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

    print("\nJOB ALIGNMENT")
    print("-------------")
    print(f"Matched: {len(comparison['matched_skills'])}")
    print(f"Still missing: {len(comparison['missing_skills'])} (informational)")

    print("\nCLAIM VALIDATION")
    print("----------------")

    if claims is None:
        print("Potential unsupported claims: not checked")
    else:
        print(f"Potential unsupported claims: {len(claims)}")

        for severity in ("HIGH", "MEDIUM", "LOW"):
            count = sum(
                1 for claim in claims
                if claim["severity"] == severity
            )

            if count:
                print(f"  {severity}: {count}")

    print("\nFINAL STATUS")
    print("------------")

    if final["status"] == READY_FOR_REVIEW:
        print(f"✓ {final['status']}")
        print("No blocking issues found. Review the tailored resume before using it.")
    else:
        print(f"⚠ {final['status']}")

        for reason in final["reasons"]:
            print(f"  - {reason}")

        print(
            "Human review is recommended before using this resume. "
            "It has not been rejected or changed."
        )

    print("\n" + "=" * 50)


def check_tracked_application(job_id):
    """Fail before tailoring if --track cannot link the new version."""
    try:
        with open_tracker() as conn:
            application = find_application(conn, job_id)
    except (DatabaseConfigError, psycopg.OperationalError) as error:
        return f"Could not connect to the application tracker: {error}"

    if application is None:
        return (
            f"No tracked application for job_id '{job_id}'. "
            f"Add it first with: python tracker.py add --job-id {job_id} ..."
        )

    return None


def print_tracked_application(job_id, version):
    print("\n" + "=" * 50)
    print("           APPLICATION TRACKER")
    print("=" * 50)

    try:
        with open_tracker() as conn:
            application = link_resume_version(conn, job_id, version)
    except (
        DatabaseConfigError,
        ApplicationError,
        psycopg.OperationalError
    ) as error:
        print(f"\n⚠ Resume version was saved but not linked: {error}")
    else:
        print(
            f"\nApplication: {application['company']} - "
            f"{application['job_title']}"
        )
        print(f"Job ID: {application['job_id']}")
        print(f"Status: {application['status']}")
        print(f"Resume version: {application['resume_version_id']}")
        print(f"Resume path: {application['resume_version_path']}")

    print("\n" + "=" * 50)


def main():
    args = sys.argv[1:]
    tailor = "--tailor" in args

    if tailor:
        args.remove("--tailor")

    track = "--track" in args

    if track:
        args.remove("--track")

    job_id = None

    if "--job-id" in args:
        index = args.index("--job-id")

        if index + 1 < len(args):
            job_id = make_job_id(args[index + 1])
            del args[index:index + 2]

    if len(args) != 2:
        print(
            "Usage: python main.py <resume.docx> <job.txt> "
            "[--tailor] [--job-id <id>] [--track]"
        )
        return

    if track and not tailor:
        print("--track links a newly tailored resume, so it requires --tailor.")
        return

    resume_path = args[0]
    job_path = args[1]
    job_id = job_id or job_id_from_path(job_path)

    rules = load_rules()

    resume = parse_resume(resume_path)

    resume_results = validate_resume(
        resume,
        rules
    )

    job_text = load_job_posting(job_path)

    job_skills = extract_job_skills(
        job_text,
        rules["job"]["skills"]
    )

    resume_text = "\n".join(
        paragraph["text"]
        for paragraph in resume["paragraphs"]
    )

    comparison = compare_resume_to_job(
        resume_text,
        job_skills
    )

    print_report(
        resume_results,
        resume_path
    )

    print("\n" + "=" * 50)
    print("             JOB ANALYSIS")
    print("=" * 50)

    print("\nSKILLS FOUND IN JOB")
    print("-------------------")

    for skill in job_skills:
        print(f"• {skill}")

    print("\nMATCHED BY RESUME")
    print("-----------------")

    for match in comparison["matched_skills"]:
        print(
            f"✓ {match['skill']} "
            f"(found: {match['evidence']})"
        )

    print("\nMISSING FROM RESUME")
    print("-------------------")

    for skill in comparison["missing_skills"]:
        print(f"✗ {skill}")

    print("\n" + "=" * 50)

    if not tailor:
        return

    if track:
        tracker_error = check_tracked_application(job_id)

        if tracker_error:
            print(f"\nError: {tracker_error}")
            sys.exit(1)

    original_hash = file_sha256(resume_path)

    print("\n" + "=" * 50)
    print("         TAILORED RESUME (CLAUDE)")
    print("=" * 50)

    try:
        tailored_resume = tailor_resume(
            resume_text,
            job_text,
            comparison["matched_skills"],
            comparison["missing_skills"]
        )
    except ClaudeTailorError as error:
        print(f"\nError: {error}")
        sys.exit(1)

    print("\n" + tailored_resume)
    print("\n" + "=" * 50)

    try:
        version = save_tailored_version(
            resume_path,
            tailored_resume,
            job_id
        )
    except (DocxGenerationError, VersionExistsError) as error:
        print(f"\nDOCX generation failed: {error}")
        print("The tailored resume text above was not saved.")
        sys.exit(1)

    output_path = Path(version["tailored_docx"])

    print(f"\nTailored resume saved to: {output_path}")
    print(f"Version: {version['job_id']} / {version['version_id']}")

    tailored_results = validate_resume_file(
        output_path,
        rules
    )

    print_report(
        tailored_results,
        output_path,
        title="PHASE 4 VALIDATION"
    )

    # Phase 4.2 and 4.3 use the generated DOCX as the source of truth.
    tailored_comparison = compare_resume_file_to_job(
        output_path,
        job_skills
    )

    print_job_alignment(job_skills, tailored_comparison)

    tailored_text = get_resume_text(parse_resume(output_path))
    claims = None
    claim_error = None

    try:
        claims = validate_claims(
            resume_text,
            tailored_text,
            job_text
        )
    except ClaimValidationError as error:
        claim_error = error

    print_claim_validation(claims, claim_error)

    original_unchanged = file_sha256(resume_path) == original_hash

    final = get_final_status(
        tailored_results,
        claims,
        original_unchanged
    )

    print_phase4_summary(
        tailored_results,
        tailored_comparison,
        claims,
        final
    )

    if original_unchanged:
        print(f"\nOriginal resume unchanged: {resume_path}")
    else:
        print(f"\nWarning: original resume was modified: {resume_path}")

    if track:
        print_tracked_application(job_id, version)

if __name__ == "__main__":
    main()