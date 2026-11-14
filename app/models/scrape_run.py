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


def acquire_run(conn: sqlite3.Connection) -> Optional[ScrapeRun]:
    """Atomically acquire the SQLite-backed scrape lock and create a run."""
    conn.execute("BEGIN IMMEDIATE")
    running = conn.execute(
        "SELECT id FROM scrape_runs WHERE status = 'running' LIMIT 1"
    ).fetchone()
    if running is not None:
        conn.rollback()
        return None

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
    if status not in ("running", "success", "failed"):
        raise ValueError(f"Unsupported scrape run status: {status}")
    now = datetime.now(timezone.utc).isoformat()
    completed_at = None if status == "running" else now
    conn.execute(
        """
        UPDATE scrape_runs
        SET status = ?, completed_at = ?, error_message = ?
        WHERE id = ?
        """,
        (status, completed_at, error_message, run_id),
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


def get_latest_successful_run(conn: sqlite3.Connection) -> Optional[ScrapeRun]:
    """Return the most recent successfully completed scrape run."""
    row = conn.execute(
        """
        SELECT id, started_at, completed_at, status, error_message
        FROM scrape_runs
        WHERE status = 'success'
        ORDER BY completed_at DESC, started_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return ScrapeRun(**dict(row))


def list_runs_between(
    conn: sqlite3.Connection,
    started_at: str,
    ended_at: str,
) -> list[ScrapeRun]:
    """Return runs whose start timestamp falls within [started_at, ended_at)."""
    rows = conn.execute(
        """
        SELECT id, started_at, completed_at, status, error_message
        FROM scrape_runs
        WHERE started_at >= ? AND started_at < ?
        ORDER BY started_at DESC
        """,
        (started_at, ended_at),
    ).fetchall()
    return [ScrapeRun(**dict(r)) for r in rows]


def list_completed_runs_between(
    conn: sqlite3.Connection,
    completed_at: str,
    ended_at: str,
) -> list[ScrapeRun]:
    """Return runs whose completion timestamp falls within [completed_at, ended_at)."""
    rows = conn.execute(
        """
        SELECT id, started_at, completed_at, status, error_message
        FROM scrape_runs
        WHERE completed_at >= ? AND completed_at < ?
        ORDER BY completed_at DESC, started_at DESC
        """,
        (completed_at, ended_at),
    ).fetchall()
    return [ScrapeRun(**dict(r)) for r in rows]


def mark_running_runs_failed(
    conn: sqlite3.Connection,
    error_message: str = "Scrape interrupted by application restart.",
) -> int:
    """Finalize orphaned running rows left behind by a process restart."""
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        UPDATE scrape_runs
        SET status = 'failed', completed_at = ?, error_message = ?
        WHERE status = 'running'
        """,
        (now, error_message),
    )
    conn.commit()
    return cursor.rowcount


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
