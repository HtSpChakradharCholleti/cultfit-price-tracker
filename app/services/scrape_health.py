"""Derived health information for the daily Cult.fit scrape."""

from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import sqlite3

from app.config import (
    SCHEDULER_HOUR,
    SCHEDULER_MINUTE,
    SCHEDULER_TIMEZONE,
)
from app.models.scrape_run import (
    ScrapeRun,
    get_latest_run,
    get_latest_successful_run,
    list_completed_runs_between,
    list_runs_between,
)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(ZoneInfo("UTC")).isoformat()


def local_day_bounds(
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    """Return the current configured-local calendar day as timezone-aware bounds."""
    timezone = ZoneInfo(SCHEDULER_TIMEZONE)
    current = (now or datetime.now(timezone)).astimezone(timezone)
    start = datetime.combine(current.date(), time.min, tzinfo=timezone)
    return start, start + timedelta(days=1)


def has_successful_scrape_today(
    conn: sqlite3.Connection,
    now: Optional[datetime] = None,
) -> bool:
    """Return whether any successful attempt completed during today's local date."""
    day_start, day_end = local_day_bounds(now)
    return any(
        run.status == "success"
        for run in list_completed_runs_between(
            conn, _utc_iso(day_start), _utc_iso(day_end)
        )
    )


def _serialize_run(run: Optional[ScrapeRun]) -> Optional[dict]:
    if run is None:
        return None
    return {
        "id": run.id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "status": run.status,
        "error_message": run.error_message,
    }


def _next_scheduled_run(now: datetime) -> datetime:
    timezone = ZoneInfo(SCHEDULER_TIMEZONE)
    local_now = now.astimezone(timezone)
    candidate = datetime.combine(
        local_now.date(),
        time(SCHEDULER_HOUR, SCHEDULER_MINUTE),
        tzinfo=timezone,
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def get_scrape_health(
    conn: sqlite3.Connection,
    now: Optional[datetime] = None,
) -> dict:
    """Return today's status, historical timestamps, and the next run time."""
    timezone = ZoneInfo(SCHEDULER_TIMEZONE)
    current = (now or datetime.now(timezone)).astimezone(timezone)
    day_start, day_end = local_day_bounds(current)
    today_runs = list_runs_between(
        conn,
        _utc_iso(day_start),
        _utc_iso(day_end),
    )
    today_completed_runs = list_completed_runs_between(
        conn,
        _utc_iso(day_start),
        _utc_iso(day_end),
    )
    today_successes = [
        run for run in today_completed_runs if run.status == "success"
    ]
    latest = get_latest_run(conn)
    latest_success = get_latest_successful_run(conn)
    latest_dict = _serialize_run(latest)

    has_success = bool(today_successes)
    if has_success:
        today_state = "complete"
    elif today_runs and today_runs[0].status == "running":
        today_state = "pending"
    elif today_runs:
        today_state = "failed"
    else:
        today_state = "pending"
    today_status = (
        today_runs[0].status
        if today_runs
        else ("success" if today_successes else "not_run")
    )
    today_last_attempt = today_runs[0] if today_runs else (
        today_successes[0] if today_successes else None
    )

    return {
        "status": latest.status if latest else "never_run",
        "run": latest_dict,
        "completed_at": latest.completed_at if latest else None,
        "error_message": latest.error_message if latest else None,
        "today_state": today_state,
        "today_status": today_status,
        "today_last_attempt": _serialize_run(today_last_attempt),
        "last_attempt": _serialize_run(latest),
        "last_successful": _serialize_run(latest_success),
        "next_scheduled_run": _next_scheduled_run(current).isoformat(),
        "schedule_timezone": SCHEDULER_TIMEZONE,
        "schedule_hour": SCHEDULER_HOUR,
        "schedule_minute": SCHEDULER_MINUTE,
    }
