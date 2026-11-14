"""Application configuration — read from environment variables with sane defaults."""

import os
from pathlib import Path

# Project root directory (cultfit-price-tracker/)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Database Persistence
# ---------------------------------------------------------------------------
# Ensure database path is always an absolute path so data persists regardless
# of the current working directory from which uvicorn or python is executed.
_env_db = os.environ.get("DB_PATH")
if _env_db:
    if _env_db == ":memory:":
        DB_PATH: str = ":memory:"
    elif os.path.isabs(_env_db):
        DB_PATH = _env_db
    else:
        DB_PATH = str((PROJECT_ROOT / _env_db).resolve())
else:
    DB_PATH = str((PROJECT_ROOT / "cultfit_prices.db").resolve())

# Ensure parent directory exists for persistence
if DB_PATH != ":memory:":
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------
BASE_URL: str = os.environ.get(
    "BASE_URL", "https://next.cult.fit/sku/elite"
)

USER_AGENT: str = os.environ.get(
    "USER_AGENT",
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
)

SCRAPER_TIMEOUT: int = int(os.environ.get("SCRAPER_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
SCHEDULER_TIMEZONE: str = os.environ.get("SCHEDULER_TIMEZONE", "Asia/Kolkata")
SCHEDULER_HOUR: int = int(os.environ.get("SCHEDULER_HOUR", "8"))
SCHEDULER_MINUTE: int = int(os.environ.get("SCHEDULER_MINUTE", "0"))
