import sqlite3
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import get_db, init_db
from app.models.scrape_run import create_run, get_latest_run, update_run
from app.models.product import get_or_create_product
from app.scraper.cultfit import CultFitScraper, PlanData, ScrapingResult
from app.main import app
from app.routes.dashboard import format_dt
from app import scheduler


class _FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, *args, **kwargs):
        self.jobs.append((args, kwargs))


def test_startup_catchup_is_scheduled_only_when_today_is_incomplete(
    tmp_path, monkeypatch
):
    db_path = str(tmp_path / "catchup.db")
    init_db(db_path)
    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)

    assert scheduler.schedule_startup_catchup() is True
    assert fake_scheduler.jobs[-1][1]["id"].startswith("cultfit-startup-catchup-")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = create_run(conn)
        update_run(conn, run.id, "success")

    fake_scheduler.jobs.clear()
    assert scheduler.schedule_startup_catchup() is False
    assert fake_scheduler.jobs == []


def test_failed_attempts_schedule_15_30_and_60_minute_retries(tmp_path, monkeypatch):
    db_path = str(tmp_path / "retry.db")
    init_db(db_path)
    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)

    for _ in range(4):
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            run = create_run(conn)
            update_run(conn, run.id, "failed", "temporary outage")
        scheduler._schedule_retry(db_path)

    assert len(fake_scheduler.jobs) == 3
    assert [job[1]["id"].rsplit("-", 1)[-1] for job in fake_scheduler.jobs] == [
        "1",
        "2",
        "3",
    ]


def test_successful_attempt_with_zero_changes_is_recorded_as_success(
    tmp_path, monkeypatch
):
    db_path = str(tmp_path / "success.db")
    init_db(db_path)
    fake_scheduler = _FakeScheduler()

    class FakeScraper(CultFitScraper):
        def scrape(self):
            return _result()

    monkeypatch.setattr(scheduler, "CultFitScraper", FakeScraper)
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    with get_db(db_path) as conn:
        product = get_or_create_product(conn, "Cult.fit Elite", scheduler.BASE_URL)
        CultFitScraper().save_observations(conn, _result(), product.id)

    assert scheduler.run_scrape_attempt(db_path) is True
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = get_latest_run(conn)
        assert conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 2
    assert run.status == "success"
    assert run.completed_at is not None
    assert fake_scheduler.jobs == []


def test_automatic_attempt_does_not_duplicate_a_successful_day(tmp_path, monkeypatch):
    db_path = str(tmp_path / "duplicate.db")
    init_db(db_path)

    class FakeScraper(CultFitScraper):
        def scrape(self):
            return _result()

    monkeypatch.setattr(scheduler, "CultFitScraper", FakeScraper)
    assert scheduler.run_scrape_attempt(db_path) is True
    assert scheduler.run_scrape_attempt(db_path, skip_if_success=True) is True

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0]
    assert count == 1


def test_failed_attempt_records_error_and_schedules_retry(tmp_path, monkeypatch):
    db_path = str(tmp_path / "failure.db")
    init_db(db_path)
    fake_scheduler = _FakeScheduler()

    class FakeScraper:
        def scrape(self):
            return SimpleNamespace(success=False, error="network unavailable")

    monkeypatch.setattr(scheduler, "CultFitScraper", FakeScraper)
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)

    assert scheduler.run_scrape_attempt(db_path) is False
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = get_latest_run(conn)
    assert run.status == "failed"
    assert run.error_message == "network unavailable"
    assert len(fake_scheduler.jobs) == 1


def test_scheduled_run_skips_when_today_already_succeeded(monkeypatch):
    monkeypatch.setattr(scheduler, "has_successful_scrape_today", lambda conn: True)
    called = []
    monkeypatch.setattr(scheduler, "run_scrape_attempt", lambda *_: called.append(True))

    scheduler.run_scheduled_scrape()

    assert called == []


def _result(price=16950):
    return ScrapingResult(
        plans=[
            PlanData(
                tier="ELITE", name="ELITE 12M", duration_months=12,
                duration_label="12 Months", mrp=19450, selling_price=price,
                discount_pct=12.85, offer_string="Extra ₹2500 OFF",
                product_id_code="CULTPACK_WY", raw_widget_name="Elite 12M",
            )
        ],
        scraped_at=datetime.now(timezone.utc),
        source_url="https://next.cult.fit/sku/elite",
        success=True,
    )


