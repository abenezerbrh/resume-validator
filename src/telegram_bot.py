"""
Telegram interface for the Resume Validator.

This module only parses commands, checks who is asking, calls the existing
application logic, and formats replies. Tracking rules, database access,
tailoring, versioning, and validation all live in the existing modules.
"""

import asyncio
import functools
import logging
import os
from pathlib import Path

import psycopg
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

from src import application_tracker as tracker
from src.application_tracker import ApplicationError, InvalidStatusError, STATUSES
from src.claude_tailer import ClaudeTailorError
from src.database import DatabaseConfigError, load_env
from src.docx_generator import DocxGenerationError
from src.phase4_summary import READY_FOR_REVIEW
from src.resume_versioning import VersionExistsError
from src.tailoring_workflow import DEFAULT_RESUME_SETTING, tailor_application


TOKEN_SETTING = "TELEGRAM_BOT_TOKEN"
ALLOWED_USER_SETTING = "TELEGRAM_ALLOWED_USER_ID"

# Telegram rejects messages longer than 4096 characters.
MAX_MESSAGE_LENGTH = 4000

logger = logging.getLogger(__name__)


class BotConfigError(Exception):
    pass


def load_bot_config():
    """Read bot settings. Error messages name settings, never their values."""
    load_env()

    token = os.environ.get(TOKEN_SETTING, "").strip()
    allowed_user = os.environ.get(ALLOWED_USER_SETTING, "").strip()

    missing = [
        name for name, value in (
            (TOKEN_SETTING, token),
            (ALLOWED_USER_SETTING, allowed_user),
        )
        if not value
    ]

    if missing:
        raise BotConfigError(
            "Missing Telegram settings: " + ", ".join(missing)
            + ". Add them to your environment or .env file."
        )

    if not allowed_user.isdigit():
        raise BotConfigError(
            f"{ALLOWED_USER_SETTING} must be a numeric Telegram user ID."
        )

    return {
        "token": token,
        "allowed_user_id": int(allowed_user),
        "default_resume_path": os.environ.get(DEFAULT_RESUME_SETTING) or None,
    }


class TrackerServices:
    """
    Thin adapter from the bot to the existing application logic. Each call
    opens its own database connection. Tests replace this with a fake.
    """

    def __init__(self, database_name=None, default_resume_path=None):
        self.database_name = database_name
        self.default_resume_path = default_resume_path

    def list_applications(self):
        with tracker.open_tracker(self.database_name) as conn:
            return tracker.list_applications(conn)

    def find_application(self, job_id):
        with tracker.open_tracker(self.database_name) as conn:
            return tracker.find_application(conn, job_id)

    def update_status(self, job_id, status):
        with tracker.open_tracker(self.database_name) as conn:
            return tracker.update_status(conn, job_id, status)

    def tailor_application(self, job_id):
        with tracker.open_tracker(self.database_name) as conn:
            return tailor_application(conn, job_id, self.default_resume_path)


# ---------- formatting ----------

HELP_TEXT = (
    "Resume Validator bot\n"
    "\n"
    "Track your job applications and tailor your resume for them.\n"
    "\n"
    "Commands:\n"
    "/applications - list tracked applications\n"
    "/show <job_id> - show one application\n"
    "/status <job_id> <status> - update an application's status\n"
    "/tailor <job_id> - tailor your resume for an application\n"
    "/start - show this help\n"
    "\n"
    f"Statuses: {', '.join(STATUSES)}"
)


def _date(value):
    return value.strftime("%Y-%m-%d") if value else None


def format_application_list(applications):
    if not applications:
        return (
            "No applications tracked yet.\n"
            "Add one on your computer with: python tracker.py add ..."
        )

    blocks = [
        f"{application['job_id']}\n"
        f"{application['company']} - {application['job_title']}\n"
        f"Status: {application['status']}"
        for application in applications
    ]

    return "\n\n".join(blocks)


def format_application(application):
    lines = [
        f"{application['company']} - {application['job_title']}",
        "",
        f"Job ID: {application['job_id']}",
        f"Status: {application['status']}",
        f"Date added: {_date(application['date_added'])}",
    ]

    optional = (
        ("Date applied", _date(application["date_applied"])),
        ("Resume version", application["resume_version_id"]),
        ("Resume file", application["resume_version_path"]),
        ("Job URL", application["job_url"]),
        ("Notes", application["notes"]),
    )

    lines.extend(f"{label}: {value}" for label, value in optional if value)

    return "\n".join(lines)


