"""HTML dashboard routes."""

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.models.product import list_products
from app.models.membership_plan import list_plans_for_product, get_plan
from app.models.scrape_run import get_latest_run
from app.services.scrape_health import get_scrape_health
from app.services.pricing_service import (
    get_latest_prices,
    get_plan_history,
    get_plan_offers,
    get_offer_summary,
    get_offer_history,
    calculate_effective_price,
    calculate_effective_monthly_cost,
)
from app.services.statistics_service import compute_statistics

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Jinja Filters
# ---------------------------------------------------------------------------

def format_inr(value) -> str:
    """Format numeric price into Indian rupee format (e.g. 19,450)."""
    if value is None:
        return "—"
    try:
        val = int(round(float(value)))
        s = str(abs(val))
        if len(s) <= 3:
            res = s
        else:
            res = s[-3:]
            s = s[:-3]
            while len(s) > 2:
                res = s[-2:] + "," + res
                s = s[:-2]
            if s:
                res = s + "," + res
        return ("-" if val < 0 else "") + res
    except (ValueError, TypeError):
        return str(value)


def format_inr_decimal(value) -> str:
    """Format a numeric value as Indian currency with two decimal places."""
    if value is None:
        return "—"
    try:
        numeric = float(value)
        sign = "-" if numeric < 0 else ""
        absolute = abs(numeric)
        whole = int(absolute)
        paise = int(round((absolute - whole) * 100))
        if paise == 100:
            whole += 1
            paise = 0
        return f"{sign}{format_inr(whole)}.{paise:02d}"
    except (ValueError, TypeError):
        return str(value)


def format_dt(value) -> str:
    """Format UTC datetime / ISO string to Asia/Kolkata readable string."""
    if not value:
        return "Never"
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = value
        ist_dt = dt.astimezone(ZoneInfo("Asia/Kolkata"))
        return ist_dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return str(value)


templates.env.filters["format_inr"] = format_inr
templates.env.filters["format_inr_decimal"] = format_inr_decimal
templates.env.filters["format_dt"] = format_dt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group_plans_by_tier(plans: list) -> dict[str, list]:
    """Return plans grouped as {tier: [plan, …]}."""
    grouped: dict[str, list] = {}
    for plan in plans:
        grouped.setdefault(plan.tier, []).append(plan)
    return grouped


def _default_plan_id(plans: list) -> Optional[int]:
    """Prefer the primary Elite 12 Months plan, then fall back to the first plan."""
    for plan in plans:
        if plan.tier.upper() == "ELITE" and plan.duration_months == 12:
            return plan.id
    return plans[0].id if plans else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    plan_id: Optional[int] = Query(None),
):
    """
    Render the main dashboard page.
    """
    with get_db() as conn:
        products = list_products(conn)

        plans: list = []
        plans_grouped: dict = {}
        latest_prices_map: dict = {}
        selected_plan = None
        selected_price = None
        selected_offers: list[dict] = []
        effective_price: dict = {}
        offer_history: list[dict] = []
        offer_summary: dict = {"counts": {}, "verified_at": None}
        history: list = []
        stats: dict = {}

        if products:
            first_product = products[0]
            plans = list_plans_for_product(conn, first_product.id)
            plans_grouped = _group_plans_by_tier(plans)

            # Build latest prices map {plan_id: obs_dict}
            latest = get_latest_prices(conn, first_product.id)
            for obs in latest:
                latest_prices_map[obs["plan_id"]] = obs

            # Determine which plan to show detail for
            selected_plan_id = plan_id
            if selected_plan_id is None and plans:
                selected_plan_id = _default_plan_id(plans)

            if selected_plan_id is not None:
                selected_plan = get_plan(conn, selected_plan_id)
                if selected_plan:
                    history = get_plan_history(conn, selected_plan_id)
                    stats = compute_statistics(history)
                    candidate_price = latest_prices_map.get(selected_plan.id)
                    selected_price = (
                        candidate_price
                        if candidate_price and candidate_price["selling_price"] is not None
                        else None
                    )
                    selected_offers = get_plan_offers(conn, selected_plan.id)
                    offer_summary = get_offer_summary(conn, selected_plan.id)
                    effective_price = calculate_effective_price(
                        selected_price["selling_price"]
                        if selected_price else None,
                        selected_offers,
                    )
                    effective_price["effective_monthly_cost"] = calculate_effective_monthly_cost(
                        effective_price,
                        selected_plan.duration_months,
                    )
                    offer_history = get_offer_history(conn, selected_plan.id)

        last_scrape = get_latest_run(conn)
        scrape_health = get_scrape_health(conn)

    # Convert plans to dicts for JSON serialization in template
    plans_dicts = [
        {
            "id": p.id,
            "product_id": p.product_id,
            "name": p.name,
            "duration_months": p.duration_months,
            "duration_label": p.duration_label,
            "tier": p.tier,
            "sku": p.sku,
        }
        for p in plans
    ]

    context = {
        "request": request,
        "products": products,
        "plans": plans_dicts,
        "plans_grouped": plans_grouped,
        "plans_by_tier": plans_grouped,
        "latest_prices_map": latest_prices_map,
        "latest_prices": latest_prices_map,
        "last_scrape": last_scrape,
        "scrape_health": scrape_health,
        "selected_plan_id": selected_plan.id if selected_plan else None,
        "selected_plan": selected_plan,
        "selected_price": selected_price,
        "selected_offers": selected_offers,
        "effective_price": effective_price,
        "offer_history": offer_history,
        "offer_summary": offer_summary,
        "history": history,
        "stats": stats,
        "statistics": stats,
    }

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=context,
    )
