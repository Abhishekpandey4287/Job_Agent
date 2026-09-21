"""Test application state management."""

import pytest

from job_agent.application.manager import ApplicationManager
from job_agent.database.models import init_db
from job_agent.database.repository import JobRepository
from job_agent.normalization.filters import normalize_job
from job_agent.schemas import ApplicationStatus, RawJob


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from job_agent.database import models
    models._engine = None
    models._SessionLocal = None
    init_db()


def test_status_transitions():
    raw = RawJob(
        source="test", title="Android Dev", company="Co",
        location="Mumbai", job_url="https://test.com/1",
    )
    job = normalize_job(raw)
    repo = JobRepository()
    repo.upsert_job(job, application_status=ApplicationStatus.RESUME_GENERATED.value)

    mgr = ApplicationManager()
    assert mgr.approve_application(job.job_id)
    assert mgr.start_application(job.job_id)
    assert mgr.mark_applied(job.job_id, "Applied via company portal")
    mgr.close()
    repo.close()

    repo2 = JobRepository()
    record = repo2.get_by_job_id(job.job_id)
    assert record.application_status == ApplicationStatus.APPLIED.value
    repo2.close()
