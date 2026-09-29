from datetime import date, datetime
from pathlib import Path

import psycopg
import pytest
from docx import Document

from src import database
from src.application_tracker import (
    ApplicationError,
    ApplicationNotFoundError,
    InvalidStatusError,
    ResumeVersionError,
    add_application,
    find_application,
    get_application,
    link_resume_version,
    list_applications,
    load_version_metadata,
    normalize_status,
    update_status
)
from src.database import DuplicateApplicationError
from src.resume_versioning import save_tailored_version


def add_bmo(conn, **overrides):
    fields = {
        "company": "BMO",
        "job_title": "Software Developer Intern",
        "job_id": "BMO",
        "job_posting_path": "jobs/software_developer.txt",
    }
    fields.update(overrides)
    return add_application(conn, **fields)


def create_version(tmp_path, job_id="BMO", created_at=None):
    resume = tmp_path / "resume.docx"

    if not resume.exists():
        document = Document()
        document.add_paragraph("Experience")
        document.save(resume)

    return save_tailored_version(
        resume,
        "Experience\nBuilt a Python tool.\n",
        job_id,
        created_at=created_at or datetime(2026, 9, 29, 14, 30, 17),
        output_dir=tmp_path / "versions"
    )


# ---------- status rules (no database needed) ----------

def test_normalize_status_accepts_known_statuses_case_insensitively():
    assert normalize_status("applied") == "Applied"
    assert normalize_status(" Interview ") == "Interview"


@pytest.mark.parametrize("status", ["Submitted", "", None, "Apply"])
def test_normalize_status_rejects_unknown_statuses(status):
    with pytest.raises(InvalidStatusError):
        normalize_status(status)


# ---------- 1. creating ----------

def test_creates_application_with_defaults(conn):
    application = add_bmo(conn, job_url="https://jobs.bmo.com/123")

    assert isinstance(application["id"], int)
    assert application["company"] == "BMO"
    assert application["job_title"] == "Software Developer Intern"
    assert application["job_id"] == "BMO"
    assert application["job_posting_path"] == "jobs/software_developer.txt"
    assert application["job_url"] == "https://jobs.bmo.com/123"
    assert application["status"] == "Saved"
    assert application["date_added"] is not None


def test_rejects_missing_required_fields(conn):
    with pytest.raises(ApplicationError):
        add_bmo(conn, company="  ")


def test_rejects_job_id_that_cannot_be_a_version_folder(conn):
    with pytest.raises(ApplicationError):
        add_bmo(conn, job_id="BMO / Dev")

    assert list_applications(conn) == []


# ---------- 2. retrieving ----------

def test_retrieves_application_by_id_and_lists_all(conn):
    bmo = add_bmo(conn)
    rbc = add_bmo(conn, company="RBC", job_id="RBC")

    assert get_application(conn, bmo["id"]) == bmo
    assert get_application(conn, 9999) is None
    assert [a["job_id"] for a in list_applications(conn)] == ["BMO", "RBC"]
    assert bmo["id"] != rbc["id"]


# ---------- 3. finding by job_id ----------

def test_finds_application_by_job_id(conn):
    bmo = add_bmo(conn)

    assert find_application(conn, "BMO") == bmo
    assert find_application(conn, "TD") is None


# ---------- 4. updating status ----------

def test_updates_status(conn):
    add_bmo(conn)

    updated = update_status(conn, "BMO", "interview")

    assert updated["status"] == "Interview"
    assert find_application(conn, "BMO")["status"] == "Interview"


def test_applied_sets_date_applied_once(conn):
    add_bmo(conn)

    applied = update_status(conn, "BMO", "Applied", date(2026, 9, 20))
    assert applied["date_applied"] == date(2026, 9, 20)

    # A later status change keeps the original application date.
    interview = update_status(conn, "BMO", "Interview")
    assert interview["date_applied"] == date(2026, 9, 20)


def test_applied_without_date_defaults_to_today(conn):
    add_bmo(conn)

    assert update_status(conn, "BMO", "Applied")["date_applied"] == date.today()


def test_updating_missing_application_fails(conn):
    with pytest.raises(ApplicationNotFoundError):
        update_status(conn, "TD", "Applied")


# ---------- 5. linking a resume version ----------

