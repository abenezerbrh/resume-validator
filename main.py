import sys
import yaml

from src.parser import parse_resume
from src.validator import validate_resume


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
    if len(sys.argv) != 2:
        print("Usage: python main.py <resume.docx>")
        return

    file_path = sys.argv[1]

    rules = load_rules()
    resume = parse_resume(file_path)

    results = validate_resume(resume, rules)

    print_report(results, file_path)


if __name__ == "__main__":
    main()