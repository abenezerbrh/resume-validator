import asyncio
import logging
from datetime import date, datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from telegram.error import NetworkError
from telegram.ext import CommandHandler

from src import telegram_bot
from src.application_tracker import (
    ApplicationNotFoundError,
    InvalidStatusError,
    normalize_status
)
from src.claude_tailer import ClaudeTailorError
from src.docx_generator import DocxGenerationError
from src.tailoring_workflow import TailoringInputError
from src.telegram_bot import (
    BotConfigError,
    RedactingFormatter,
    applications_command,
    build_application,
    error_handler,
    load_bot_config,
    plain_text,
    show_command,
    start_command,
    status_command,
    tailor_command,
    unknown_command
)


ALLOWED_USER = 1001
STRANGER = 2002
FAKE_TOKEN = "123456:TEST-TOKEN-not-real"


def application(job_id="BMO", company="BMO", status="Tailored", **extra):
    record = {
        "id": 1,
        "company": company,
        "job_title": "Software Developer Intern",
        "job_id": job_id,
        "job_posting_path": "jobs/software_developer.txt",
        "job_url": None,
        "date_added": datetime(2026, 9, 29, 15, 1, tzinfo=timezone.utc),
        "date_applied": None,
        "status": status,
        "resume_version_id": None,
        "resume_version_path": None,
        "notes": None,
    }
    record.update(extra)
    return record


class FakeMessage:
    def __init__(self, fail_document=False):
        self.replies = []
        self.documents = []
        self.fail_document = fail_document

    async def reply_text(self, text):
        self.replies.append(text)

    async def reply_document(self, document, filename, caption=None):
        if self.fail_document:
            raise NetworkError("upload failed")

        self.documents.append({
            "filename": filename,
            "content": document.read(),
            "caption": caption,
        })

    @property
    def text(self):
        return "\n".join(self.replies)


class FakeServices:
    """Stands in for TrackerServices; records calls instead of using the database."""

    def __init__(self, applications=(), error=None, tailor_result=None):
        self.applications = {a["job_id"]: a for a in applications}
        self.error = error
        self.tailor_result = tailor_result
        self.calls = []

    def _call(self, name, *args):
        self.calls.append((name, *args))

        if self.error:
            raise self.error

    def list_applications(self):
        self._call("list_applications")
        return list(self.applications.values())

    def find_application(self, job_id):
        self._call("find_application", job_id)
        return self.applications.get(job_id)

    def update_status(self, job_id, status):
        self._call("update_status", job_id, status)

        if job_id not in self.applications:
            raise ApplicationNotFoundError(
                f"No application found for job_id '{job_id}'."
            )

        updated = dict(self.applications[job_id], status=normalize_status(status))
        self.applications[job_id] = updated
        return updated

    def tailor_application(self, job_id):
        self._call("tailor_application", job_id)

        if job_id not in self.applications:
            raise ApplicationNotFoundError(
                f"No application found for job_id '{job_id}'."
            )

        return self.tailor_result


def run(handler, services, args=(), user_id=ALLOWED_USER, message=None):
    message = message or FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message
    )
    context = SimpleNamespace(
        args=list(args),
        bot_data={
            "allowed_user_id": ALLOWED_USER,
            "services": services,
            "tailor_lock": asyncio.Lock(),
        }
    )

    asyncio.run(handler(update, context))
    return message


def tailoring_result(tmp_path):
    docx_path = tmp_path / "Abenezer_Balcha_Resume_BMO_tailored.docx"
    docx_path.write_bytes(b"fake docx bytes")

    return {
        "application": application(
            resume_version_id="20260929-150218",
            resume_version_path=str(docx_path)
        ),
        "version": {"job_id": "BMO", "version_id": "20260929-150218"},
        "output_path": docx_path,
        "tailored_results": [{"status": "PASS"}] * 9,
        "tailored_comparison": {
            "matched_skills": [{"skill": f"s{i}"} for i in range(8)],
            "missing_skills": ["a", "b", "c"],
        },
        "claims": [{"severity": "MEDIUM"}, {"severity": "LOW"}],
        "claim_error": None,
        "original_unchanged": True,
        "final": {
            "status": "REVIEW REQUIRED",
            "reasons": ["1 MEDIUM/HIGH potential unsupported claim(s)"],
        },
    }


