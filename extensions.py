
import os
import sqlite3

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

JOURNAL_MODES = {"WAL", "DELETE", "TRUNCATE"}


def sqlite_journal_mode():
    """Journal mode from SQLITE_JOURNAL_MODE (default WAL).

    WAL needs shared memory, which network file systems (such as the
    storage on PythonAnywhere) do not provide reliably; set DELETE there.
    """
    mode = os.environ.get("SQLITE_JOURNAL_MODE", "WAL").strip().upper()
    return mode if mode in JOURNAL_MODES else "WAL"


@event.listens_for(Engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    """Per-connection SQLite settings."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()

        # SQLite ignores foreign keys unless this is set on every connection.
        cursor.execute("PRAGMA foreign_keys=ON")

        # Write-ahead logging by default: faster commits, and pages can still
        # read while a student's attendance is being written. The setting is
        # stored in the database file. In-memory test databases ignore it.
        # The value comes from a fixed allow-list, never raw user input.
        cursor.execute(f"PRAGMA journal_mode={sqlite_journal_mode()}")

        # Wait up to 5 seconds for another writer instead of failing at once
        # with "database is locked" when many students submit together.
        cursor.execute("PRAGMA busy_timeout=5000")

        cursor.close()
