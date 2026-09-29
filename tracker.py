import argparse
import sys
from datetime import date

import psycopg

from src.application_tracker import (
    STATUSES,
    ApplicationError,
    InvalidStatusError,
    add_application,
    find_application,
    link_resume_version,
    list_applications,
    load_version_metadata,
    open_tracker,
    update_status
)
from src.database import DatabaseConfigError, DuplicateApplicationError


FIELDS = (
    "id",
    "company",
    "job_title",
    "job_id",
    "job_posting_path",
    "job_url",
    "date_added",
    "date_applied",
    "status",
    "resume_version_id",
    "resume_version_path",
    "notes",
)


def print_application(application):
    print()

    for field in FIELDS:
        value = application[field]

        if field == "date_added":
            value = value.strftime("%Y-%m-%d %H:%M")

        print(f"{field:20} {value if value is not None else '-'}")


def print_application_list(applications):
    if not applications:
        print("No applications tracked yet.")
        return

    print(f"\n{'ID':>4}  {'JOB ID':20} {'COMPANY':15} {'STATUS':10} RESUME VERSION")

    for application in applications:
        print(
            f"{application['id']:>4}  "
            f"{application['job_id']:20} "
            f"{application['company']:15} "
            f"{application['status']:10} "
            f"{application['resume_version_id'] or '-'}"
        )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Track job applications in PostgreSQL."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Track a new application")
    add.add_argument("--company", required=True)
    add.add_argument("--job-title", required=True)
    add.add_argument("--job-id", required=True)
    add.add_argument("--job-posting", required=True)
    add.add_argument("--job-url")
    add.add_argument("--notes")
    add.add_argument("--status", default="Saved", help=", ".join(STATUSES))

    commands.add_parser("list", help="List tracked applications")

    show = commands.add_parser("show", help="Show one application")
    show.add_argument("job_id")

    status = commands.add_parser("status", help="Update an application's status")
    status.add_argument("job_id")
    status.add_argument("status", help=", ".join(STATUSES))
    status.add_argument(
        "--date-applied",
        type=date.fromisoformat,
        help="YYYY-MM-DD (defaults to today when first set to Applied)"
    )

    link = commands.add_parser(
        "link",
        help="Link a Phase 5 resume version to an application"
    )
    link.add_argument("job_id")
    link.add_argument(
        "version",
        help="Version folder (reports/versions/<job_id>/<version_id>) "
             "or its metadata.json"
    )

    return parser


def run(arguments, conn):
    if arguments.command == "add":
        print_application(add_application(
            conn,
            company=arguments.company,
            job_title=arguments.job_title,
            job_id=arguments.job_id,
            job_posting_path=arguments.job_posting,
            job_url=arguments.job_url,
            status=arguments.status,
            notes=arguments.notes
        ))
    elif arguments.command == "list":
        print_application_list(list_applications(conn))
    elif arguments.command == "show":
        application = find_application(conn, arguments.job_id)

        if application is None:
            print(f"No application found for job_id '{arguments.job_id}'.")
            return 1

        print_application(application)
    elif arguments.command == "status":
        print_application(update_status(
            conn,
            arguments.job_id,
            arguments.status,
            arguments.date_applied
        ))
    elif arguments.command == "link":
        version = load_version_metadata(arguments.version)
        print_application(link_resume_version(conn, arguments.job_id, version))

    return 0


def main():
    arguments = build_parser().parse_args()

    try:
        with open_tracker() as conn:
            return run(arguments, conn)
    except (
        DatabaseConfigError,
        DuplicateApplicationError,
        InvalidStatusError,
        ApplicationError,
        psycopg.OperationalError
    ) as error:
        print(f"Error: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
