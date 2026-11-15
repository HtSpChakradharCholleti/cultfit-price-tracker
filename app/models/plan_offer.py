"""Offer models and historical persistence helpers."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class OfferData:
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    coupon_code: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    maximum_discount: Optional[float] = None
    minimum_transaction_amount: Optional[float] = None
    eligible_payment_methods: Optional[str] = None
    validity_start: Optional[str] = None
    validity_end: Optional[str] = None
    eligibility_conditions: Optional[str] = None
    is_conditional: Optional[bool] = None
    is_stackable: Optional[bool] = None
    source_url: Optional[str] = None

    def normalized(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "coupon_code": self.coupon_code,
            "discount_type": self.discount_type,
            "discount_value": self.discount_value,
            "maximum_discount": self.maximum_discount,
            "minimum_transaction_amount": self.minimum_transaction_amount,
            "eligible_payment_methods": self.eligible_payment_methods,
            "validity_start": self.validity_start,
            "validity_end": self.validity_end,
            "eligibility_conditions": self.eligibility_conditions,
            "is_conditional": self.is_conditional,
            "is_stackable": self.is_stackable,
            "source_url": self.source_url,
        }

    def key(self) -> str:
        identity = {
            "source_url": self.source_url,
            "title": self.title,
            "category": self.category,
            "discount_type": self.discount_type,
        }
        return hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:32]


@dataclass
class PlanOffer:
    id: int
    membership_plan_id: int
    offer_key: str
    current_status: str
    fields: dict[str, Any]


def offer_category_group(category: Optional[str]) -> str:
    normalized = (category or "").strip().lower()
    if normalized == "instant discount":
        return "Instant discounts"
    if normalized == "coupon":
        return "Coupons"
    if normalized == "emi":
        return "EMI"
    return "Other benefits"


def _row_fields(row: sqlite3.Row) -> dict[str, Any]:
    keys = (
        "title", "description", "category", "coupon_code", "discount_type",
        "discount_value", "maximum_discount", "minimum_transaction_amount",
        "eligible_payment_methods", "validity_start", "validity_end",
        "eligibility_conditions", "is_conditional", "is_stackable", "source_url",
    )
    return {key: row[key] for key in keys}


def _offer_from_row(row: sqlite3.Row) -> PlanOffer:
    return PlanOffer(
        id=row["id"],
        membership_plan_id=row["membership_plan_id"],
        offer_key=row["offer_key"],
        current_status=row["current_status"],
        fields=_row_fields(row),
    )


def _changed(offer: PlanOffer, data: OfferData) -> bool:
    return offer.fields != data.normalized()


def _identity_matches(offer: PlanOffer, data: OfferData) -> bool:
    """Match legacy keys to the stable identity used by newer observations."""
    if data.source_url and offer.fields.get("source_url"):
        return data.source_url == offer.fields["source_url"]
    return (
        (offer.fields.get("title") or "") == (data.title or "")
        and (offer.fields.get("category") or "") == (data.category or "")
        and (offer.fields.get("discount_type") or "") == (data.discount_type or "")
    )


def sync_plan_offers(
    conn: sqlite3.Connection,
    membership_plan_id: int,
    offers: list[OfferData],
    observed_at: Optional[str] = None,
) -> dict[str, int]:
    """Synchronize a successfully extracted offer set and append only changes."""
    ts = observed_at or datetime.now(timezone.utc).isoformat()
    existing_rows = conn.execute(
        """
        SELECT id, membership_plan_id, offer_key, current_status,
               title, description, category, coupon_code, discount_type,
               discount_value, maximum_discount, minimum_transaction_amount,
               eligible_payment_methods, validity_start, validity_end,
               eligibility_conditions, is_conditional, is_stackable, source_url
        FROM plan_offers
        WHERE membership_plan_id = ?
        """,
        (membership_plan_id,),
    ).fetchall()
    existing = {row["offer_key"]: _offer_from_row(row) for row in existing_rows}
    seen_keys: set[str] = set()
    new_count = changed_count = removed_count = 0

    for data in offers:
        key = data.key()
        seen_keys.add(key)
        current = existing.get(key)
        if current is None:
            current = next(
                (offer for offer in existing.values() if _identity_matches(offer, data)),
                None,
            )
            if current is not None and current.offer_key != key:
                conn.execute(
                    "UPDATE plan_offers SET offer_key=? WHERE id=?",
                    (key, current.id),
                )
                existing.pop(current.offer_key, None)
                existing[key] = current
        if current is None:
            cur = conn.execute(
                """
                INSERT INTO plan_offers
                    (membership_plan_id, offer_key, title, description, category,
                     coupon_code, discount_type, discount_value, maximum_discount,
                     minimum_transaction_amount, eligible_payment_methods,
                     validity_start, validity_end, eligibility_conditions,
                     is_conditional, is_stackable, source_url,
                     first_observed_at, last_observed_at, current_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?)
                """,
                (
                    membership_plan_id, key, *data.normalized().values(),
                    ts, ts, ts,
                ),
            )
            offer_id = cur.lastrowid
            new_count += 1
            status = "available"
        else:
            offer_id = current.id
            status = "available"
            if _changed(current, data) or current.current_status != "available":
                conn.execute(
                    """
                    UPDATE plan_offers
                    SET title=?, description=?, category=?, coupon_code=?,
                        discount_type=?, discount_value=?, maximum_discount=?,
                        minimum_transaction_amount=?, eligible_payment_methods=?,
                        validity_start=?, validity_end=?, eligibility_conditions=?,
                        is_conditional=?, is_stackable=?, source_url=?,
                        last_observed_at=?, current_status='available'
                    WHERE id=?
                    """,
                    (*data.normalized().values(), ts, offer_id),
                )
                changed_count += 1
            else:
                conn.execute(
                    "UPDATE plan_offers SET last_observed_at=?, current_status='available' WHERE id=?",
                    (ts, offer_id),
                )

        conn.execute(
            """
            INSERT OR IGNORE INTO offer_observations
                (offer_id, observed_at, status, title, description, category,
                 coupon_code, discount_type, discount_value, maximum_discount,
                 minimum_transaction_amount, eligible_payment_methods,
                 validity_start, validity_end, eligibility_conditions,
                 is_conditional, is_stackable, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                offer_id, ts, status, *data.normalized().values(),
            ),
        )

    for key, current in existing.items():
        if key in seen_keys or current.current_status != "available":
            continue
        conn.execute(
            "UPDATE plan_offers SET current_status='removed', last_observed_at=? WHERE id=?",
            (ts, current.id),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO offer_observations
                (offer_id, observed_at, status, title, description, category,
                 coupon_code, discount_type, discount_value, maximum_discount,
                 minimum_transaction_amount, eligible_payment_methods,
                 validity_start, validity_end, eligibility_conditions,
                 is_conditional, is_stackable, source_url)
            SELECT id, ?, 'removed', title, description, category, coupon_code,
                   discount_type, discount_value, maximum_discount,
                   minimum_transaction_amount, eligible_payment_methods,
                   validity_start, validity_end, eligibility_conditions,
                   is_conditional, is_stackable, source_url
            FROM plan_offers WHERE id = ?
            """,
            (ts, current.id),
        )
        removed_count += 1

    return {
        "new": new_count,
        "changed": changed_count,
        "removed": removed_count,
        "total": len(offers),
    }


def list_plan_offers(
    conn: sqlite3.Connection,
    membership_plan_id: int,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    where = "WHERE po.membership_plan_id = ?"
    params: list[Any] = [membership_plan_id]
    if status:
        where += " AND po.current_status = ?"
        params.append(status)
    rows = conn.execute(
        f"""
        SELECT po.*, mp.name AS plan_name
        FROM plan_offers po
        JOIN membership_plans mp ON mp.id = po.membership_plan_id
        {where}
        ORDER BY po.current_status DESC, po.first_observed_at ASC, po.id ASC
        """,
        params,
    ).fetchall()
    offers = []
    for row in rows:
        offer = dict(row)
        offer["availability"] = offer["current_status"]
        offer["category_group"] = offer_category_group(offer.get("category"))
        offers.append(offer)
    return offers


def list_offer_history(
    conn: sqlite3.Connection,
    membership_plan_id: int,
    days: Optional[int] = None,
) -> list[dict[str, Any]]:
    where = "WHERE po.membership_plan_id = ?"
    params: list[Any] = [membership_plan_id]
    if days is not None:
        cutoff = (
            datetime.now(timezone.utc)
            .timestamp() - int(days) * 24 * 60 * 60
        )
        where += " AND oo.observed_at >= ?"
        params.append(datetime.fromtimestamp(cutoff, timezone.utc).isoformat())
    rows = conn.execute(
        f"""
        SELECT oo.*, po.membership_plan_id, po.offer_key
        FROM offer_observations oo
        JOIN plan_offers po ON po.id = oo.offer_id
        {where}
        ORDER BY oo.observed_at ASC, oo.id ASC
        """,
        params,
    ).fetchall()
    history: list[dict[str, Any]] = []
    previous_by_offer: dict[int, dict[str, Any]] = {}
    comparable_fields = (
        "title", "description", "category", "coupon_code", "discount_type",
        "discount_value", "maximum_discount", "minimum_transaction_amount",
        "eligible_payment_methods", "validity_start", "validity_end",
        "eligibility_conditions", "is_conditional", "is_stackable", "source_url",
    )
    for row in rows:
        current = dict(row)
        previous = previous_by_offer.get(current["offer_id"])
        current_values = tuple(current.get(field) for field in comparable_fields)
        previous_values = (
            tuple(previous.get(field) for field in comparable_fields)
            if previous else None
        )
        if previous is None:
            current["event_type"] = "Offer introduced"
        elif previous["status"] != current["status"]:
            current["event_type"] = (
                "Offer removed" if current["status"] == "removed"
                else "Offer introduced" if current["status"] == "available"
                else "Availability changed"
            )
        elif current["status"] == "available" and current_values != previous_values:
            current["event_type"] = (
                "Coupon code changed"
                if current.get("coupon_code") != previous.get("coupon_code")
                else "Offer description or value changed"
            )
        else:
            previous_by_offer[current["offer_id"]] = current
            continue
        current["category_group"] = offer_category_group(current.get("category"))
        history.append(current)
        previous_by_offer[current["offer_id"]] = current
    return history


def get_latest_offer_verification(
    conn: sqlite3.Connection,
    membership_plan_id: int,
) -> Optional[str]:
    row = conn.execute(
        """
        SELECT MAX(oo.observed_at) AS verified_at
        FROM offer_observations oo
        JOIN plan_offers po ON po.id = oo.offer_id
        WHERE po.membership_plan_id = ?
        """,
        (membership_plan_id,),
    ).fetchone()
    return row["verified_at"] if row else None
