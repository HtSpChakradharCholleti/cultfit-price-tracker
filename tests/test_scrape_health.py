import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.scrape_run import (
    create_run,
    mark_running_runs_failed,
    update_run,
)
from app.services.scrape_health import get_scrape_health


IST = ZoneInfo("Asia/Kolkata")


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE scrape_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            error_message TEXT
        )
        """
    )
    return conn


def _insert_run(conn, started_at, status, completed_at=None, error_message=None):
    conn.execute(
        """
        INSERT INTO scrape_runs
            (started_at, completed_at, status, error_message)
        VALUES (?, ?, ?, ?)
        """,
        (started_at, completed_at, status, error_message),
    )
    conn.commit()


def test_health_reports_successful_scrape_today():
    conn = _connection()
    now = datetime(2026, 11, 14, 10, 0, tzinfo=IST)
    started = datetime(2026, 11, 14, 8, 0, tzinfo=IST).astimezone(ZoneInfo("UTC")).isoformat()
    completed = datetime(2026, 11, 14, 8, 1, tzinfo=IST).astimezone(ZoneInfo("UTC")).isoformat()
    _insert_run(conn, started, "success", completed)

    health = get_scrape_health(conn, now)

    assert health["today_status"] == "success"
    assert health["today_state"] == "complete"
    assert health["today_last_attempt"]["status"] == "success"
    assert health["last_successful"]["completed_at"] == completed
    assert health["next_scheduled_run"] == "2026-11-15T08:00:00+05:30"


def test_health_distinguishes_failed_attempt_from_not_run():
    conn = _connection()
    now = datetime(2026, 11, 14, 10, 0, tzinfo=IST)
    started = datetime(2026, 11, 14, 8, 0, tzinfo=IST).astimezone(ZoneInfo("UTC")).isoformat()
    _insert_run(conn, started, "failed", started, "Cult.fit returned HTTP 503")

    health = get_scrape_health(conn, now)
    assert health["today_status"] == "failed"
    assert health["today_state"] == "failed"
    assert health["today_last_attempt"]["error_message"] == "Cult.fit returned HTTP 503"

    empty_conn = _connection()
    empty_health = get_scrape_health(empty_conn, now)
    assert empty_health["today_status"] == "not_run"
    assert empty_health["today_state"] == "pending"
    assert empty_health["last_successful"] is None


def test_running_scrape_is_reported_as_incomplete():
    conn = _connection()
    now = datetime(2026, 11, 14, 8, 30, tzinfo=IST)
    started = datetime(2026, 11, 14, 8, 0, tzinfo=IST).astimezone(ZoneInfo("UTC")).isoformat()
    _insert_run(conn, started, "running")

    health = get_scrape_health(conn, now)
    assert health["today_status"] == "running"
    assert health["today_state"] == "pending"
    assert health["today_last_attempt"]["completed_at"] is None


def test_completion_date_uses_configured_local_timezone():
    conn = _connection()
    now = datetime(2026, 11, 14, 6, 0, tzinfo=IST)
    started = "2026-11-13T23:50:00+00:00"
    completed = "2026-11-14T00:05:00+00:00"
    _insert_run(conn, started, "success", completed)

    health = get_scrape_health(conn, now)

    assert health["today_state"] == "complete"
    assert health["today_status"] == "success"
    assert health["today_last_attempt"]["completed_at"] == completed


def test_restart_finalizes_orphaned_running_scrapes():
    conn = _connection()
    run = create_run(conn)
    assert mark_running_runs_failed(conn) == 1

    updated = conn.execute(
        "SELECT status, completed_at, error_message FROM scrape_runs WHERE id = ?",
        (run.id,),
    ).fetchone()
    assert updated["status"] == "failed"
    assert updated["completed_at"] is not None
    assert "restart" in updated["error_message"].lower()


def test_update_run_records_completion_for_success_and_failure():
    conn = _connection()
    success = create_run(conn)
    completed_success = update_run(conn, success.id, "success")
    assert completed_success.status == "success"
    assert completed_success.completed_at is not None

    failed = create_run(conn)
    completed_failed = update_run(conn, failed.id, "failed", "database write failed")
    assert completed_failed.status == "failed"
    assert completed_failed.completed_at is not None
    assert completed_failed.error_message == "database write failed"
