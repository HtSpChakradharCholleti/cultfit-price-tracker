"""
FastAPI application entry point for Cult.fit Elite Price Tracker.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config
from app.database import checkpoint_db, get_db, init_db
from app.models.product import get_or_create_product
from app.models.scrape_run import mark_running_runs_failed
from app.routes.api import router as api_router
from app.routes.dashboard import router as dashboard_router
from app.scheduler import schedule_startup_catchup, setup_scheduler, shutdown_scheduler

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cultfit-tracker")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Initializing Cult.fit Price Tracker...")

    # 1. Initialize SQLite schema
    init_db(config.DB_PATH)

    # 2. Seed initial product record
    with get_db(config.DB_PATH) as conn:
        get_or_create_product(conn, "Cult.fit Elite", config.BASE_URL)
        interrupted = mark_running_runs_failed(conn)
        if interrupted:
            logger.warning("Marked %d interrupted scrape run(s) as failed.", interrupted)

    # 3. Setup and start background scheduler
    setup_scheduler()
    schedule_startup_catchup()

    logger.info("Cult.fit Price Tracker ready.")
    yield

    # 4. Graceful shutdown
    shutdown_scheduler()
    checkpoint_db(config.DB_PATH)
    logger.info("Cult.fit Price Tracker stopped.")


app = FastAPI(
    title="Cult.fit Elite Price Tracker",
    description="Track Cult.fit Elite membership prices over time with interactive analytics.",
    lifespan=lifespan,
)

# Static files
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Routers
app.include_router(api_router)
app.include_router(dashboard_router)
