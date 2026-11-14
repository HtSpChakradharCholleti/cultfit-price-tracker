"""PriceObservation model: dataclass + sqlite3 CRUD helpers."""

import json
import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PriceObservation:
    id: Optional[int]
    membership_plan_id: int
    original_price: float
    selling_price: float
    discount_percentage: float
    offer_description: Optional[str]
    raw_data: Optional[str]  # JSON string
    observed_at: str          # UTC ISO-8601 string


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def insert_observation(
    conn: sqlite3.Connection,
    membership_plan_id: int,
    original_price: float,
    selling_price: float,
    discount_percentage: float,
    offer_description: Optional[str] = None,
    raw_data: Optional[Any] = None,
    observed_at: Optional[str] = None,
) -> PriceObservation:
    """Insert a price observation and return it."""
    ts = observed_at or datetime.now(timezone.utc).isoformat()
    raw_str = json.dumps(raw_data) if raw_data is not None else None
    cur = conn.execute(
        """
        INSERT INTO price_observations
            (membership_plan_id, original_price, selling_price,
             discount_percentage, offer_description, raw_data, observed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            membership_plan_id,
            original_price,
            selling_price,
            discount_percentage,
            offer_description,
            raw_str,
            ts,
        ),
    )
    return PriceObservation(
        id=cur.lastrowid,
        membership_plan_id=membership_plan_id,
        original_price=original_price,
        selling_price=selling_price,
        discount_percentage=discount_percentage,
        offer_description=offer_description,
        raw_data=raw_str,
        observed_at=ts,
    )


def get_latest_observation(
    conn: sqlite3.Connection, membership_plan_id: int
) -> Optional[PriceObservation]:
    """Return the most recent observation for a plan, or None."""
    row = conn.execute(
        """
        SELECT id, membership_plan_id, original_price, selling_price,
               discount_percentage, offer_description, raw_data, observed_at
        FROM price_observations
        WHERE membership_plan_id = ?
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (membership_plan_id,),
    ).fetchone()
    if row is None:
        return None
    return PriceObservation(**dict(row))


def list_observations_for_plan(
    conn: sqlite3.Connection,
    membership_plan_id: int,
    days: Optional[int] = None,
) -> list[dict]:
    """
    Return all observations for a plan as plain dicts, oldest first.
    Optionally filtered to the last ``days`` calendar days.
    """
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT po.id, po.membership_plan_id, po.original_price, po.selling_price,
                   po.discount_percentage, po.offer_description, po.raw_data,
                   po.observed_at,
                   mp.name AS plan_name, mp.tier, mp.duration_months
            FROM price_observations po
            JOIN membership_plans mp ON mp.id = po.membership_plan_id
            WHERE po.membership_plan_id = ? AND po.observed_at >= ?
            ORDER BY po.observed_at ASC
            """,
            (membership_plan_id, cutoff),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT po.id, po.membership_plan_id, po.original_price, po.selling_price,
                   po.discount_percentage, po.offer_description, po.raw_data,
                   po.observed_at,
                   mp.name AS plan_name, mp.tier, mp.duration_months
            FROM price_observations po
            JOIN membership_plans mp ON mp.id = po.membership_plan_id
            WHERE po.membership_plan_id = ?
            ORDER BY po.observed_at ASC
            """,
            (membership_plan_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_recent_observations(
    conn: sqlite3.Connection, limit: int = 50
) -> list[dict]:
    """Return the most recent observations across all plans."""
    rows = conn.execute(
        """
        SELECT po.id, po.membership_plan_id, po.original_price, po.selling_price,
               po.discount_percentage, po.offer_description, po.observed_at,
               mp.name AS plan_name, mp.tier, mp.duration_months
        FROM price_observations po
        JOIN membership_plans mp ON mp.id = po.membership_plan_id
        ORDER BY po.observed_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
