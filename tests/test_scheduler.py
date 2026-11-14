import sqlite3
from types import SimpleNamespace

from app.database import init_db
from app.models.scrape_run import create_run, get_latest_run, update_run
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

    class FakeScraper:
        def scrape(self):
            return SimpleNamespace(success=True, error=None)

        def save_observations(self, conn, result, product_id):
            return {"new": 0, "unchanged": 1}

    monkeypatch.setattr(scheduler, "CultFitScraper", FakeScraper)
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)

    assert scheduler.run_scrape_attempt(db_path) is True
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = get_latest_run(conn)
    assert run.status == "success"
    assert run.completed_at is not None
    assert fake_scheduler.jobs == []


def test_automatic_attempt_does_not_duplicate_a_successful_day(tmp_path, monkeypatch):
    db_path = str(tmp_path / "duplicate.db")
    init_db(db_path)

    class FakeScraper:
        def scrape(self):
            return SimpleNamespace(success=True, error=None)

        def save_observations(self, conn, result, product_id):
            return {"new": 0, "unchanged": 1}

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
