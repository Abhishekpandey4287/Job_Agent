"""ATS jobs use score≥70 and a longer age window than board ≤24h gate."""

from datetime import datetime, timedelta

from job_agent.discovery.job_ids import is_ats_source, job_looks_like_ats
from job_agent.discovery.orchestrator import _ats_fresh_enough, _is_ats_job, _is_expired
from job_agent.schemas import NormalizedJob


def _job(**kw) -> NormalizedJob:
    base = dict(
        job_id="j1",
        source="shine",
        title="Android Developer",
        company="Acme",
        location="Mumbai",
        location_tier=1,
        job_url="https://example.com/job/1",
        date_posted=datetime.utcnow() - timedelta(hours=10),
    )
    base.update(kw)
    return NormalizedJob(**base)


def test_ats_source_detection():
    assert is_ats_source("greenhouse:stripe")
    assert is_ats_source("lever:acme")
    assert is_ats_source("ashby:foo")
    assert is_ats_source("greenhouse_careers")
    assert not is_ats_source("shine")
    assert not is_ats_source("indeed")
    assert job_looks_like_ats(job_url="https://boards.greenhouse.io/x/jobs/99")
    assert job_looks_like_ats(job_url="https://jobs.lever.co/acme/abcdef12-3456-7890-abcd-ef1234567890")


def test_ats_age_window_vs_board():
    ats = _job(
        source="greenhouse:acme",
        date_posted=datetime.utcnow() - timedelta(days=5),
        job_url="https://boards.greenhouse.io/acme/jobs/1",
    )
    assert _is_ats_job(ats)
    assert _ats_fresh_enough(ats, 14)
    assert _is_expired(ats, 1)  # would fail board ≤24h soft gate
    assert not _is_expired(ats, 14)

    undated = _job(source="lever:z", date_posted=None, job_url="https://jobs.lever.co/z/abc")
    assert _ats_fresh_enough(undated, 14)
