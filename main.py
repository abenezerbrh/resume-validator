import sys

import psycopg

from src.claude_tailer import ClaudeTailorError
from src.docx_generator import DocxGenerationError
from src.resume_versioning import (
    job_id_from_path,
    make_job_id,
    VersionExistsError
)
from src.phase4_summary import READY_FOR_REVIEW
from src.application_tracker import (
    ApplicationError,
    find_application,
    link_resume_version,
    open_tracker
)
from src.database import DatabaseConfigError
from src.tailoring_workflow import (
    analyze,
    file_sha256,
    load_rules,
    save_and_validate,
    tailor as tailor_resume_text
)


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

    analysis = analyze(resume_path, job_path, rules)
    job_skills = analysis["job_skills"]
    comparison = analysis["comparison"]

    print_report(
        analysis["resume_results"],
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
        tailored_resume = tailor_resume_text(analysis)
    except ClaudeTailorError as error:
        print(f"\nError: {error}")
        sys.exit(1)

    print("\n" + tailored_resume)
    print("\n" + "=" * 50)

    try:
        result = save_and_validate(
            resume_path,
            job_id,
            tailored_resume,
            analysis,
            rules,
            original_hash
        )
    except (DocxGenerationError, VersionExistsError) as error:
        print(f"\nDOCX generation failed: {error}")
        print("The tailored resume text above was not saved.")
        sys.exit(1)

    version = result["version"]
    output_path = result["output_path"]

    print(f"\nTailored resume saved to: {output_path}")
    print(f"Version: {version['job_id']} / {version['version_id']}")

    print_report(
        result["tailored_results"],
        output_path,
        title="PHASE 4 VALIDATION"
    )

    print_job_alignment(job_skills, result["tailored_comparison"])

    print_claim_validation(result["claims"], result["claim_error"])

    print_phase4_summary(
        result["tailored_results"],
        result["tailored_comparison"],
        result["claims"],
        result["final"]
    )

    if result["original_unchanged"]:
        print(f"\nOriginal resume unchanged: {resume_path}")
    else:
        print(f"\nWarning: original resume was modified: {resume_path}")

    if track:
        print_tracked_application(job_id, version)

if __name__ == "__main__":
    main()