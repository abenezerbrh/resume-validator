import sys
import yaml

from src.parser import parse_resume
from src.validator import validate_resume
from src.parser import load_job_posting
from src.job_analyzer import (
    extract_job_skills,
    compare_resume_to_job
)

def load_rules():
    with open("config/rules.yaml", "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def print_report(results, file_path):
    passed = sum(
        1 for result in results
        if result["status"] == "PASS"
    )

    failed = sum(
        1 for result in results
        if result["status"] == "FAIL"
    )

    print("\n" + "=" * 50)
    print("           RESUME VALIDATION REPORT")
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


def main():
    if len(sys.argv) != 3:
        print("Usage: python main.py <resume.docx> <job.txt>")
        return

    resume_path = sys.argv[1]
    job_path = sys.argv[2]

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

if __name__ == "__main__":
    main()