# ---------- /start ----------

def test_start_explains_bot_and_lists_commands():
    message = run(start_command, FakeServices())

    for command in ("/applications", "/show", "/status", "/tailor"):
        assert command in message.text

    assert "Applied" in message.text


# ---------- access control ----------

@pytest.mark.parametrize("handler, args", [
    (start_command, []),
    (applications_command, []),
    (show_command, ["BMO"]),
    (status_command, ["BMO", "Applied"]),
    (tailor_command, ["BMO"]),
    (unknown_command, []),
    (plain_text, []),
])
def test_unauthorized_user_is_denied_and_nothing_runs(handler, args):
    services = FakeServices([application()])

    message = run(handler, services, args, user_id=STRANGER)

    assert message.replies == ["Access denied."]
    assert services.calls == []


def test_missing_user_is_denied():
    services = FakeServices([application()])
    message = FakeMessage()
    update = SimpleNamespace(effective_user=None, effective_message=message)
    context = SimpleNamespace(args=[], bot_data={
        "allowed_user_id": ALLOWED_USER,
        "services": services,
    })

    asyncio.run(applications_command(update, context))

    assert message.replies == ["Access denied."]
    assert services.calls == []


# ---------- /applications ----------

def test_applications_lists_tracked_jobs_and_statuses():
    services = FakeServices([
        application(),
        application(job_id="RBC", company="RBC", status="Applied"),
    ])

    message = run(applications_command, services)

    assert message.text == (
        "BMO\nBMO - Software Developer Intern\nStatus: Tailored\n\n"
        "RBC\nRBC - Software Developer Intern\nStatus: Applied"
    )


def test_applications_when_none_are_tracked():
    message = run(applications_command, FakeServices())

    assert "No applications tracked yet." in message.text


def test_long_application_list_is_split_into_telegram_sized_messages():
    services = FakeServices([
        application(job_id=f"JOB{i}", company="X" * 200) for i in range(40)
    ])

    message = run(applications_command, services)

    assert len(message.replies) > 1
    assert all(len(reply) <= 4096 for reply in message.replies)


# ---------- /show ----------

def test_show_returns_application_details():
    services = FakeServices([application(
        status="Applied",
        date_applied=date(2026, 9, 30),
        resume_version_id="20260929-150218",
        resume_version_path="reports/versions/BMO/20260929-150218/r.docx",
        notes="Referred by a friend",
    )])

    message = run(show_command, services, ["BMO"])

    assert services.calls == [("find_application", "BMO")]
    for expected in (
        "BMO - Software Developer Intern",
        "Status: Applied",
        "Date added: 2026-09-29",
        "Date applied: 2026-09-30",
        "Resume version: 20260929-150218",
        "Notes: Referred by a friend",
    ):
        assert expected in message.text


def test_show_leaves_out_empty_optional_fields():
    message = run(show_command, FakeServices([application()]), ["BMO"])

    for label in ("Date applied", "Resume version", "Notes", "Job URL"):
        assert label not in message.text


def test_show_requires_job_id():
    services = FakeServices([application()])

    message = run(show_command, services)

    assert "Missing job_id" in message.text
    assert services.calls == []


def test_show_unknown_application():
    message = run(show_command, FakeServices(), ["TD"])

    assert "No application found for job_id 'TD'" in message.text


# ---------- /status ----------

def test_status_updates_application_through_tracker():
    services = FakeServices([application()])

    message = run(status_command, services, ["BMO", "applied"])

    assert services.calls == [("update_status", "BMO", "applied")]
    assert "status is now Applied" in message.text


