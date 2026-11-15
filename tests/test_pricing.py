"""
Tests for pricing service, persistence, change detection, and immutability.
"""

import sqlite3
import pytest
from app.database import init_db
from app.models.product import insert_product
from app.models.membership_plan import insert_plan
from app.models.price_observation import (
    insert_observation,
    get_latest_observation,
    list_observations_for_plan,
)
from app.services.pricing_service import (
    detect_price_change,
    get_latest_prices,
    get_plan_history,
    calculate_effective_price,
    calculate_monthly_cost,
    calculate_effective_monthly_cost,
)
from app.models.plan_offer import OfferData, sync_plan_offers, list_plan_offers, list_offer_history
from app.scraper.cultfit import CultFitScraper, PlanData, ScrapingResult
from datetime import datetime, timezone


@pytest.fixture
def db_conn():
    """In-memory SQLite database fixture."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")

    # Setup schema
    conn.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            source_url TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE membership_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            name TEXT NOT NULL,
            duration_months INTEGER NOT NULL,
            tier TEXT NOT NULL,
            sku TEXT,
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_plans_unique ON membership_plans(product_id, tier, duration_months, name);
        CREATE TABLE price_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            membership_plan_id INTEGER NOT NULL REFERENCES membership_plans(id),
            original_price REAL NOT NULL,
            selling_price REAL NOT NULL,
            discount_percentage REAL NOT NULL DEFAULT 0.0,
            offer_description TEXT,
            raw_data TEXT,
            observed_at TEXT NOT NULL
        );
        CREATE TABLE scrape_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            error_message TEXT
        );
        CREATE TABLE plan_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            membership_plan_id INTEGER NOT NULL REFERENCES membership_plans(id),
            offer_key TEXT NOT NULL,
            title TEXT, description TEXT, category TEXT, coupon_code TEXT,
            discount_type TEXT, discount_value REAL, maximum_discount REAL,
            minimum_transaction_amount REAL, eligible_payment_methods TEXT,
            validity_start TEXT, validity_end TEXT, eligibility_conditions TEXT,
            is_conditional INTEGER, is_stackable INTEGER, source_url TEXT,
            first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,
            current_status TEXT NOT NULL DEFAULT 'available',
            created_at TEXT NOT NULL,
            UNIQUE(membership_plan_id, offer_key)
        );
        CREATE TABLE offer_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL REFERENCES plan_offers(id),
            observed_at TEXT NOT NULL, status TEXT NOT NULL,
            title TEXT, description TEXT, category TEXT, coupon_code TEXT,
            discount_type TEXT, discount_value REAL, maximum_discount REAL,
            minimum_transaction_amount REAL, eligible_payment_methods TEXT,
            validity_start TEXT, validity_end TEXT, eligibility_conditions TEXT,
            is_conditional INTEGER, is_stackable INTEGER, source_url TEXT,
            UNIQUE(offer_id, observed_at, status)
        );
        """
    )
    yield conn
    conn.close()


def test_detect_price_change(db_conn):
    """Verify price change detection logic."""
    prod = insert_product(db_conn, "Cult.fit Elite", "https://next.cult.fit/sku/elite")
    plan = insert_plan(db_conn, prod.id, "Elite 12M", 12, "ELITE")

    # 1. No prior observation -> should detect change (new)
    assert detect_price_change(db_conn, plan.id, 16950.0, 19450.0, "Extra ₹2500 OFF") is True

    # Insert first observation
    insert_observation(db_conn, plan.id, 19450.0, 16950.0, 12.85, "Extra ₹2500 OFF")

    # 2. Same prices and offer -> no change
    assert detect_price_change(db_conn, plan.id, 16950.0, 19450.0, "Extra ₹2500 OFF") is False

    # 3. Selling price dropped -> change detected
    assert detect_price_change(db_conn, plan.id, 15950.0, 19450.0, "Extra ₹2500 OFF") is True

    # 4. MRP changed -> change detected
    assert detect_price_change(db_conn, plan.id, 16950.0, 21000.0, "Extra ₹2500 OFF") is True

    # 5. Offer changed -> change detected
    assert detect_price_change(db_conn, plan.id, 16950.0, 19450.0, "Free Duffle Bag") is True


def test_historical_immutability(db_conn):
    """Verify observations are appended and earlier observations remain untouched."""
    prod = insert_product(db_conn, "Cult.fit Elite", "https://next.cult.fit/sku/elite")
    plan = insert_plan(db_conn, prod.id, "Elite 12M", 12, "ELITE")

    # Insert 3 observations at different timestamps
    obs1 = insert_observation(db_conn, plan.id, 19450.0, 17500.0, 10.0, "Diwali Sale", observed_at="2026-10-01T08:00:00Z")
    obs2 = insert_observation(db_conn, plan.id, 19450.0, 16950.0, 12.85, "Black Friday", observed_at="2026-11-01T08:00:00Z")
    obs3 = insert_observation(db_conn, plan.id, 19450.0, 16500.0, 15.0, "Flash Deal", observed_at="2026-11-14T08:00:00Z")

    history = get_plan_history(db_conn, plan.id)
    assert len(history) == 3

    # Preserved in chronological order
    assert history[0]["selling_price"] == 17500.0
    assert history[0]["offer_description"] == "Diwali Sale"
    assert history[1]["selling_price"] == 16950.0
    assert history[2]["selling_price"] == 16500.0

    # Earlier records are immutable
    first = history[0]
    assert first["id"] == obs1.id
    assert first["original_price"] == 19450.0