def format_tailoring_summary(result):
    application = result["application"]
    comparison = result["tailored_comparison"]
    claims = result["claims"]
    final = result["final"]

    lines = [
        f"{application['company']} - {application['job_title']}",
        "",
        "Resume tailored successfully.",
        f"Version: {result['version']['version_id']}",
        f"Application status: {application['status']}",
        "",
        "Resume validation:",
        _validation_counts(result["tailored_results"]),
        "",
        "Job alignment:",
        f"{len(comparison['matched_skills'])} matched",
        f"{len(comparison['missing_skills'])} missing",
        "",
        "Claim validation:",
    ]

    if claims is None:
        lines.append("Could not be completed")
    else:
        lines.append(f"{len(claims)} potential claims")

        for severity in ("HIGH", "MEDIUM", "LOW"):
            count = sum(1 for claim in claims if claim["severity"] == severity)

            if count:
                lines.append(f"{count} {severity}")

    lines += ["", "Final status:"]

    if final["status"] == READY_FOR_REVIEW:
        lines.append(f"✓ {final['status']}")
    else:
        lines.append(f"⚠ {final['status']}")
        lines.extend(f"- {reason}" for reason in final["reasons"])

    if not result["original_unchanged"]:
        lines += ["", "Warning: the original resume was modified."]

    return "\n".join(lines)


def _validation_counts(results):
    passed = sum(1 for result in results if result["status"] == "PASS")
    return f"{passed} passed, {len(results) - passed} failed"


def friendly_error(error):
    """A short message that is safe to show in Telegram."""
    if isinstance(error, (InvalidStatusError, ApplicationError)):
        # These messages come from our own code and contain no secrets.
        message = str(error)

        if isinstance(error, tracker.ApplicationNotFoundError):
            message += " Use /applications to see tracked jobs."

        return message

    if isinstance(error, DatabaseConfigError):
        return "The database is not configured. Check the DB_ settings in .env."

    if isinstance(error, psycopg.Error):
        return "Could not reach the database. Check that PostgreSQL is running."

    if isinstance(error, ClaudeTailorError):
        return "Claude could not tailor the resume. Please try again later."

    if isinstance(error, DocxGenerationError):
        return "The tailored DOCX could not be created."

    if isinstance(error, VersionExistsError):
        return "A resume version was just created. Try again in a moment."

    return "Something went wrong. Check the bot logs for details."


# ---------- handlers ----------

def restricted(handler):
    """Only the configured Telegram user may use the bot."""

    @functools.wraps(handler)
    async def wrapper(update, context):
        user = update.effective_user

        if user is None or user.id != context.bot_data["allowed_user_id"]:
            logger.warning(
                "Denied access to Telegram user %s",
                user.id if user else "unknown"
            )

            if update.effective_message:
                await update.effective_message.reply_text("Access denied.")

            return

        await handler(update, context)

    return wrapper


async def reply_long(message, text):
    for start in range(0, len(text), MAX_MESSAGE_LENGTH):
        await message.reply_text(text[start:start + MAX_MESSAGE_LENGTH])


async def call_service(message, function, *args):
    """
    Run blocking application logic off the event loop. Returns (result, ok);
    on failure the user gets a safe message and the details go to the log.
    """
    try:
        return await asyncio.to_thread(function, *args), True
    except (InvalidStatusError, ApplicationError) as error:
        await message.reply_text(friendly_error(error))
    except Exception as error:
        logger.error(
            "%s failed: %s",
            getattr(function, "__name__", "service call"),
            type(error).__name__,
            exc_info=not isinstance(error, (psycopg.Error, ClaudeTailorError))
        )
        await message.reply_text(friendly_error(error))

    return None, False


@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_TEXT)


@restricted
async def applications_command(update, context):
    services = context.bot_data["services"]
    applications, ok = await call_service(
        update.effective_message,
        services.list_applications
    )

    if ok:
        await reply_long(
            update.effective_message,
            format_application_list(applications)
        )