def test_status_rejects_invalid_status_before_touching_database():
    services = FakeServices([application()])

    message = run(status_command, services, ["BMO", "Ghosted"])

    assert "Invalid status 'Ghosted'" in message.text
    assert "Saved, Tailored, Applied" in message.text
    assert services.calls == []


@pytest.mark.parametrize("args", [[], ["BMO"], ["BMO", "Applied", "extra"]])
def test_status_requires_job_id_and_status(args):
    services = FakeServices([application()])

    message = run(status_command, services, args)

    assert "Usage: /status <job_id> <status>" in message.text
    assert services.calls == []


def test_status_for_unknown_application():
    message = run(status_command, FakeServices(), ["TD", "Applied"])

    assert "No application found for job_id 'TD'" in message.text
    assert "/applications" in message.text


def test_status_error_from_tracker_is_shown():
    services = FakeServices([application()], error=InvalidStatusError("Invalid status 'x'."))

    message = run(status_command, services, ["BMO", "Applied"])

    assert message.text == "Invalid status 'x'."


# ---------- /tailor ----------

def test_tailor_sends_summary_then_docx(tmp_path):
    result = tailoring_result(tmp_path)
    services = FakeServices([application()], tailor_result=result)

    message = run(tailor_command, services, ["BMO"])

    assert services.calls == [("tailor_application", "BMO")]
    assert message.replies[0].startswith("Tailoring your resume for BMO")

    summary = message.replies[1]
    for expected in (
        "BMO - Software Developer Intern",
        "Resume tailored successfully.",
        "Version: 20260929-150218",
        "Application status: Tailored",
        "9 passed, 0 failed",
        "8 matched",
        "3 missing",
        "2 potential claims",
        "1 MEDIUM",
        "1 LOW",
        "⚠ REVIEW REQUIRED",
    ):
        assert expected in summary

    assert message.documents == [{
        "filename": "Abenezer_Balcha_Resume_BMO_tailored.docx",
        "content": b"fake docx bytes",
        "caption": "Tailored resume. Review it before using it.",
    }]


def test_tailor_summary_when_ready_and_claims_unchecked(tmp_path):
    result = tailoring_result(tmp_path)
    result["claims"] = None
    result["final"] = {"status": "READY FOR REVIEW", "reasons": []}
    services = FakeServices([application()], tailor_result=result)

    summary = run(tailor_command, services, ["BMO"]).replies[1]

    assert "Could not be completed" in summary
    assert "✓ READY FOR REVIEW" in summary


def test_tailor_requires_job_id():
    services = FakeServices([application()])

    message = run(tailor_command, services)

    assert "Missing job_id" in message.text
    assert services.calls == []


def test_tailor_unknown_application_does_not_create_one():
    services = FakeServices()

    message = run(tailor_command, services, ["TD"])

    assert "No application found for job_id 'TD'" in message.text
    assert services.applications == {}
    assert message.documents == []


@pytest.mark.parametrize("error, expected", [
    (ClaudeTailorError("claude exploded: details"), "Claude could not tailor the resume"),
    (DocxGenerationError("bad template"), "The tailored DOCX could not be created."),
    (TailoringInputError("Resume file not found: resumes/x.docx"), "Resume file not found: resumes/x.docx"),
    (TailoringInputError("Job posting not found: jobs/x.txt"), "Job posting not found: jobs/x.txt"),
])
def test_tailor_failures_send_friendly_message_and_no_docx(error, expected):
    services = FakeServices([application()], error=error)

    message = run(tailor_command, services, ["BMO"])

    assert expected in message.replies[-1]
    assert message.documents == []