@pytest.mark.parametrize("price", [15950, 16950])
def test_scrape_persists_changed_and_unchanged_prices_before_success(
    tmp_path, monkeypatch, price
):
    db_path = str(tmp_path / "verified.db")
    init_db(db_path)
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    with get_db(db_path) as conn:
        product = get_or_create_product(conn, "Cult.fit Elite", scheduler.BASE_URL)
        CultFitScraper().save_observations(conn, _result(), product.id)
    monkeypatch.setattr(CultFitScraper, "scrape", lambda self: _result(price))

    def record_run(conn, run_id, status, **kwargs):
        if status == "success":
            with get_db(db_path) as reader:
                assert reader.execute(
                    "SELECT COUNT(*) FROM price_observations"
                ).fetchone()[0] == 2
        return update_run(conn, run_id, status, **kwargs)

    monkeypatch.setattr(scheduler, "update_run", record_run)
    assert scheduler.run_scrape_attempt(db_path)
    with get_db(db_path) as conn:
        run = get_latest_run(conn)
        observations = conn.execute(
            "SELECT * FROM price_observations ORDER BY observed_at"
        ).fetchall()
    assert run.status == "success"
    assert len(observations) == 2
    latest = observations[-1]
    assert latest["selling_price"] == price
    assert run.started_at <= latest["observed_at"] <= run.completed_at

    client = TestClient(app)
    history = client.get(
        f"/api/plans/{latest['membership_plan_id']}/history/?days=7"
    ).json()
    assert history[-1]["id"] == latest["id"]
    table_body = client.get("/").text.split('id="observationsBody">')[1].split("</tbody>")[0]
    assert format_dt(latest["observed_at"]) in table_body
    assert client.get("/api/scraper/status/").json()["today_state"] == "complete"


def test_observation_persistence_failure_rolls_back_and_marks_run_failed(
    tmp_path, monkeypatch
):
    db_path = str(tmp_path / "write-failure.db")
    init_db(db_path)
    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    monkeypatch.setattr(CultFitScraper, "scrape", lambda self: _result())

    from app.scraper import cultfit
    real_insert = cultfit.insert_observation

    def failing_insert(*args, **kwargs):
        real_insert(*args, **kwargs)
        raise sqlite3.OperationalError("observation write failed")

    monkeypatch.setattr(cultfit, "insert_observation", failing_insert)
    assert scheduler.run_scrape_attempt(db_path) is False
    with get_db(db_path) as conn:
        run = get_latest_run(conn)
        assert conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 0
    assert run.status == "failed"
    assert run.completed_at is not None
    assert "observation write failed" in run.error_message
    assert len(fake_scheduler.jobs) == 1


@pytest.mark.parametrize(
    ("observed_at", "display_date"),
    [
        ("2026-11-14T18:29:00+00:00", "14 Nov 2026, 11:59 PM"),
        ("2026-11-14T18:30:00+00:00", "15 Nov 2026, 12:00 AM"),
    ],
)
def test_dashboard_keeps_observations_across_ist_midnight(
    tmp_path, monkeypatch, observed_at, display_date
):
    db_path = str(tmp_path / "midnight.db")
    init_db(db_path)
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    fixed_time = datetime.fromisoformat(observed_at)

    class ObservationClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_time.astimezone(tz)

    monkeypatch.setattr("app.scraper.cultfit.datetime", ObservationClock)
    monkeypatch.setattr("app.models.price_observation.datetime", ObservationClock)
    with get_db(db_path) as conn:
        product = get_or_create_product(conn, "Cult.fit Elite", scheduler.BASE_URL)
        CultFitScraper().save_observations(conn, _result(), product.id)

    client = TestClient(app)
    history = client.get("/api/plans/1/history/?days=7").json()
    assert history[-1]["observed_at"] == observed_at
    table_body = client.get("/").text.split('id="observationsBody">')[1].split("</tbody>")[0]
    assert display_date in table_body
