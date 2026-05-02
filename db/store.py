"""SQLite storage for classified emails."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path("emails.db")

CREATE_EMAILS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    subject TEXT,
    sender TEXT,
    date TEXT,
    body TEXT,
    label TEXT,
    confidence REAL,
    created_at TIMESTAMP,
    priority_tag TEXT
)
"""

REQUIRED_COLUMNS: dict[str, str] = {
    "sender": "TEXT",
    "date": "TEXT",
    "body": "TEXT",
    "label": "TEXT",
    "confidence": "REAL",
    "created_at": "TIMESTAMP",
    "priority_tag": "TEXT",
}

UPSERT_EMAIL_SQL = """
INSERT INTO emails (id, subject, sender, date, body, label, confidence, created_at, priority_tag)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    subject = excluded.subject,
    sender = excluded.sender,
    date = excluded.date,
    body = excluded.body,
    label = excluded.label,
    confidence = excluded.confidence,
    priority_tag = excluded.priority_tag
"""

SELECT_ALL_EMAILS_SQL = """
SELECT id, subject, sender, date, body, label, confidence, created_at, priority_tag
FROM emails
ORDER BY created_at DESC
"""

SELECT_EMAILS_BY_LABEL_SQL = """
SELECT id, subject, sender, date, body, label, confidence, created_at, priority_tag
FROM emails
WHERE label = ?
ORDER BY created_at DESC
"""


def _connect() -> sqlite3.Connection:
    """Open a SQLite connection with Row output."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    """Create the emails table if it does not exist."""
    with _connect() as connection:
        connection.execute(CREATE_EMAILS_TABLE_SQL)
        _ensure_columns(connection)
        connection.commit()


def save_email(email_dict: dict[str, Any]) -> None:
    """Save one classified email.

    Args:
        email_dict: Email dictionary with id, subject, sender, date, body,
            label, and confidence.
    """
    init_db()
    with _connect() as connection:
        connection.execute(UPSERT_EMAIL_SQL, _email_row(email_dict))
        connection.commit()


def save_emails(emails: list[dict[str, Any]]) -> int:
    """Save many classified emails.

    Args:
        emails: Classified email dictionaries.

    Returns:
        Number of saved emails.
    """
    init_db()
    rows = [_email_row(email) for email in emails]
    if not rows:
        return 0

    with _connect() as connection:
        connection.executemany(UPSERT_EMAIL_SQL, rows)
        connection.commit()
    return len(rows)


def get_all_emails() -> list[dict[str, Any]]:
    """Return all saved emails."""
    init_db()
    with _connect() as connection:
        return [_row_to_dict(row) for row in connection.execute(SELECT_ALL_EMAILS_SQL)]


def get_emails_by_label(label: str) -> list[dict[str, Any]]:
    """Return emails matching a category label.

    Args:
        label: Category label to filter by.

    Returns:
        Matching emails.
    """
    init_db()
    with _connect() as connection:
        return [
            _row_to_dict(row)
            for row in connection.execute(SELECT_EMAILS_BY_LABEL_SQL, (label,))
        ]


def _email_row(email: dict[str, Any]) -> tuple[str, str, str, str, str, str, float, str, str]:
    """Convert an email dictionary to SQLite parameters."""
    return (
        str(email.get("id", "")),
        str(email.get("subject", "")),
        str(email.get("sender", "")),
        str(email.get("date", "")),
        str(email.get("body", email.get("body_snippet", ""))),
        str(email.get("label", "Other")),
        float(email.get("confidence", 0.0)),
        str(email.get("created_at") or datetime.now(tz=timezone.utc).isoformat()),
        str(email.get("priority_tag", "FYI")),
    )


def _ensure_columns(connection: sqlite3.Connection) -> None:
    """Add columns needed by the Flask app when an old table exists."""
    existing_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(emails)")
    }
    for column, column_type in REQUIRED_COLUMNS.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE emails ADD COLUMN {column} {column_type}")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite row to a dictionary."""
    priority_tag = row["priority_tag"] if "priority_tag" in row.keys() and row["priority_tag"] is not None else "FYI"
    return {
        "id": row["id"],
        "subject": row["subject"],
        "sender": row["sender"],
        "date": row["date"],
        "body": row["body"],
        "label": row["label"],
        "confidence": row["confidence"],
        "created_at": row["created_at"],
        "priority_tag": priority_tag,
    }
