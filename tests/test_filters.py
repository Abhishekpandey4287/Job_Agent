"""Test location filtering and deduplication."""

from datetime import datetime, timedelta

import pytest

from job_agent.normalization.filters import (
    classify_location,
    deduplicate_jobs,
    filter_by_recency,
    is_android_relevant,
    normalize_job,
)
from job_agent.schemas import RawJob


def test_mumbai_location_tier():
    raw = RawJob(
        source="test", title="Android Dev", company="Co",
        location="Mumbai, Maharashtra", job_url="https://a.com/1",
    )
    assert classify_location(raw) == 1


def test_india_location_tier():
    raw = RawJob(
        source="test", title="Android Dev", company="Co",
        location="Bengaluru, Karnataka", job_url="https://a.com/2",
    )
    assert classify_location(raw) == 2


def test_remote_india_eligible():
    raw = RawJob(
        source="test", title="Android Dev", company="Co",
        location="Remote", job_url="https://a.com/3",
        is_remote=True, remote_india_eligible=True,
    )
    assert classify_location(raw) == 3


def test_remote_us_only_excluded():
    raw = RawJob(
        source="test", title="Android Dev", company="Co",
        location="Remote - US Only", job_url="https://a.com/4",
        is_remote=True,
        description="This position is US only.",
    )
    assert classify_location(raw) == 0


def test_android_relevance():
    relevant, _ = is_android_relevant(
        "Senior Android Engineer",
        "Build Android apps with Kotlin and Jetpack Compose using MVVM architecture.",
    )
    assert relevant is True

    irrelevant, _ = is_android_relevant(
        "Backend Engineer",
        "Python, Django, PostgreSQL. No mobile development.",
    )
    assert irrelevant is False


def test_deduplication():
    raw1 = RawJob(
        source="linkedin", title="Android Dev", company="Google",
        location="Mumbai", job_url="https://jobs.google.com/1",
        source_job_id="123",
    )
    raw2 = RawJob(
        source="indeed", title="Android Dev", company="Google",
        location="Mumbai", job_url="https://indeed.com/view/123",
        source_job_id="123",
    )
    jobs = [normalize_job(raw1), normalize_job(raw2)]
    deduped = deduplicate_jobs(jobs)
    assert len(deduped) == 1


def test_recency_filter():
    raw_recent = RawJob(
        source="test", title="Android Dev", company="Co",
        location="Mumbai", job_url="https://a.com/5",
        date_posted=datetime.utcnow() - timedelta(hours=12),
    )
    raw_old = RawJob(
        source="test", title="Android Dev", company="Co2",
        location="Mumbai", job_url="https://a.com/6",
        date_posted=datetime.utcnow() - timedelta(days=7),
    )
    jobs = [normalize_job(raw_recent), normalize_job(raw_old)]
    recent = filter_by_recency(jobs, hours=24)
    assert len(recent) == 1
