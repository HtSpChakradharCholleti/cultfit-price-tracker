"""
APScheduler configuration for automatic price scraping.

- Runs once daily at 8:00 AM in Asia/Kolkata timezone.
- Uses max_instances=1 to prevent overlapping executions.
- Records run details (start, complete, status, errors) in SQLite scrape_runs table.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from app.config import (
    BASE_URL,
    DB_PATH,
    SCHEDULER_HOUR,
    SCHEDULER_MINUTE,
    SCHEDULER_TIMEZONE,
)
from app.database import get_db
from app.models.product import get_or_create_product
from app.models.scrape_run import create_run, update_run, get_running_run
from app.scraper.cultfit import CultFitScraper

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone=ZoneInfo(SCHEDULER_TIMEZONE))


def run_scheduled_scrape() -> None:
    """
    Execute a scheduled scrape job.

    1. Checks if another scrape is already running.
    2. Logs a ScrapeRun entry in 'running' state.
    3. Runs the CultFit scraper and saves any new observations.
    4. Marks the run as 'success' or 'failed'.
    """
    logger.info("Starting scheduled Cult.fit price scrape...")
    with get_db(DB_PATH) as conn:
        running = get_running_run(conn)
        if running is not None:
            logger.warning(
                "A scrape run is already in progress (run id=%s). Skipping scheduled execution.",
                running.id,
            )
            return

        run = create_run(conn)

        try:
            product = get_or_create_product(conn, "Cult.fit Elite", BASE_URL)
            scraper = CultFitScraper()
            result = scraper.scrape()

            if not result.success:
                update_run(conn, run.id, "failed", error_message=result.error)
                logger.error("Scheduled scrape failed: %s", result.error)
                return

            summary = scraper.save_observations(conn, result, product.id)
            update_run(conn, run.id, "success")
            logger.info("Scheduled scrape completed successfully: %s", summary)

        except Exception as exc:
            logger.exception("Unexpected error in scheduled scrape: %s", exc)
            try:
                update_run(conn, run.id, "failed", error_message=str(exc))
            except Exception:
                pass


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
