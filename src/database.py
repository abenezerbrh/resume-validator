"""
PostgreSQL access for the application tracker.

All SQL lives in this module. Connection settings come from environment
variables (optionally loaded from a local .env file):

    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import errors
from psycopg.rows import dict_row


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

REQUIRED_SETTINGS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")

# Keep in sync with application_tracker.STATUSES.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS applications (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company             TEXT NOT NULL,
    job_title           TEXT NOT NULL,
    job_id              TEXT NOT NULL UNIQUE,
    job_posting_path    TEXT NOT NULL,
    job_url             TEXT,
    date_added          TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_applied        DATE,
    status              TEXT NOT NULL DEFAULT 'Saved'
        CHECK (status IN (
            'Saved', 'Tailored', 'Applied', 'Interview',
            'Offer', 'Rejected', 'Withdrawn'
        )),
    resume_version_id   TEXT,
    resume_version_path TEXT,
    notes               TEXT
)
"""

INSERT_COLUMNS = (
    "company",
    "job_title",
    "job_id",
    "job_posting_path",
    "job_url",
    "date_applied",
    "status",
    "notes",
)


def load_env():
    # Real environment variables take priority over the .env file.
    load_dotenv(ENV_FILE)


class DatabaseConfigError(Exception):
    pass


class DuplicateApplicationError(Exception):
    pass


def get_db_settings(database=None):
    load_env()

    missing = [name for name in REQUIRED_SETTINGS if not os.environ.get(name)]

    if missing:
        raise DatabaseConfigError(
            "Missing database settings: " + ", ".join(missing)
            + ". Set them in your environment or in a .env file "
            "(see README.md)."
        )

    return {
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
        "dbname": database or os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def connect(database=None):
    # Autocommit: every function below is a single statement, so each
    # write is its own transaction.
    return psycopg.connect(
        **get_db_settings(database),
        autocommit=True,
        row_factory=dict_row
    )


def create_schema(conn):
    conn.execute(SCHEMA_SQL)


def insert_application(conn, application):
    values = [application.get(column) for column in INSERT_COLUMNS]
    placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))

    try:
        return conn.execute(
            f"INSERT INTO applications ({', '.join(INSERT_COLUMNS)}) "
            f"VALUES ({placeholders}) RETURNING *",
            values
        ).fetchone()
    except errors.UniqueViolation:
        raise DuplicateApplicationError(
            f"An application for job_id '{application.get('job_id')}' "
            "already exists."
        ) from None


def fetch_application(conn, application_id):
    return conn.execute(
        "SELECT * FROM applications WHERE id = %s",
        (application_id,)
    ).fetchone()


def fetch_application_by_job_id(conn, job_id):
    return conn.execute(
        "SELECT * FROM applications WHERE job_id = %s",
        (job_id,)
    ).fetchone()


def fetch_all_applications(conn):
    return conn.execute(
        "SELECT * FROM applications ORDER BY date_added, id"
    ).fetchall()


def update_status(conn, job_id, status, date_applied=None):
    # A date is only replaced when a new one is given.
    return conn.execute(
        "UPDATE applications "
        "SET status = %s, date_applied = COALESCE(%s, date_applied) "
        "WHERE job_id = %s RETURNING *",
        (status, date_applied, job_id)
    ).fetchone()


def update_resume_version(conn, job_id, version_id, version_path, status):
    return conn.execute(
        "UPDATE applications "
        "SET resume_version_id = %s, resume_version_path = %s, status = %s "
        "WHERE job_id = %s RETURNING *",
        (version_id, version_path, status, job_id)
    ).fetchone()
