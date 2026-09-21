"""Freshness classification — undated jobs are never treated as fresh."""

from datetime import datetime, timedelta

from job_agent.discovery.freshness import (
    FreshnessBucket,
    classify_freshness,
    filter_fresh_jobs,
    is_fresh_enough,
)
from job_agent.schemas import DateVerification, NormalizedJob


def _job(hours_ago=None, **kw):
    posted = None
    if hours_ago is not None:
        posted = datetime.utcnow() - timedelta(hours=hours_ago)
    return NormalizedJob(
        job_id=kw.get("job_id", "x"),
        source="test",
        title="Android Engineer",
        company="Acme",
        location=kw.get("location", "Mumbai"),
        job_url="https://example.com/j",
        date_posted=posted,
        date_posted_verified=kw.get("verified", DateVerification.VERIFIED),
        location_tier=kw.get("tier", 1),
        is_remote=kw.get("remote", False),
    )


def test_classify_buckets():
    assert classify_freshness(_job(6).date_posted) == FreshnessBucket.H0_24
    assert classify_freshness(_job(30).date_posted) == FreshnessBucket.H24_48
    assert classify_freshness(_job(72).date_posted) == FreshnessBucket.OLD
    assert classify_freshness(None) == FreshnessBucket.UNKNOWN


def test_unknown_never_fresh_by_default():
    assert not is_fresh_enough(_job())
    assert is_fresh_enough(_job(), allow_unknown=True)


def test_filter_excludes_old_and_unknown():
    jobs = [_job(6), _job(30), _job(100), _job()]
    fresh = filter_fresh_jobs(jobs, max_age_hours=48)
    assert len(fresh) == 2
    assert all(j.date_posted is not None for j in fresh)


def test_48h_boundary():
    assert is_fresh_enough(_job(47))
    assert not is_fresh_enough(_job(49))
