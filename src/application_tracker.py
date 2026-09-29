"""
Application tracking rules. Database access goes through src.database;
the DOCX files themselves stay under Phase 5 versioning (reports/versions/).
"""

import json
from datetime import date
from pathlib import Path

from src import database
from src.resume_versioning import METADATA_FILENAME, make_job_id


STATUSES = (
    "Saved",
    "Tailored",
    "Applied",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
)


class InvalidStatusError(ValueError):
    pass


class ApplicationError(Exception):
    pass


class ApplicationNotFoundError(ApplicationError):
    pass


class ResumeVersionError(ApplicationError):
    pass


def open_tracker(database_name=None):
    conn = database.connect(database_name)
    database.create_schema(conn)
    return conn


def normalize_status(status):
    for valid in STATUSES:
        if str(status).strip().lower() == valid.lower():
            return valid

    raise InvalidStatusError(
        f"Invalid status '{status}'. Use one of: {', '.join(STATUSES)}"
    )


def _required(name, value):
    if value is None or not str(value).strip():
        raise ApplicationError(f"{name} is required.")

    return str(value).strip()


def _optional(value):
    if value is None or not str(value).strip():
        return None

    return str(value).strip()


def _validate_job_id(job_id):
    job_id = _required("job_id", job_id)

    # Must match the Phase 5 version folder name (reports/versions/<job_id>/).
    if make_job_id(job_id) != job_id:
        raise ApplicationError(
            f"Invalid job_id '{job_id}'. Use only letters, numbers, "
            f"'_' and '-' (for example '{make_job_id(job_id)}')."
        )

    return job_id


def add_application(
    conn,
    company,
    job_title,
    job_id,
    job_posting_path,
    job_url=None,
    date_applied=None,
    status="Saved",
    notes=None
):
    application = {
        "company": _required("company", company),
        "job_title": _required("job_title", job_title),
        "job_id": _validate_job_id(job_id),
        "job_posting_path": _required("job_posting_path", job_posting_path),
        "job_url": _optional(job_url),
        "date_applied": date_applied,
        "status": normalize_status(status),
        "notes": _optional(notes),
    }

    # Raises DuplicateApplicationError instead of replacing an existing row.
    return database.insert_application(conn, application)


def list_applications(conn):
    return database.fetch_all_applications(conn)


def get_application(conn, application_id):
    return database.fetch_application(conn, application_id)


def find_application(conn, job_id):
    return database.fetch_application_by_job_id(conn, job_id)


def _require_application(conn, job_id):
    application = find_application(conn, job_id)

    if application is None:
        raise ApplicationNotFoundError(
            f"No application found for job_id '{job_id}'."
        )

    return application


def update_status(conn, job_id, status, date_applied=None):
    status = normalize_status(status)
    application = _require_application(conn, job_id)

    if (
        status == "Applied"
        and date_applied is None
        and application["date_applied"] is None
    ):
        date_applied = date.today()

    return database.update_status(conn, job_id, status, date_applied)


def load_version_metadata(path):
    """Read Phase 5 metadata from a version folder or its metadata.json."""
    path = Path(path)

    if path.is_dir():
        path = path / METADATA_FILENAME

    if not path.is_file():
        raise ResumeVersionError(f"Resume version metadata not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def link_resume_version(conn, job_id, version):
    """
    Point an application at a Phase 5 resume version (the metadata dict
    returned by save_tailored_version). A Saved application becomes
    Tailored; any later status is kept, so tailoring never marks an
    application as Applied.
    """
    application = _require_application(conn, job_id)

    if version.get("job_id") != job_id:
        raise ResumeVersionError(
            f"Resume version belongs to job_id '{version.get('job_id')}', "
            f"not '{job_id}'."
        )

    docx_path = Path(version["tailored_docx"])

    if not docx_path.is_file():
        raise ResumeVersionError(f"Tailored resume not found: {docx_path}")

    status = application["status"]

    if status == "Saved":
        status = "Tailored"

    return database.update_resume_version(
        conn,
        job_id,
        version["version_id"],
        str(docx_path),
        status
    )
