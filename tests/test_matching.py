"""Test match scoring."""

import pytest

from job_agent.matching.scorer import MatchScorer
from job_agent.schemas import DateVerification, NormalizedJob, RawJob
from job_agent.normalization.filters import normalize_job


@pytest.fixture
def android_job():
    raw = RawJob(
        source="test",
        title="Android Engineer",
        company="TestCorp",
        location="Mumbai, India",
        description=(
            "We are looking for an Android Engineer with 2+ years experience. "
            "Required: Kotlin, Jetpack Compose, MVVM, Coroutines, Room, Dagger/Hilt, "
            "Retrofit, REST APIs, Git, CI/CD."
        ),
        job_url="https://example.com/job/1",
        application_url="https://example.com/apply/1",
        remote_india_eligible=False,
    )
    return normalize_job(raw)


def test_high_match_android_job(resume, android_job):
    scorer = MatchScorer(resume)
    match = scorer.score(android_job)
    assert match.match_score >= 70
    assert "Kotlin" in match.matching_skills or "kotlin" in [s.lower() for s in match.matching_skills]


def test_low_match_irrelevant_job(resume):
    raw = RawJob(
        source="test",
        title="iOS Developer",
        company="Apple",
        location="Cupertino, US",
        description="Swift, SwiftUI, 5+ years iOS experience required.",
        job_url="https://example.com/job/2",
    )
    job = normalize_job(raw)
    scorer = MatchScorer(resume)
    match = scorer.score(job)
    assert match.match_score < 60


def test_score_breakdown_totals(resume, android_job):
    scorer = MatchScorer(resume)
    match = scorer.score(android_job)
    bd = match.score_breakdown
    expected = bd.technical_skills + bd.experience + bd.role_alignment + bd.seniority + bd.location + bd.industry
    assert abs(bd.total - expected) < 0.2
