"""ScrapeRun model: dataclass + sqlite3 CRUD helpers."""

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

logger = logging.getLogger(__name__)

Status = Literal["running", "success", "failed"]


@dataclass
class ScrapeRun:
    id: Optional[int]
    started_at: str           # UTC ISO-8601 string
    completed_at: Optional[str]
    status: str               # 'running' | 'success' | 'failed'
    error_message: Optional[str]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_run(conn: sqlite3.Connection) -> ScrapeRun:
    """Insert a new ScrapeRun with status='running' and return it."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO scrape_runs (started_at, status)
        VALUES (?, 'running')
        """,
        (now,),
    )
    conn.commit()
    return ScrapeRun(
        id=cur.lastrowid,
        started_at=now,
        completed_at=None,
        status="running",
        error_message=None,
    )


def update_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    error_message: Optional[str] = None,
) -> ScrapeRun:
    """Update status and completed_at of an existing ScrapeRun."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE scrape_runs
        SET status = ?, completed_at = ?, error_message = ?
        WHERE id = ?
        """,
        (status, now, error_message, run_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, started_at, completed_at, status, error_message FROM scrape_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    return ScrapeRun(**dict(row))


def get_latest_run(conn: sqlite3.Connection) -> Optional[ScrapeRun]:
    """Return the most recent ScrapeRun or None."""
    row = conn.execute(
        """
        SELECT id, started_at, completed_at, status, error_message
        FROM scrape_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return ScrapeRun(**dict(row))


def list_runs(conn: sqlite3.Connection, limit: int = 20) -> list[ScrapeRun]:
    """Return the most recent scrape runs, newest first."""
    rows = conn.execute(
        """
        SELECT id, started_at, completed_at, status, error_message
        FROM scrape_runs
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [ScrapeRun(**dict(r)) for r in rows]


def get_running_run(conn: sqlite3.Connection) -> Optional[ScrapeRun]:
    """Return a ScrapeRun currently in 'running' state, or None."""
    row = conn.execute(
        """
        SELECT id, started_at, completed_at, status, error_message
        FROM scrape_runs
        WHERE status = 'running'
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return ScrapeRun(**dict(row))
