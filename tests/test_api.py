"""
Integration tests for FastAPI endpoints: dashboard and JSON APIs.
"""

import os
import pytest
from fastapi.testclient import TestClient

# Ensure DB_PATH uses test db
os.environ["DB_PATH"] = ":memory:"

from app.main import app
from app.database import init_db, get_db
from app.models.product import get_or_create_product
from app.models.membership_plan import insert_plan
from app.models.price_observation import insert_observation
from app.models.scrape_run import create_run, update_run


@pytest.fixture
def client(tmp_path):
    """Test client with isolated SQLite database."""
    test_db = str(tmp_path / "test_cultfit.db")
    init_db(test_db)

    with get_db(test_db) as conn:
        prod = get_or_create_product(conn, "Cult.fit Elite", "https://next.cult.fit/sku/elite")
        p12 = insert_plan(conn, prod.id, "ELITE 12M", 12, "ELITE", sku="CULTPACK_WY")
        p6 = insert_plan(conn, prod.id, "ELITE 6M", 6, "ELITE", sku="CULTPACK_6M")
        insert_observation(conn, p12.id, 19450.0, 16950.0, 12.85, "Extra ₹2500 OFF")
        insert_observation(conn, p6.id, 15850.0, 13850.0, 12.62, "Flat 12% OFF")
        run = create_run(conn)
        update_run(conn, run.id, "success")

    # Patch DB_PATH in config
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.config.DB_PATH", test_db)
        with TestClient(app) as test_client:
            yield test_client


def test_dashboard_renders(client):
    """Verify GET / returns 200 and renders HTML dashboard."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Cult.fit Price Tracker" in resp.text
    assert "Refresh Price" in resp.text
    assert "ELITE" in resp.text


def test_api_products(client):
    """Verify GET /api/products/ returns products."""
    resp = client.get("/api/products/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["name"] == "Cult.fit Elite"


def test_api_plans(client):
    """Verify GET /api/plans/ returns plans."""
    resp = client.get("/api/plans/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    names = [p["name"] for p in data]
    assert "ELITE 12M" in names


def test_api_plan_history(client):
    """Verify GET /api/plans/{id}/history/ returns price history."""
    # First get plan id
    plans_resp = client.get("/api/plans/")
    plan_id = plans_resp.json()[0]["id"]

    resp = client.get(f"/api/plans/{plan_id}/history/")
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) >= 1
    assert "selling_price" in history[0]
    assert "observed_at" in history[0]


def test_api_plan_statistics(client):
    """Verify GET /api/plans/{id}/statistics/ returns computed stats."""
    plans_resp = client.get("/api/plans/")
    plan_id = plans_resp.json()[0]["id"]

    resp = client.get(f"/api/plans/{plan_id}/statistics/")
    assert resp.status_code == 200
    stats = resp.json()
    assert "min_price" in stats
    assert "avg_price" in stats
    assert "current_price" in stats
    assert stats["current_price"] > 0


def test_api_scraper_status(client):
    """Verify GET /api/scraper/status/ returns run details."""
    resp = client.get("/api/scraper/status/")
    assert resp.status_code == 200
    status = resp.json()
    assert status["status"] in ("success", "running", "never_run", "failed")
    assert status["today_status"] == "success"
    assert status["today_state"] == "complete"
    assert status["today_last_attempt"]["status"] == "success"
    assert status["last_successful"]["completed_at"] is not None
    assert status["next_scheduled_run"]