def _job_id_argument(context, usage):
    if not context.args:
        return None, f"Missing job_id. Usage: {usage}"

    if len(context.args) > 1:
        return None, f"Too many arguments. Usage: {usage}"

    return context.args[0], None


@restricted
async def show_command(update, context):
    message = update.effective_message
    job_id, problem = _job_id_argument(context, "/show <job_id>")

    if problem:
        await message.reply_text(problem)
        return

    services = context.bot_data["services"]
    application, ok = await call_service(message, services.find_application, job_id)

    if not ok:
        return

    if application is None:
        await message.reply_text(
            f"No application found for job_id '{job_id}'. "
            "Use /applications to see tracked jobs."
        )
        return

    await message.reply_text(format_application(application))


@restricted
async def status_command(update, context):
    message = update.effective_message
    usage = "/status <job_id> <status>"

    if len(context.args) != 2:
        await message.reply_text(
            f"Usage: {usage}\nStatuses: {', '.join(STATUSES)}"
        )
        return

    job_id, status = context.args

    try:
        tracker.normalize_status(status)
    except InvalidStatusError as error:
        await message.reply_text(friendly_error(error))
        return

    services = context.bot_data["services"]
    application, ok = await call_service(
        message,
        services.update_status,
        job_id,
        status
    )

    if ok:
        await message.reply_text(
            f"Updated {application['job_id']}: status is now "
            f"{application['status']}.\n\n{format_application(application)}"
        )


@restricted
async def tailor_command(update, context):
    message = update.effective_message
    job_id, problem = _job_id_argument(context, "/tailor <job_id>")

    if problem:
        await message.reply_text(problem)
        return

    lock = context.bot_data["tailor_lock"]

    if lock.locked():
        await message.reply_text(
            "A tailoring run is already in progress. Please wait for it to finish."
        )
        return

    async with lock:
        await message.reply_text(
            f"Tailoring your resume for {job_id}. This can take a few minutes..."
        )

        services = context.bot_data["services"]
        result, ok = await call_service(
            message,
            services.tailor_application,
            job_id
        )

        if not ok:
            return

        await message.reply_text(format_tailoring_summary(result))
        await send_tailored_docx(message, result["output_path"])


async def send_tailored_docx(message, path):
    path = Path(path)

    try:
        with path.open("rb") as document:
            await message.reply_document(
                document=document,
                filename=path.name,
                caption="Tailored resume. Review it before using it."
            )
    except (OSError, TelegramError) as error:
        logger.error("Sending the tailored DOCX failed: %s", type(error).__name__)
        await message.reply_text(
            f"The tailored resume was saved to {path}, "
            "but it could not be sent through Telegram."
        )


@restricted
async def unknown_command(update, context):
    await update.effective_message.reply_text(
        "Unknown command. Send /start to see the available commands."
    )


@restricted
async def plain_text(update, context):
    await update.effective_message.reply_text(
        "Send /start to see the available commands."
    )


async def error_handler(update, context):
    # Log the error type only; never echo details (which could include
    # configuration) back to Telegram.
    logger.error(
        "Unhandled bot error: %s",
        type(context.error).__name__,
        exc_info=context.error
    )

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Something went wrong. Please try again."
            )
        except TelegramError:
            pass


# ---------- setup ----------

class RedactingFormatter(logging.Formatter):
    """Removes secrets (such as the bot token) from every log line."""

    def __init__(self, secrets, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.secrets = [secret for secret in secrets if secret]

    def format(self, record):
        text = super().format(record)

        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")

        return text


def configure_logging(secrets):
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(
        secrets,
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))

    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    # httpx logs every request URL, and Telegram URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_application(config, services=None):
    application = Application.builder().token(config["token"]).build()

    application.bot_data.update({
        "allowed_user_id": config["allowed_user_id"],
        "services": services or TrackerServices(
            default_resume_path=config.get("default_resume_path")
        ),
        "tailor_lock": asyncio.Lock(),
    })

    application.add_handler(CommandHandler(["start", "help"], start_command))
    application.add_handler(CommandHandler("applications", applications_command))
    application.add_handler(CommandHandler("show", show_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("tailor", tailor_command))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text)
    )
    application.add_error_handler(error_handler)

    return application