def test_scraper_save_observations_retains_unchanged_verifications(db_conn):
    """Every successful retrieval retains an observation, including unchanged prices."""
    prod = insert_product(db_conn, "Cult.fit Elite", "https://next.cult.fit/sku/elite")
    scraper = CultFitScraper()

    mock_result = ScrapingResult(
        plans=[
            PlanData(
                tier="ELITE",
                name="Elite 12M",
                duration_months=12,
                duration_label="12 Months",
                mrp=19450.0,
                selling_price=16950.0,
                discount_pct=12.85,
                offer_string="Extra ₹2500 OFF",
                product_id_code="CULTPACK_WY",
                raw_widget_name="Elite - S1 - 12M Sku",
            )
        ],
        scraped_at=datetime.now(timezone.utc),
        source_url="https://next.cult.fit/sku/elite",
        success=True,
    )

    # First save -> 1 new, 0 unchanged
    s1 = scraper.save_observations(db_conn, mock_result, prod.id)
    assert s1["new"] == 1
    assert s1["unchanged"] == 0

    # Second save with exact same data -> 0 new, 1 unchanged
    s2 = scraper.save_observations(db_conn, mock_result, prod.id)
    assert s2["new"] == 0
    assert s2["unchanged"] == 1

    all_obs = db_conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0]
    assert all_obs == 2
    history = get_plan_history(
        db_conn, db_conn.execute("SELECT id FROM membership_plans").fetchone()[0]
    )
    assert [row["selling_price"] for row in history] == [16950.0, 16950.0]
    assert history[0]["observed_at"] < history[1]["observed_at"]


def test_offer_history_tracks_changes_and_removals(db_conn):
    prod = insert_product(db_conn, "Cult.fit Elite", "https://next.cult.fit/sku/elite")
    plan = insert_plan(db_conn, prod.id, "Elite 12M", 12, "ELITE")
    first = OfferData(
        title="Extra ₹2,500 OFF applied",
        description="Extra ₹2,500 OFF applied",
        category="Instant discount",
        discount_type="fixed",
        discount_value=2500,
        is_conditional=False,
    )
    second = OfferData(
        title="Extra ₹2,500 OFF applied",
        description="Extra ₹2,000 OFF applied",
        category="Instant discount",
        discount_type="fixed",
        discount_value=2000,
        is_conditional=False,
    )
    sync_plan_offers(db_conn, plan.id, [first], "2026-11-01T08:00:00+00:00")
    sync_plan_offers(db_conn, plan.id, [second], "2026-11-02T08:00:00+00:00")
    sync_plan_offers(db_conn, plan.id, [], "2026-11-03T08:00:00+00:00")

    current = list_plan_offers(db_conn, plan.id)
    history = list_offer_history(db_conn, plan.id)
    assert current[0]["current_status"] == "removed"
    assert [row["status"] for row in history] == ["available", "available", "removed"]


def test_offer_history_omits_unchanged_snapshots_and_tracks_coupon_changes(db_conn):
    prod = insert_product(db_conn, "Cult.fit Elite", "https://next.cult.fit/sku/elite")
    plan = insert_plan(db_conn, prod.id, "Elite 12M", 12, "ELITE")
    base = OfferData(
        title="Bank offer",
        description="Save on payment",
        category="Coupon",
        coupon_code="SAVE10",
        discount_type="percentage",
        discount_value=10,
    )
    updated = OfferData(**{**base.__dict__, "coupon_code": "SAVE15"})
    sync_plan_offers(db_conn, plan.id, [base], "2026-11-01T08:00:00+00:00")
    sync_plan_offers(db_conn, plan.id, [base], "2026-11-02T08:00:00+00:00")
    sync_plan_offers(db_conn, plan.id, [updated], "2026-11-03T08:00:00+00:00")

    history = list_offer_history(db_conn, plan.id)
    assert [row["event_type"] for row in history] == [
        "Offer introduced",
        "Coupon code changed",
    ]


def test_effective_price_separates_discount_types():
    result = calculate_effective_price(
        16950,
        [
            {
                "category": "Instant discount",
                "discount_type": "fixed",
                "discount_value": 2500,
                "is_conditional": False,
            },
            {
                "category": "Cashback",
                "discount_type": "fixed",
                "discount_value": 1200,
                "is_conditional": True,
            },
        ],
    )
    assert result["instant_discount"] == 2500
    assert result["cashback"] == 1200
    assert result["potential_effective_price"] == 14450


def test_coupon_is_not_applied_without_verified_applicability():
    result = calculate_effective_price(
        16950,
        [
            {
                "category": "Instant discount",
                "discount_type": "fixed",
                "discount_value": 2500,
                "is_conditional": False,
            },
            {
                "category": "Coupon",
                "discount_type": "fixed",
                "discount_value": 1200,
                "coupon_code": "CULT1200",
                "is_conditional": True,
            },
        ],
    )
    assert result["instant_discounted_price"] == 14450
    assert result["potential_effective_price"] == 14450
    assert result["excluded_benefit_count"] == 1
    assert calculate_effective_monthly_cost(result, 12) == 1204.17


def test_monthly_costs_use_plan_duration():
    assert calculate_monthly_cost(16950, 12) == 1412.5
    assert calculate_monthly_cost(16950, 0) is None
    assert calculate_monthly_cost(16950, None) is None

    effective = {
        "advertised_price": 16950,
        "instant_discount": 2500,
        "conditional_discount": 900,
        "cashback": 1200,
        "potential_effective_price": 13550,
    }
    assert calculate_effective_monthly_cost(effective, 12) == 1204.17
