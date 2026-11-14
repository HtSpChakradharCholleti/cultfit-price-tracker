"""MembershipPlan model: dataclass + sqlite3 CRUD helpers."""

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

logger = logging.getLogger(__name__)

Tier = Literal["ELITE", "PRO", "PLAY"]


@dataclass
class MembershipPlan:
    id: Optional[int]
    product_id: int
    name: str
    duration_months: int
    tier: str          # 'ELITE' | 'PRO' | 'PLAY'
    sku: Optional[str]
    created_at: str    # UTC ISO-8601 string

    @property
    def duration_label(self) -> str:
        if "all india" in self.name.lower():
            return f"{self.duration_months}M All India"
        return f"{self.duration_months} {'Month' if self.duration_months == 1 else 'Months'}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def insert_plan(
    conn: sqlite3.Connection,
    product_id: int,
    name: str,
    duration_months: int,
    tier: str,
    sku: Optional[str] = None,
) -> MembershipPlan:
    """Insert a new MembershipPlan and return it."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO membership_plans
            (product_id, name, duration_months, tier, sku, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (product_id, name, duration_months, tier, sku, now),
    )
    return MembershipPlan(
        id=cur.lastrowid,
        product_id=product_id,
        name=name,
        duration_months=duration_months,
        tier=tier,
        sku=sku,
        created_at=now,
    )


def get_plan(conn: sqlite3.Connection, plan_id: int) -> Optional[MembershipPlan]:
    """Return a MembershipPlan by primary key, or None."""
    row = conn.execute(
        """
        SELECT id, product_id, name, duration_months, tier, sku, created_at
        FROM membership_plans WHERE id = ?
        """,
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    return MembershipPlan(**dict(row))


def list_plans_for_product(
    conn: sqlite3.Connection, product_id: int
) -> list[MembershipPlan]:
    """Return all plans for a product, ordered by tier then duration."""
    rows = conn.execute(
        """
        SELECT id, product_id, name, duration_months, tier, sku, created_at
        FROM membership_plans
        WHERE product_id = ?
        ORDER BY tier, duration_months
        """,
        (product_id,),
    ).fetchall()
    return [MembershipPlan(**dict(r)) for r in rows]


def get_or_create_plan(
    conn: sqlite3.Connection,
    product_id: int,
    name: str,
    duration_months: int,
    tier: str,
    sku: Optional[str] = None,
) -> MembershipPlan:
    """Return existing plan matching (product_id, tier, duration_months, name), or create it."""
    row = conn.execute(
        """
        SELECT id, product_id, name, duration_months, tier, sku, created_at
        FROM membership_plans
        WHERE product_id = ? AND tier = ? AND duration_months = ? AND name = ?
        """,
        (product_id, tier, duration_months, name),
    ).fetchone()
    if row:
        return MembershipPlan(**dict(row))
    plan = insert_plan(conn, product_id, name, duration_months, tier, sku)
    conn.commit()
    logger.info(
        "Created plan %r tier=%s duration=%sm (id=%s)",
        name, tier, duration_months, plan.id,
    )
    return plan
