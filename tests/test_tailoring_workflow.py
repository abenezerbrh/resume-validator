import json
from datetime import date, datetime
from pathlib import Path

import pytest
from docx import Document

from src import tailoring_workflow
from src.phase4_summary import get_final_status
from src.application_tracker import (
    ApplicationNotFoundError,
    add_application,
    find_application,
    update_status
)
from src.tailoring_workflow import (
    TailoringInputError,
    analyze,
    load_rules,
    resolve_resume_path,
    tailor_application
)


RESUME_LINES = [
    "Abenezer Balcha",
    "abenezer@example.com | 416-555-0100 | linkedin.com/in/abenezer | github.com/abenezer",
    "Education",
    "Seneca Polytechnic",
    "Experience",
    "Built a Python tool with SQL.",
    "Projects",
    "Resume validator",
    "Technical Skills",
    "Languages: Python, SQL",
]

TAILORED_TEXT = "\n".join(RESUME_LINES[:5] + [
    "Built a Python and SQL tool for the team.",
] + RESUME_LINES[6:])


def create_resume(path):
    document = Document()

    for line in RESUME_LINES:
        document.add_paragraph(line)

    document.save(path)


def stored_application(**overrides):
    record = {
        "job_id": "BMO",
        "resume_version_path": None,
    }
    record.update(overrides)
    return record


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A project-like folder so new versions are not written to the real reports/."""
    rules = load_rules()
    resume = tmp_path / "resumes" / "resume.docx"
    job = tmp_path / "jobs" / "software_developer.txt"
    resume.parent.mkdir()
    job.parent.mkdir()
    create_resume(resume)
    job.write_text("We need Python, SQL, and Docker.", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    return {"rules": rules, "resume": resume, "job": job}


@pytest.fixture
def no_claude(monkeypatch):
    """Replace both Claude calls; the tests must never run Claude."""
    calls = []

    def fake_tailor(*args):
        calls.append("tailor")
        return TAILORED_TEXT

    def fake_claims(*args):
        calls.append("claims")
        return [{"severity": "LOW", "claim": "x", "reason": "y", "original_evidence": None}]

    monkeypatch.setattr(tailoring_workflow, "tailor_resume", fake_tailor)
    monkeypatch.setattr(tailoring_workflow, "validate_claims", fake_claims)
    return calls


# ---------- resume path resolution ----------

def test_resume_path_comes_from_linked_version_metadata(tmp_path):
    version_dir = tmp_path / "versions" / "BMO" / "20260929-150218"
    version_dir.mkdir(parents=True)
    (version_dir / "metadata.json").write_text(json.dumps({
        "original_resume_path": "resumes/Abenezer_Balcha_Resume_BMO.docx"
    }), encoding="utf-8")

    application = stored_application(
        resume_version_path=str(version_dir / "resume_tailored.docx")
    )

    assert resolve_resume_path(application, "resumes/default.docx") == Path(
        "resumes/Abenezer_Balcha_Resume_BMO.docx"
    )


def test_resume_path_falls_back_to_default():
    assert resolve_resume_path(stored_application(), "resumes/default.docx") == Path(
        "resumes/default.docx"
    )


def test_resume_path_falls_back_when_version_metadata_is_missing(tmp_path):
    application = stored_application(
        resume_version_path=str(tmp_path / "gone" / "resume_tailored.docx")
    )

    assert resolve_resume_path(application, "resumes/default.docx") == Path(
        "resumes/default.docx"
    )


def test_resume_path_is_required():
    with pytest.raises(TailoringInputError, match="DEFAULT_RESUME_PATH"):
        resolve_resume_path(stored_application())


# ---------- analysis matches main.py ----------

def test_analyze_runs_phase_1_and_2(workspace):
    analysis = analyze(workspace["resume"], workspace["job"], workspace["rules"])

    assert analysis["resume_text"] == "\n".join(RESUME_LINES)
    assert "Python" in analysis["job_skills"]
    assert {r["status"] for r in analysis["resume_results"]} <= {"PASS", "FAIL"}
    assert "Docker" in analysis["comparison"]["missing_skills"]


# ---------- tailoring a tracked application ----------

def add_tracked(conn, job):
    return add_application(
        conn,
        company="BMO",
        job_title="Software Developer Intern",
        job_id="BMO",
        job_posting_path=str(job)
    )


def test_tailors_existing_application_and_links_new_version(conn, workspace, no_claude):
    add_tracked(conn, workspace["job"])
    original_bytes = workspace["resume"].read_bytes()

    result = tailor_application(
        conn, "BMO", workspace["resume"], rules=workspace["rules"]
    )

    # Phase 5 created a new version under the working folder.
    output = result["output_path"]
    assert output.is_file()
    assert output.parts[:3] == ("reports", "versions", "BMO")

    # Phase 4 ran against the new version.
    assert result["tailored_results"]
    assert result["tailored_comparison"]["matched_skills"]
    assert result["claims"][0]["severity"] == "LOW"
    assert result["final"] == get_final_status(
        result["tailored_results"], result["claims"], True
    )

    # The application now points at exactly this version, and is not Applied.
    application = find_application(conn, "BMO")
    assert application["resume_version_id"] == result["version"]["version_id"]
    assert application["resume_version_path"] == str(output)
    assert application["status"] == "Tailored"
    assert application["date_applied"] is None
    assert result["application"] == application

    assert workspace["resume"].read_bytes() == original_bytes
    assert result["original_unchanged"] is True
    assert no_claude == ["tailor", "claims"]


def test_tailoring_keeps_later_status(conn, workspace, no_claude):
    add_tracked(conn, workspace["job"])
    update_status(conn, "BMO", "Interview", date(2026, 9, 20))

    tailor_application(conn, "BMO", workspace["resume"], rules=workspace["rules"])

    application = find_application(conn, "BMO")
    assert application["status"] == "Interview"
    assert application["date_applied"] == date(2026, 9, 20)


def test_retailoring_uses_resume_from_linked_version(
    conn, workspace, no_claude, monkeypatch
):
    # Two runs within one second would share a version ID, which Phase 5
    # refuses to overwrite, so give each run its own timestamp.
    times = iter([datetime(2026, 9, 29, 15, 0, 0), datetime(2026, 9, 29, 15, 5, 0)])
    save = tailoring_workflow.save_tailored_version
    monkeypatch.setattr(
        tailoring_workflow,
        "save_tailored_version",
        lambda *args: save(*args, created_at=next(times))
    )
    add_tracked(conn, workspace["job"])
    tailor_application(conn, "BMO", workspace["resume"], rules=workspace["rules"])

    # No default resume this time: it is found through the linked version.
    result = tailor_application(conn, "BMO", rules=workspace["rules"])

    assert result["resume_path"] == workspace["resume"]


def test_unknown_application_is_not_created(conn, workspace, no_claude):
    with pytest.raises(ApplicationNotFoundError):
        tailor_application(conn, "BMO", workspace["resume"], rules=workspace["rules"])

    assert find_application(conn, "BMO") is None
    assert no_claude == []


def test_missing_resume_file_stops_before_claude(conn, workspace, no_claude):
    add_tracked(conn, workspace["job"])

    with pytest.raises(TailoringInputError, match="Resume file not found"):
        tailor_application(
            conn, "BMO", workspace["resume"].with_name("missing.docx"),
            rules=workspace["rules"]
        )

    assert no_claude == []


def test_missing_job_posting_stops_before_claude(conn, workspace, no_claude):
    add_tracked(conn, workspace["job"])
    workspace["job"].unlink()

    with pytest.raises(TailoringInputError, match="Job posting not found"):
        tailor_application(conn, "BMO", workspace["resume"], rules=workspace["rules"])

    assert no_claude == []
    assert find_application(conn, "BMO")["status"] == "Saved"
