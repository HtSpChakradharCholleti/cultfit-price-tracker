"""FastAPI JSON API routes (prefix=/api)."""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.database import get_db
from app.models.product import list_products
from app.models.membership_plan import list_plans_for_product, get_plan
from app.models.scrape_run import get_running_run
from app.services.scrape_health import get_scrape_health
from app.services.pricing_service import (
    get_plan_history,
    get_latest_prices,
    get_plan_offers,
    get_offer_history,
    calculate_effective_price,
    calculate_effective_monthly_cost,
)
from app.services.statistics_service import compute_statistics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@router.get("/products/")
def api_list_products():
    """Return all tracked products."""
    with get_db() as conn:
        products = list_products(conn)
    return [
        {"id": p.id, "name": p.name, "source_url": p.source_url, "created_at": p.created_at}
        for p in products
    ]


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

@router.get("/plans/")
def api_list_plans(product_id: Optional[int] = Query(None)):
    """
    List membership plans.

    Pass ``?product_id=<id>`` to filter by product.  Without it, all plans
    across all products are returned.
    """
    with get_db() as conn:
        if product_id is not None:
            plans = list_plans_for_product(conn, product_id)
        else:
            # Fetch all plans by iterating products
            prods = list_products(conn)
            plans = []
            for p in prods:
                plans.extend(list_plans_for_product(conn, p.id))

    return [
        {
            "id": pl.id,
            "product_id": pl.product_id,
            "name": pl.name,
            "duration_months": pl.duration_months,
            "tier": pl.tier,
            "sku": pl.sku,
            "created_at": pl.created_at,
        }
        for pl in plans
    ]


@router.get("/plans/{plan_id}/history/")
def api_plan_history(plan_id: int, days: Optional[int] = Query(None)):
    """
    Return price history for *plan_id*.

    Pass ``?days=30`` to restrict to the last 30 days.
    """
    with get_db() as conn:
        plan = get_plan(conn, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        history = get_plan_history(conn, plan_id, days=days)
    return history


@router.get("/plans/{plan_id}/statistics/")
def api_plan_statistics(plan_id: int):
    """Return computed statistics for *plan_id*."""
    with get_db() as conn:
        plan = get_plan(conn, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        history = get_plan_history(conn, plan_id)
    return compute_statistics(history)


@router.get("/plans/{plan_id}/offers/")
def api_plan_offers(plan_id: int, status: Optional[str] = Query("available")):
    """Return the current or historical offer records for a plan."""
    with get_db() as conn:
        plan = get_plan(conn, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        offers = get_plan_offers(conn, plan_id, status=status)
        latest = get_latest_prices(conn, plan.product_id)
        price = next(
            (row for row in latest if row["plan_id"] == plan_id), None
        )
    effective = calculate_effective_price(
        price["selling_price"] if price else None,
        offers,
    )
    effective["effective_monthly_cost"] = calculate_effective_monthly_cost(
        effective,
        plan.duration_months,
    )
    return {"offers": offers, "effective_price": effective}


@router.get("/plans/{plan_id}/offers/history/")
def api_plan_offer_history(plan_id: int, days: Optional[int] = Query(None)):
    with get_db() as conn:
        plan = get_plan(conn, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        return get_offer_history(conn, plan_id, days=days)


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def _run_scrape_job(db_path: str) -> None:
    """Background task: run a full scrape and persist observations."""
    from app.scheduler import run_scrape_attempt

    run_scrape_attempt(db_path)


@router.post("/scraper/run/")
def api_trigger_scrape(background_tasks: BackgroundTasks):
    """
    Trigger a manual scrape in the background.

    Returns immediately with ``{"status": "started"}``.
    Returns ``{"status": "already_running"}`` if a scrape is already in progress.
    """
    from app.config import DB_PATH

    with get_db() as conn:
        running = get_running_run(conn)

    if running is not None:
        return {"status": "already_running", "run_id": running.id}

    background_tasks.add_task(_run_scrape_job, DB_PATH)
    return {"status": "started"}


@router.get("/scraper/status/")
def api_scraper_status():
    """Return current daily scrape health and the latest run details."""
    with get_db() as conn:
        health = get_scrape_health(conn)
    return health
