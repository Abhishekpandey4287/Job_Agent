"""Tests for discovery source integration fixes."""

import asyncio
import time

from job_agent.discovery.base import JobSourceAdapter, JobSourceRegistry
from job_agent.discovery.catalog import API_WEBSITES, list_websites, public_browser_extractors
from job_agent.discovery.browser.brave_session import (
    bind_page,
    current_page,
    resolve_parallel_tabs,
    unbind_page,
)
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


class _SlowAdapter(JobSourceAdapter):
    def __init__(self, name: str):
        self.name = name

    async def search(self, query: str, location: str, max_results: int = 50):
        await asyncio.sleep(0.25)
        return []


def test_catalog_includes_builtin_websites():
    names = {site.name for site in list_websites()}
    assert {"remotive", "greenhouse", "indeed", "web_search", "apify_linkedin"} <= names
    assert [e.name for e in public_browser_extractors()] == [
        "indeed", "cutshort", "wellfound", "foundit", "glassdoor",
    ]


def test_new_api_website_is_registered_by_appending_the_catalog():
    class ExtraBoard(JobSourceAdapter):
        name = "extra_board"

        async def search(self, query: str, location: str, max_results: int = 50):
            return []

    API_WEBSITES.append(ExtraBoard)
    try:
        names = {site.name for site in list_websites()}
        assert "extra_board" in names
    finally:
        API_WEBSITES.remove(ExtraBoard)


def test_parallel_tabs_defaults_to_one_per_site():
    assert resolve_parallel_tabs(0, 5) == 5
    assert resolve_parallel_tabs(4, 11) == 4
    assert resolve_parallel_tabs(20, 3) == 3
    assert resolve_parallel_tabs(0, 0) == 0


async def test_each_tab_keeps_its_own_page():
    seen = {}

    async def work(name: str, page: str):
        token = bind_page(page)
        try:
            await asyncio.sleep(0.05)
            seen[name] = current_page(None)
        finally:
            unbind_page(token)

    await asyncio.gather(work("indeed", "tab-indeed"), work("naukri", "tab-naukri"))
    assert seen == {"indeed": "tab-indeed", "naukri": "tab-naukri"}


async def test_search_all_runs_websites_in_parallel():
    registry = JobSourceRegistry()
    registry.register(_SlowAdapter("site_a"))
    registry.register(_SlowAdapter("site_b"))
    started = time.perf_counter()
    jobs, errors = await registry.search_all(["Android"], ["Mumbai"], max_per_source=5)
    elapsed = time.perf_counter() - started
    assert jobs == []
    assert errors == []
    assert elapsed < 0.45


def test_web_search_job_url_patterns():
    adapter = WebSearchJobsAdapter()
    assert adapter._is_job_url("https://boards.greenhouse.io/acme/jobs/12345")
    assert adapter._is_job_url("https://www.hirist.com/j/android-jobs-in-mumbai")
    assert adapter._is_job_url("https://www.shine.com/job-search/android-jobs-in-mumbai")
    assert not adapter._is_job_url("https://example.com/blog/hiring-tips")
