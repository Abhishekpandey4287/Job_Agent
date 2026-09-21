"""Location tier: India-wide + worldwide remote in scope."""

from job_agent.normalization.filters import classify_location
from job_agent.schemas import RawJob


def _raw(**kw):
    return RawJob(
        source="t",
        title=kw.get("title", "Android Engineer"),
        company="Acme",
        location=kw.get("location", ""),
        description=kw.get("description", ""),
        job_url="https://example.com",
        is_remote=kw.get("is_remote", False),
        remote_india_eligible=kw.get("remote_india_eligible"),
    )


def test_india_cities_are_in_scope():
    for city in ("Bangalore", "Hyderabad", "Pune", "Chennai", "Noida", "Kochi", "Indore", "Pan India"):
        assert classify_location(_raw(location=city)) >= 2


def test_mumbai_is_tier_1():
    assert classify_location(_raw(location="Mumbai")) == 1


def test_worldwide_remote_is_in_scope():
    tier = classify_location(_raw(location="Remote Worldwide", is_remote=True, remote_india_eligible=None))
    assert tier == 4


def test_us_only_remote_rejected():
    tier = classify_location(
        _raw(location="Remote", is_remote=True, description="US only remote role", remote_india_eligible=False)
    )
    assert tier == 0


def test_india_remote_tier_3():
    assert classify_location(_raw(location="Remote India", is_remote=True, remote_india_eligible=True)) == 3
