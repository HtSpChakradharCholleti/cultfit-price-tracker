"""CultFit scraper: orchestrates fetch → parse → persist pipeline."""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.scraper.extraction import fetch_page, parse_plans, calculate_discount
from app.models.membership_plan import get_or_create_plan
from app.models.price_observation import (
    get_latest_observation,
    insert_observation,
)
from app.models.plan_offer import OfferData, sync_plan_offers
from app.config import BASE_URL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class PlanData:
    tier: str
    name: str
    duration_months: int
    duration_label: str
    mrp: float
    selling_price: float
    discount_pct: float
    offer_string: str
    product_id_code: str    # e.g. CULTPACK_* or PLAYPACKNEW*
    raw_widget_name: str
    offers: list[OfferData] = field(default_factory=list)
    offer_extraction_status: str = "partial"


@dataclass
class ScrapingResult:
    plans: list[PlanData]
    scraped_at: datetime
    source_url: str
    success: bool
    error: Optional[str] = None
    offer_extraction_status: str = "failed"


# ---------------------------------------------------------------------------
# Scraper class
# ---------------------------------------------------------------------------

class CultFitScraper:
    URL: str = BASE_URL

    def scrape(self) -> ScrapingResult:
        """
        Full scrape pipeline: fetch the page, parse plan cards, return a
        ``ScrapingResult``.  Never raises — errors are captured in the result.
        """
        scraped_at = datetime.now(timezone.utc)
        try:
            html = fetch_page(self.URL)
            raw_plans = parse_plans(html)
        except Exception as exc:
            logger.exception("Scrape failed: %s", exc)
            return ScrapingResult(
                plans=[],
                scraped_at=scraped_at,
                source_url=self.URL,
                success=False,
                error=str(exc),
            )

        if not raw_plans:
            return ScrapingResult(
                plans=[],
                scraped_at=scraped_at,
                source_url=self.URL,
                success=False,
                error="No membership plans could be extracted",
                offer_extraction_status="failed",
            )

        plans: list[PlanData] = []
        for rp in raw_plans:
            plans.append(
                PlanData(
                    tier=rp["tier"],
                    name=rp["name"],
                    duration_months=rp["duration_months"],
                    duration_label=rp["duration_label"],
                    mrp=rp["mrp"],
                    selling_price=rp["selling_price"],
                    discount_pct=rp["discount_pct"],
                    offer_string=rp.get("offer_string", ""),
                    product_id_code=rp.get("product_id", ""),
                    raw_widget_name=rp.get("widget_name", ""),
                    offers=[
                        OfferData(**offer)
                        for offer in rp.get("offers", [])
                    ],
                    offer_extraction_status=rp.get(
                        "offer_extraction_status", "partial"
                    ),
                )
            )

        logger.info(
            "Scrape complete: %d plan(s) found at %s", len(plans), self.URL
        )
        statuses = {plan.offer_extraction_status for plan in plans}
        offer_status = (
            "success"
            if statuses == {"success"}
            else "partial"
            if "success" in statuses
            else "failed"
        )
        return ScrapingResult(
            plans=plans,
            scraped_at=scraped_at,
            source_url=self.URL,
            success=True,
            offer_extraction_status=offer_status,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_observations(
        self,
        conn: sqlite3.Connection,
        result: ScrapingResult,
        product_db_id: int,
    ) -> dict:
        """
        Persist price observations for every plan in *result*.

        For each plan:
        1. ``get_or_create`` the ``MembershipPlan`` row.
        2. Compare to the latest stored observation.
        3. Insert a new ``PriceObservation`` even when the price is unchanged.
        Commit all observations and offer updates before returning.

        Returns
        -------
        dict with keys ``new``, ``unchanged``, ``plans_total``.
        """
        new_count = 0
        unchanged_count = 0
        if not result.success or not result.plans:
            raise ValueError("Cannot persist a scrape without successfully retrieved plans")
        observed_at = datetime.now(timezone.utc).isoformat()

        for plan_data in result.plans:
            # 1. Ensure plan row exists
            plan = get_or_create_plan(
                conn,
                product_id=product_db_id,
                name=plan_data.name,
                duration_months=plan_data.duration_months,
                tier=plan_data.tier,
                sku=plan_data.product_id_code or None,
                commit=False,
            )

            # 2. Check latest observation
            latest = get_latest_observation(conn, plan.id)

            changed = (
                latest is None
                or latest.selling_price != plan_data.selling_price
                or latest.original_price != plan_data.mrp
                or (latest.offer_description or "") != (plan_data.offer_string or "")
            )

            if not changed:
                unchanged_count += 1
            else:
                new_count += 1

            # 3. Persist new observation
            raw = {
                "widget_name": plan_data.raw_widget_name,
                "product_id_code": plan_data.product_id_code,
                "scraped_at": result.scraped_at.isoformat(),
            }
            insert_observation(
                conn,
                membership_plan_id=plan.id,
                original_price=plan_data.mrp,
                selling_price=plan_data.selling_price,
                discount_percentage=plan_data.discount_pct,
                offer_description=plan_data.offer_string or None,
                raw_data=raw,
                observed_at=observed_at,
            )
            logger.debug(
                "New observation for plan %r (id=%s): ₹%s → ₹%s",
                plan_data.name, plan.id, plan_data.mrp, plan_data.selling_price,
            )

        # Offers must be synchronized even when the price itself is unchanged.
        if result.offer_extraction_status == "success":
            for plan_data in result.plans:
                plan = get_or_create_plan(
                    conn,
                    product_id=product_db_id,
                    name=plan_data.name,
                    duration_months=plan_data.duration_months,
                    tier=plan_data.tier,
                    sku=plan_data.product_id_code or None,
                    commit=False,
                )
                sync_plan_offers(
                    conn,
                    membership_plan_id=plan.id,
                    offers=plan_data.offers,
                    observed_at=result.scraped_at.isoformat(),
                )

        conn.commit()
        summary = {
            "new": new_count,
            "unchanged": unchanged_count,
            "plans_total": len(result.plans),
        }
        logger.info("save_observations: %s", summary)
        return summary
