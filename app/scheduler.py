"""
APScheduler configuration for automatic price scraping.

- Runs once daily at 8:00 AM in Asia/Kolkata timezone.
- Uses max_instances=1 to prevent overlapping executions.
- Records run details (start, complete, status, errors) in SQLite scrape_runs table.
"""

import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo
from app import config

from app.config import (
    BASE_URL,
    SCHEDULER_HOUR,
    SCHEDULER_MINUTE,
    SCHEDULER_TIMEZONE,
)
from app.database import get_db
from app.models.product import get_or_create_product
from app.models.scrape_run import (
    acquire_run,
    get_running_run,
    list_runs_between,
    update_run,
)
from app.scraper.cultfit import CultFitScraper
from app.services.scrape_health import (
    _utc_iso,
    has_successful_scrape_today,
    local_day_bounds,
)

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone=ZoneInfo(SCHEDULER_TIMEZONE))
RETRY_DELAYS_MINUTES = (15, 30, 60)


def _failed_attempt_count_today(db_path: str | None = None) -> int:
    db_path = db_path or config.DB_PATH
    day_start, day_end = local_day_bounds()
    with get_db(db_path) as conn:
        runs = list_runs_between(conn, _utc_iso(day_start), _utc_iso(day_end))
    return sum(run.status == "failed" for run in runs)


def _schedule_retry(db_path: str | None = None) -> None:
    db_path = db_path or config.DB_PATH
    failed_attempts = _failed_attempt_count_today(db_path)
    if failed_attempts > len(RETRY_DELAYS_MINUTES):
        logger.error(
            "Scrape retry limit reached for today after %d failed attempts.",
            failed_attempts,
        )
        return

    delay = RETRY_DELAYS_MINUTES[failed_attempts - 1]
    timezone = ZoneInfo(SCHEDULER_TIMEZONE)
    retry_number = failed_attempts
    retry_date = datetime.now(timezone).date().isoformat()
    run_date = datetime.now(timezone) + timedelta(minutes=delay)
    job_id = f"cultfit-scrape-retry-{retry_date}-{retry_number}"
    _scheduler.add_job(
        run_scrape_attempt,
        trigger="date",
        run_date=run_date,
        args=[db_path],
        kwargs={"skip_if_success": True},
        id=job_id,
        name=f"Cult.fit scrape retry {retry_number}",
        replace_existing=True,
    )
    logger.info(
        "Scheduled scrape retry %d in %d minutes at %s.",
        retry_number,
        delay,
        run_date.isoformat(),
    )


def run_scrape_attempt(
    db_path: str | None = None,
    skip_if_success: bool = False,
) -> bool:
    """Run one persisted scrape attempt and schedule a bounded retry on failure."""
    db_path = db_path or config.DB_PATH
    if skip_if_success:
        with get_db(db_path) as conn:
            if has_successful_scrape_today(conn):
                logger.info("Today's scrape is already successful; skipping attempt.")
                return True
    logger.info("Starting Cult.fit price scrape attempt...")
    run = None
    success = False
    with get_db(db_path) as conn:
        run = acquire_run(conn)
        if run is None:
            logger.warning(
                "A scrape run is already in progress; skipping this execution.",
            )
            return False

        try:
            product = get_or_create_product(conn, "Cult.fit Elite", BASE_URL)
            scraper = CultFitScraper()
            result = scraper.scrape()

            if not result.success:
                update_run(conn, run.id, "failed", error_message=result.error)
                logger.error("Scheduled scrape failed: %s", result.error)
            else:
                summary = scraper.save_observations(conn, result, product.id)
                update_run(conn, run.id, "success")
                success = True
                logger.info("Scrape completed successfully: %s", summary)

        except Exception as exc:
            logger.exception("Unexpected error in scheduled scrape: %s", exc)
            conn.rollback()
            try:
                update_run(conn, run.id, "failed", error_message=str(exc))
            except Exception:
                logger.exception("Unable to record failed scrape attempt.")

    if not success:
        _schedule_retry(db_path)
    return success


def run_scheduled_scrape() -> None:
    """Run the preferred 8 AM scrape unless today's scrape already succeeded."""
    db_path = config.DB_PATH
    with get_db(db_path) as conn:
        if has_successful_scrape_today(conn):
            logger.info("Today's scrape is already successful; skipping scheduled run.")
            return
    run_scrape_attempt(db_path, skip_if_success=True)


def schedule_startup_catchup() -> bool:
    """Schedule an immediate catch-up when today's local scrape is incomplete."""
    db_path = config.DB_PATH
    with get_db(db_path) as conn:
        if has_successful_scrape_today(conn):
            logger.info("Today's scrape is already successful; no startup catch-up needed.")
            return False
        if get_running_run(conn) is not None:
            logger.info("A scrape is already running; startup catch-up is not needed.")
            return False

    timezone = ZoneInfo(SCHEDULER_TIMEZONE)
    catchup_date = datetime.now(timezone).date().isoformat()
    _scheduler.add_job(
        run_scrape_attempt,
        trigger="date",
        run_date=datetime.now(timezone),
        args=[db_path],
        kwargs={"skip_if_success": True},
        id=f"cultfit-startup-catchup-{catchup_date}",
        name="Cult.fit startup catch-up scrape",
        replace_existing=True,
    )
    logger.info("Scheduled startup catch-up scrape for %s.", catchup_date)
    return True


def setup_scheduler() -> BackgroundScheduler:
    """Initialize and start the background scheduler."""
    if not _scheduler.running:
        trigger = CronTrigger(
            hour=SCHEDULER_HOUR,
            minute=SCHEDULER_MINUTE,
            timezone=ZoneInfo(SCHEDULER_TIMEZONE),
        )
        _scheduler.add_job(
            run_scheduled_scrape,
            trigger=trigger,
            id="daily_cultfit_scrape",
            name="Daily Cult.fit Price Scrape (8:00 AM IST)",
            max_instances=1,
            replace_existing=True,
        )
        _scheduler.start()
        logger.info(
            "Scheduler started: daily scrape at %02d:%02d (%s)",
            SCHEDULER_HOUR,
            SCHEDULER_MINUTE,
            SCHEDULER_TIMEZONE,
        )
    return _scheduler


def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down.")
