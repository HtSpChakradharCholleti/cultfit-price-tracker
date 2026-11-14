"""SQLite database setup: connection management, WAL mode, table creation."""

import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from app import config

logger = logging.getLogger(__name__)


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a connection with row_factory and WAL mode configured."""
    target_path = db_path or config.DB_PATH
    conn = sqlite3.connect(target_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


@contextmanager
def get_db(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a SQLite connection and commits/rolls back."""
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def checkpoint_db(db_path: Optional[str] = None) -> None:
    """Flush WAL journal changes into the main SQLite database file for full persistence."""
    try:
        conn = _connect(db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.commit()
            logger.debug("WAL checkpoint completed.")
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("WAL checkpoint skipped or failed: %s", exc)


def init_db(db_path: Optional[str] = None) -> None:
    """Create all tables (if they don't exist) and proper indexes."""
    conn = _connect(db_path)
    try:
        c = conn.cursor()

        # --- products ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE,
                source_url  TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)"
        )

        # --- membership_plans ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS membership_plans (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id       INTEGER NOT NULL REFERENCES products(id),
                name             TEXT    NOT NULL,
                duration_months  INTEGER NOT NULL,
                tier             TEXT    NOT NULL,
                sku              TEXT,
                created_at       TEXT    NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plans_product
            ON membership_plans(product_id)
            """
        )
        c.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_unique
            ON membership_plans(product_id, tier, duration_months, name)
            """
        )

        # --- price_observations ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS price_observations (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                membership_plan_id INTEGER NOT NULL REFERENCES membership_plans(id),
                original_price     REAL    NOT NULL,
                selling_price      REAL    NOT NULL,
                discount_percentage REAL   NOT NULL DEFAULT 0.0,
                offer_description  TEXT,
                raw_data           TEXT,
                observed_at        TEXT    NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_obs_plan
            ON price_observations(membership_plan_id)
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_obs_observed_at
            ON price_observations(observed_at)
            """
        )

        # --- plan_offers ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_offers (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                membership_plan_id       INTEGER NOT NULL REFERENCES membership_plans(id),
                offer_key                TEXT    NOT NULL,
                title                    TEXT,
                description              TEXT,
                category                 TEXT,
                coupon_code              TEXT,
                discount_type            TEXT,
                discount_value           REAL,
                maximum_discount         REAL,
                minimum_transaction_amount REAL,
                eligible_payment_methods TEXT,
                validity_start           TEXT,
                validity_end             TEXT,
                eligibility_conditions   TEXT,
                is_conditional           INTEGER,
                is_stackable             INTEGER,
                source_url               TEXT,
                first_observed_at        TEXT NOT NULL,
                last_observed_at         TEXT NOT NULL,
                current_status           TEXT NOT NULL DEFAULT 'available',
                created_at               TEXT NOT NULL,
                UNIQUE(membership_plan_id, offer_key)
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_offers_plan
            ON plan_offers(membership_plan_id)
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_offers_status
            ON plan_offers(current_status)
            """
        )

        # --- offer_observations ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS offer_observations (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id                   INTEGER NOT NULL REFERENCES plan_offers(id),
                observed_at                TEXT    NOT NULL,
                status                     TEXT    NOT NULL,
                title                      TEXT,
                description                TEXT,
                category                   TEXT,
                coupon_code                TEXT,
                discount_type              TEXT,
                discount_value             REAL,
                maximum_discount           REAL,
                minimum_transaction_amount REAL,
                eligible_payment_methods   TEXT,
                validity_start             TEXT,
                validity_end               TEXT,
                eligibility_conditions     TEXT,
                is_conditional             INTEGER,
                is_stackable               INTEGER,
                source_url                 TEXT,
                UNIQUE(offer_id, observed_at, status)
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_offer_obs_offer
            ON offer_observations(offer_id)
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_offer_obs_observed_at
            ON offer_observations(observed_at)
            """
        )

        # --- scrape_runs ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at    TEXT NOT NULL,
                completed_at  TEXT,
                status        TEXT NOT NULL DEFAULT 'running',
                error_message TEXT
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runs_started_at
            ON scrape_runs(started_at DESC)
            """
        )

        conn.commit()
        logger.info("Database initialised at %s", db_path)
    finally:
        conn.close()
