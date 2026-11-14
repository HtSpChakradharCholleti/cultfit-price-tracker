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


def calculate_monthly_cost(
    selling_price: Optional[float],
    duration_months: Optional[int],
) -> Optional[float]:
    """Return a duration-derived monthly price, or None for invalid inputs."""
    try:
        duration = int(duration_months or 0)
        price = float(selling_price)
    except (TypeError, ValueError):
        return None
    if duration <= 0 or price < 0:
        return None
    return round(price / duration, 2)


def calculate_effective_monthly_cost(
    effective_price: dict,
    duration_months: Optional[int],
) -> Optional[float]:
    """Calculate monthly cost after explicit upfront instant discounts only."""
    advertised = effective_price.get("advertised_price")
    instant_discount = effective_price.get("instant_discount", 0.0)
    if advertised is None:
        return None
    try:
        adjusted_price = max(0.0, float(advertised) - float(instant_discount or 0))
    except (TypeError, ValueError):
        return None
    return calculate_monthly_cost(adjusted_price, duration_months)


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
    for result in results:
        result["monthly_cost"] = calculate_monthly_cost(
            result.get("selling_price"),
            result.get("duration_months"),
        )
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
    observations = list_observations_for_plan(conn, plan_id, days=days)
    for observation in observations:
        observation["monthly_cost"] = calculate_monthly_cost(
            observation.get("selling_price"),
            observation.get("duration_months"),
        )
    return observations


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
    """Separate verified upfront discounts from conditional benefits.

    Coupon offers are not automatically applied because their eligibility and
    applicability cannot be verified from the offer record alone. Cashback and
    non-monetary benefits are reported separately and do not reduce price.
    """
    if selling_price is None:
        return {
            "advertised_price": None,
            "instant_discount": 0.0,
            "conditional_discount": 0.0,
            "cashback": 0.0,
            "potential_effective_price": None,
            "instant_discounted_price": None,
            "excluded_benefit_count": 0,
        }

    instant = conditional = cashback = 0.0
    excluded_benefit_count = 0
    for offer in offers:
        category = (offer.get("category") or "").lower()
        if category == "coupon":
            excluded_benefit_count += 1
            continue
        value = offer.get("discount_value")
        if value is None:
            excluded_benefit_count += 1
            continue
        value = float(value)
        if offer.get("discount_type") == "percentage":
            value = selling_price * value / 100
        maximum = offer.get("maximum_discount")
        if maximum is not None:
            value = min(value, float(maximum))
        if category == "cashback":
            cashback += value
        elif category == "instant discount" or offer.get("is_conditional") is False:
            instant += value
        elif offer.get("is_conditional"):
            conditional += value
        else:
            excluded_benefit_count += 1

    return {
        "advertised_price": selling_price,
        "instant_discount": round(instant, 2),
        "conditional_discount": round(conditional, 2),
        "cashback": round(cashback, 2),
        "instant_discounted_price": round(max(0.0, selling_price - instant), 2),
        "potential_effective_price": round(
            max(0.0, selling_price - instant - conditional), 2
        ),
        "excluded_benefit_count": excluded_benefit_count,
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