def test_links_phase_5_resume_version(conn, tmp_path):
    add_bmo(conn)
    version = create_version(tmp_path)

    linked = link_resume_version(conn, "BMO", version)

    assert linked["resume_version_id"] == "20260929-143017"
    assert linked["resume_version_path"] == version["tailored_docx"]
    assert Path(linked["resume_version_path"]).is_file()
    # Tailoring moves Saved to Tailored, never to Applied.
    assert linked["status"] == "Tailored"
    assert linked["date_applied"] is None


def test_linking_keeps_later_status(conn, tmp_path):
    add_bmo(conn)
    update_status(conn, "BMO", "Applied", date(2026, 9, 20))

    linked = link_resume_version(conn, "BMO", create_version(tmp_path))

    assert linked["status"] == "Applied"
    assert linked["date_applied"] == date(2026, 9, 20)


def test_linking_newer_version_keeps_old_version_files(conn, tmp_path):
    add_bmo(conn)
    first = create_version(tmp_path)
    link_resume_version(conn, "BMO", first)

    second = create_version(tmp_path, created_at=datetime(2026, 9, 30, 9, 0, 0))
    linked = link_resume_version(conn, "BMO", second)

    assert linked["resume_version_id"] == "20260930-090000"
    assert Path(first["tailored_docx"]).is_file()


def test_links_version_loaded_from_metadata_file(conn, tmp_path):
    add_bmo(conn)
    version = create_version(tmp_path)
    version_dir = Path(version["tailored_docx"]).parent

    linked = link_resume_version(conn, "BMO", load_version_metadata(version_dir))

    assert linked["resume_version_id"] == version["version_id"]


def test_rejects_version_from_another_job(conn, tmp_path):
    add_bmo(conn)

    with pytest.raises(ResumeVersionError):
        link_resume_version(conn, "BMO", create_version(tmp_path, job_id="RBC"))

    assert find_application(conn, "BMO")["resume_version_id"] is None


def test_rejects_version_whose_docx_is_missing(conn, tmp_path):
    add_bmo(conn)
    version = create_version(tmp_path)
    Path(version["tailored_docx"]).unlink()

    with pytest.raises(ResumeVersionError):
        link_resume_version(conn, "BMO", version)


def test_linking_requires_existing_application(conn, tmp_path):
    with pytest.raises(ApplicationNotFoundError):
        link_resume_version(conn, "BMO", create_version(tmp_path))


# ---------- 6. optional fields ----------

def test_optional_fields_can_be_empty(conn):
    application = add_bmo(conn, job_url="", notes="   ")

    for field in (
        "job_url",
        "date_applied",
        "resume_version_id",
        "resume_version_path",
        "notes",
    ):
        assert application[field] is None


# ---------- 7. invalid statuses ----------

def test_rejects_invalid_status_on_create(conn):
    with pytest.raises(InvalidStatusError):
        add_bmo(conn, status="Submitted")

    assert list_applications(conn) == []


def test_rejects_invalid_status_on_update(conn):
    add_bmo(conn)

    with pytest.raises(InvalidStatusError):
        update_status(conn, "BMO", "Ghosted")

    assert find_application(conn, "BMO")["status"] == "Saved"


def test_database_also_rejects_invalid_status(conn):
    add_bmo(conn)

    with pytest.raises(psycopg.errors.CheckViolation):
        database.update_status(conn, "BMO", "Ghosted")


# ---------- 8. duplicates ----------

def test_duplicate_job_id_does_not_overwrite_existing_application(conn):
    original = add_bmo(conn, notes="first")

    with pytest.raises(DuplicateApplicationError):
        add_bmo(conn, company="Someone Else", notes="second")

    assert list_applications(conn) == [original]


# ---------- 9. persistence ----------

def test_applications_persist_across_connections(test_database, tmp_path):
    with database.connect(test_database) as first:
        add_bmo(first)
        link_resume_version(first, "BMO", create_version(tmp_path))

    with database.connect(test_database) as second:
        application = find_application(second, "BMO")

    assert application["status"] == "Tailored"
    assert application["resume_version_id"] == "20260929-143017"


def test_other_applications_stay_intact(conn, tmp_path):
    rbc = add_bmo(conn, company="RBC", job_id="RBC", notes="keep me")
    add_bmo(conn)

    update_status(conn, "BMO", "Applied")
    link_resume_version(conn, "BMO", create_version(tmp_path))

    assert find_application(conn, "RBC") == rbc
