"""Tests for discovery source integration fixes."""

from job_agent.discovery.browser.portals import _slugify
from job_agent.discovery.keywords import matches_track, should_include_ats_job
from job_agent.discovery.web_search_jobs import WebSearchJobsAdapter


def test_matches_track_java_keyword():
    assert matches_track("Software Engineer", "", "Java Developer")


def test_should_include_ats_job_by_location():
    assert should_include_ats_job(
        "Product Manager",
        "",
        "Mumbai, India",
        "Android Developer",
        "India",
    )


def test_should_include_ats_job_broad_scan_skipped_at_adapter():
    """Broad ATS scan uses query='*' to skip adapter-level filtering."""
    assert should_include_ats_job("Accountant", "", "New York, USA", "*", "India") is False


def test_slugify_for_portal_urls():
    assert _slugify("Java Backend Developer") == "java-backend-developer"
    assert _slugify("Mumbai") == "mumbai"


def test_web_search_job_url_patterns():
    adapter = WebSearchJobsAdapter()
    assert adapter._is_job_url("https://boards.greenhouse.io/acme/jobs/12345")
    assert adapter._is_job_url("https://www.hirist.com/j/android-jobs-in-mumbai")
    assert adapter._is_job_url("https://www.shine.com/job-search/android-jobs-in-mumbai")
    assert not adapter._is_job_url("https://example.com/blog/hiring-tips")
