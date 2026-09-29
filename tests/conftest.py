import os

import psycopg
import pytest
from psycopg import sql

from src import database
from src.database import DatabaseConfigError


# ---------- test database setup ----------
# Tests only ever use TEST_DB_NAME, never the real DB_NAME database.

def get_test_database_name():
    database.load_env()
    test_database = os.environ.get("TEST_DB_NAME")

    if not test_database:
        pytest.skip("TEST_DB_NAME is not set; skipping PostgreSQL tests.")

    if test_database == os.environ.get("DB_NAME"):
        pytest.fail("TEST_DB_NAME must be different from DB_NAME.")

    return test_database


def ensure_test_database(test_database):
    with database.connect("postgres") as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (test_database,)
        ).fetchone()

        if not exists:
            conn.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(test_database)
                )
            )


@pytest.fixture
def test_database():
    test_database = get_test_database_name()

    try:
        ensure_test_database(test_database)
    except (DatabaseConfigError, psycopg.OperationalError) as error:
        pytest.skip(f"PostgreSQL is not available: {error}")

    with database.connect(test_database) as conn:
        current = conn.execute("SELECT current_database() AS name").fetchone()
        assert current["name"] == test_database

        database.create_schema(conn)
        conn.execute("TRUNCATE applications RESTART IDENTITY")

    return test_database


@pytest.fixture
def conn(test_database):
    with database.connect(test_database) as conn:
        yield conn
