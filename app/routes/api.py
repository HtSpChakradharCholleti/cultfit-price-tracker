"""FastAPI JSON API routes (prefix=/api)."""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.database import get_db
from app.models.product import list_products
from app.models.membership_plan import list_plans_for_product, get_plan
from app.models.scrape_run import get_latest_run, list_runs, create_run, update_run, get_running_run
from app.services.pricing_service import (
    get_plan_history,
    get_latest_prices,
    get_plan_offers,
    get_offer_history,
    calculate_effective_price,
)
from app.services.statistics_service import compute_statistics
from app.scraper.cultfit import CultFitScraper
from app.models.product import get_or_create_product
from app.config import BASE_URL

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
    from app.database import get_db as _get_db

    with _get_db(db_path) as conn:
        run = create_run(conn)
        try:
            product = get_or_create_product(conn, "Cult.fit Elite", BASE_URL)
            scraper = CultFitScraper()
            result = scraper.scrape()

            if not result.success:
                update_run(conn, run.id, "failed", error_message=result.error)
                return

            summary = scraper.save_observations(conn, result, product.id)
            logger.info("Scrape job summary: %s", summary)
            update_run(conn, run.id, "success")
        except Exception as exc:
            logger.exception("Background scrape job failed: %s", exc)
            try:
                update_run(conn, run.id, "failed", error_message=str(exc))
            except Exception:
                pass


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
    """Return the latest ScrapeRun status."""
    with get_db() as conn:
        run = get_latest_run(conn)
        all_runs = list_runs(conn, limit=5)

    if run is None:
        return {"status": "never_run", "run": None, "completed_at": None, "error_message": None}

    return {
        "status": run.status,
        "completed_at": run.completed_at,
        "error_message": run.error_message,
        "run": {
            "id": run.id,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "status": run.status,
            "error_message": run.error_message,
        },
        "recent_runs": [
            {
                "id": r.id,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "status": r.status,
            }
            for r in all_runs
        ],
    }
