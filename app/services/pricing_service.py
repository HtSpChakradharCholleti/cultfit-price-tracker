"""Pricing service: query helpers for the API layer."""

import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.price_observation import list_observations_for_plan
from app.models.plan_offer import list_plan_offers, list_offer_history

logger = logging.getLogger(__name__)

# Tier sort order for consistent display
_TIER_ORDER = {"ELITE": 0, "ELITE PLUS": 1, "PRO": 2, "PRO PLUS": 3, "PLAY": 4}


def get_latest_prices(conn: sqlite3.Connection, product_id: int) -> list[dict]:
    """
    Return the latest price observation for every plan belonging to *product_id*.

    Results are sorted by tier (ELITE → PRO → PLAY) then by duration_months ASC.
    """
    rows = conn.execute(
        """
        SELECT
            mp.id               AS plan_id,
            mp.name             AS plan_name,
            mp.tier,
            mp.duration_months,
            mp.sku,
            po.id               AS observation_id,
            po.original_price,
            po.selling_price,
            po.discount_percentage,
            po.offer_description,
            po.observed_at
        FROM membership_plans mp
        LEFT JOIN price_observations po ON po.id = (
            SELECT id FROM price_observations
            WHERE membership_plan_id = mp.id
            ORDER BY observed_at DESC
            LIMIT 1
        )
        WHERE mp.product_id = ?
        ORDER BY mp.tier, mp.duration_months
        """,
        (product_id,),
    ).fetchall()

    results = [dict(r) for r in rows]
    # Sort by tier display order then duration
    results.sort(
        key=lambda r: (_TIER_ORDER.get(r.get("tier", ""), 99), r.get("duration_months", 0))
    )
    return results


def get_plan_history(
    conn: sqlite3.Connection, plan_id: int, days: Optional[int] = None
) -> list[dict]:
    """
    Return all observations for *plan_id*, oldest first.

    Parameters
    ----------
    days:
        When provided, restrict to observations within the last *days* days.
    """
    return list_observations_for_plan(conn, plan_id, days=days)


def get_plan_offers(
    conn: sqlite3.Connection,
    plan_id: int,
    status: Optional[str] = "available",
) -> list[dict]:
    return list_plan_offers(conn, plan_id, status=status)


def get_offer_history(
    conn: sqlite3.Connection,
    plan_id: int,
    days: Optional[int] = None,
) -> list[dict]:
    return list_offer_history(conn, plan_id, days=days)


def calculate_effective_price(
    selling_price: Optional[float],
    offers: list[dict],
) -> dict:
    """Separate immediate, conditional, and cashback benefits.

    Only immediate and conditional discounts reduce the potential purchase
    price. Cashback is reported separately because it is not an upfront price
    reduction.
    """
    if selling_price is None:
        return {
            "advertised_price": None,
            "instant_discount": 0.0,
            "conditional_discount": 0.0,
            "cashback": 0.0,
            "potential_effective_price": None,
        }

    instant = conditional = cashback = 0.0
    for offer in offers:
        value = offer.get("discount_value")
        if value is None:
            continue
        value = float(value)
        if offer.get("discount_type") == "percentage":
            value = selling_price * value / 100
        maximum = offer.get("maximum_discount")
        if maximum is not None:
            value = min(value, float(maximum))
        category = (offer.get("category") or "").lower()
        if category == "cashback":
            cashback += value
        elif category == "instant discount" or offer.get("is_conditional") is False:
            instant += value
        elif offer.get("is_conditional"):
            conditional += value

    return {
        "advertised_price": selling_price,
        "instant_discount": round(instant, 2),
        "conditional_discount": round(conditional, 2),
        "cashback": round(cashback, 2),
        "potential_effective_price": round(
            max(0.0, selling_price - instant - conditional), 2
        ),
    }


def detect_price_change(
    conn: sqlite3.Connection,
    plan_id: int,
    new_selling: float,
    new_mrp: float,
    new_offer: str,
) -> bool:
    """
    Return ``True`` if *new_selling*, *new_mrp*, or *new_offer* differ from the
    latest stored observation for *plan_id*, or if there is no prior observation.
    """
    row = conn.execute(
        """
        SELECT original_price, selling_price, offer_description
        FROM price_observations
        WHERE membership_plan_id = ?
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (plan_id,),
    ).fetchone()

    if row is None:
        return True  # No prior data → treat as changed

    return (
        row["selling_price"] != new_selling
        or row["original_price"] != new_mrp
        or (row["offer_description"] or "") != (new_offer or "")
    )