def test_tailor_is_refused_while_another_run_is_in_progress():
    services = FakeServices([application()])
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=ALLOWED_USER),
        effective_message=message
    )

    async def scenario():
        lock = asyncio.Lock()
        context = SimpleNamespace(args=["BMO"], bot_data={
            "allowed_user_id": ALLOWED_USER,
            "services": services,
            "tailor_lock": lock,
        })

        async with lock:
            await tailor_command(update, context)

    asyncio.run(scenario())

    assert "already in progress" in message.text
    assert services.calls == []


def test_docx_send_failure_reports_saved_path(tmp_path):
    result = tailoring_result(tmp_path)
    services = FakeServices([application()], tailor_result=result)

    message = run(
        tailor_command, services, ["BMO"],
        message=FakeMessage(fail_document=True)
    )

    assert "could not be sent through Telegram" in message.replies[-1]
    assert str(result["output_path"]) in message.replies[-1]


# ---------- error handling ----------

def test_database_failure_hides_connection_details():
    error = psycopg.OperationalError(
        'connection failed: password="super-secret" user=postgres'
    )

    message = run(applications_command, FakeServices(error=error))

    assert message.text == (
        "Could not reach the database. Check that PostgreSQL is running."
    )
    assert "super-secret" not in message.text


def test_unexpected_error_is_generic():
    message = run(show_command, FakeServices(error=RuntimeError("stack details")), ["BMO"])

    assert message.text == "Something went wrong. Check the bot logs for details."


def test_unknown_command():
    message = run(unknown_command, FakeServices())

    assert "Unknown command" in message.text
    assert "/start" in message.text


def test_plain_text_points_to_start():
    message = run(plain_text, FakeServices())

    assert "/start" in message.text


def test_error_handler_replies_without_details(monkeypatch):
    monkeypatch.setattr(telegram_bot, "Update", SimpleNamespace)
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(error=RuntimeError(f"token {FAKE_TOKEN}"))

    asyncio.run(error_handler(update, context))

    assert message.replies == ["Something went wrong. Please try again."]


# ---------- configuration and logging ----------

@pytest.fixture
def bot_env(monkeypatch):
    # Ignore the real .env so tests do not depend on local settings.
    monkeypatch.setattr(telegram_bot, "load_env", lambda: None)

    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "DEFAULT_RESUME_PATH"):
        monkeypatch.delenv(name, raising=False)

    return monkeypatch


def test_config_is_read_from_environment(bot_env):
    bot_env.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    bot_env.setenv("TELEGRAM_ALLOWED_USER_ID", str(ALLOWED_USER))

    config = load_bot_config()

    assert config == {
        "token": FAKE_TOKEN,
        "allowed_user_id": ALLOWED_USER,
        "default_resume_path": None,
    }


def test_missing_config_names_settings_without_values(bot_env):
    bot_env.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)

    with pytest.raises(BotConfigError) as error:
        load_bot_config()

    assert "TELEGRAM_ALLOWED_USER_ID" in str(error.value)
    assert FAKE_TOKEN not in str(error.value)


def test_non_numeric_user_id_is_rejected(bot_env):
    bot_env.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    bot_env.setenv("TELEGRAM_ALLOWED_USER_ID", "@me")

    with pytest.raises(BotConfigError):
        load_bot_config()


def test_log_lines_never_contain_the_token():
    formatter = RedactingFormatter([FAKE_TOKEN], "%(message)s")
    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1,
        "POST https://api.telegram.org/bot%s/getMe", (FAKE_TOKEN,), None
    )

    line = formatter.format(record)

    assert FAKE_TOKEN not in line
    assert "[REDACTED]" in line


def test_build_application_registers_commands_without_network():
    services = FakeServices()
    app = build_application(
        {"token": FAKE_TOKEN, "allowed_user_id": ALLOWED_USER},
        services
    )

    commands = set()
    for handler in app.handlers[0]:
        if isinstance(handler, CommandHandler):
            commands |= handler.commands

    assert {"start", "applications", "show", "status", "tailor"} <= commands
    assert app.bot_data["allowed_user_id"] == ALLOWED_USER
    assert app.bot_data["services"] is services
