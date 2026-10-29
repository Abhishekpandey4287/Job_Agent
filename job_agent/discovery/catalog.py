"""Registry of every job website the search knows about.

Add a website here. The orchestrator runs whatever this file lists, in parallel,
then merges the jobs into the same Excel export.

API site (JSON/HTTP, like Remotive)
    1. Create ``job_agent/discovery/<site>.py`` with a ``JobSourceAdapter`` subclass.
       Implement ``health_check`` and ``search(query, location, max_results)``.
    2. Add that class to ``API_WEBSITES`` below.

Company ATS board (Greenhouse-style, one broad scan)
    1. Same adapter shape as an API site.
    2. Add the class to ``ATS_WEBSITES`` instead.

Browser site (its own tab, like Indeed or Naukri)
    1. Add an extractor class in ``job_agent/discovery/browser/portals.py``
       with ``name`` and ``async def search(self, session, query, location, max_results)``.
    2. Append an instance to ``PORTAL_EXTRACTORS`` in that file.
    3. Add the ``name`` to ``BRAVE_PORTALS`` in ``.env`` so the logged-in browser opens it.
    4. If it also works without a login, add the name to ``PUBLIC_BROWSER_SITES`` below.

``JobicyAdapter`` in ``jobicy.py`` is already written. Add it to ``API_WEBSITES``
to start searching Jobicy.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, List, Sequence, Type

from job_agent.discovery.adzuna import AdzunaAdapter
from job_agent.discovery.arbeitnow import ArbeitnowAdapter
from job_agent.discovery.ashby import AshbyAdapter
from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.greenhouse import GreenhouseAdapter
from job_agent.discovery.himalayas import HimalayasAdapter
from job_agent.discovery.jsearch import JSearchAdapter
from job_agent.discovery.lever import LeverAdapter
from job_agent.discovery.remotive import RemotiveAdapter
from job_agent.discovery.remoteok import RemoteOKAdapter
from job_agent.discovery.smartrecruiters import SmartRecruitersAdapter
from job_agent.discovery.source_stats import DiscoveryStatsCollector
from job_agent.discovery.weworkremotely import WeWorkRemotelyAdapter
from job_agent.logging_config import get_logger
from job_agent.schemas import RawJob

logger = get_logger(__name__)

# Queried with several role keywords. Append a JobSourceAdapter subclass.
API_WEBSITES: List[Type[JobSourceAdapter]] = [
    RemotiveAdapter,
    ArbeitnowAdapter,
    RemoteOKAdapter,
    HimalayasAdapter,
    WeWorkRemotelyAdapter,
    AdzunaAdapter,
    JSearchAdapter,
]

# One broad board scan (query="*", location="India"). Append a subclass.
ATS_WEBSITES: List[Type[JobSourceAdapter]] = [
    GreenhouseAdapter,
    LeverAdapter,
    AshbyAdapter,
    SmartRecruitersAdapter,
]

# Portal ``name`` values that the no-login browser fallback is allowed to open.
# The extractor still has to be listed in portals.PORTAL_EXTRACTORS.
PUBLIC_BROWSER_SITES = frozenset({
    "indeed",
    "cutshort",
    "wellfound",
    "foundit",
    "glassdoor",
})


@dataclass
class SearchContext:
    """Shared inputs for one discovery run. Website collectors only read this."""

    queries: List[str]
    locations: List[str]
    profiles: list
    max_per_source: int
    settings: object
    stats: DiscoveryStatsCollector
    errors: List[str]
    cdp_url: str = ""
    brave_used: bool = False
    public_browser_used: bool = False


@dataclass(frozen=True)
class Website:
    """One registered search target."""

    name: str
    kind: str  # api | ats | browser | custom
    collect: Callable[[SearchContext], object]


def browser_extractors() -> list:
    """Every browser portal extractor, in portal-list order."""
    from job_agent.discovery.browser.portals import PORTAL_EXTRACTORS

    return list(PORTAL_EXTRACTORS)


def public_browser_extractors() -> list:
    by_name = {extractor.name: extractor for extractor in browser_extractors()}
    ordered = ("indeed", "cutshort", "wellfound", "foundit", "glassdoor")
    return [by_name[name] for name in ordered if name in by_name and name in PUBLIC_BROWSER_SITES]


def build_api_adapters() -> List[JobSourceAdapter]:
    return [cls() for cls in API_WEBSITES]


def build_ats_adapters() -> List[JobSourceAdapter]:
    return [cls() for cls in ATS_WEBSITES]


def list_websites() -> List[Website]:
    """API, ATS, browser, and custom sites the main search will run."""
    sites: List[Website] = []
    for cls in API_WEBSITES:
        adapter = cls()
        sites.append(Website(adapter.name, "api", _api_collector(adapter)))
    for cls in ATS_WEBSITES:
        adapter = cls()
        sites.append(Website(adapter.name, "ats", _ats_collector(adapter)))
    for extractor in browser_extractors():
        sites.append(Website(extractor.name, "browser", _noop_collect))
    sites.append(Website("web_search", "custom", _collect_web))
    sites.append(Website("serpapi_google_jobs", "custom", _collect_serp))
    sites.append(Website("apify_linkedin", "custom", _collect_apify))
    sites.append(Website("browser", "custom", _collect_browser))
    return sites


def parallel_collectors(ctx: SearchContext) -> Sequence:
    """Coroutines to run together. Browser portals share one multi-tab session."""
    tasks = []
    for site in list_websites():
        if site.kind == "browser":
            continue
        tasks.append(site.collect(ctx))
    return tasks


async def _noop_collect(_ctx: SearchContext) -> List[RawJob]:
    return []


def _api_collector(adapter: JobSourceAdapter) -> Callable[[SearchContext], object]:
    async def _run(ctx: SearchContext) -> List[RawJob]:
        if not await adapter.health_check():
            ctx.stats.record_disabled(adapter.name, "disabled or missing key")
            print(f"  [{adapter.name}] DISABLED")
            return []
        queries = ctx.queries[:8] or ["Android", "Backend", "Java"]
        delay = float(getattr(ctx.settings, "request_delay_seconds", 1) or 0.5)
        cap = min(ctx.max_per_source, 100)
        collected: List[RawJob] = []
        try:
            for query in queries[:4]:
                jobs = await adapter.search(query, "Remote", cap)
                collected.extend(jobs)
                await asyncio.sleep(delay)
            ctx.stats.record_success(adapter.name, len(collected))
            print(f"  [{adapter.name}] {len(collected)} jobs")
        except Exception as e:
            ctx.errors.append(f"{adapter.name}: {e}")
            ctx.stats.record_blocked(adapter.name, str(e)[:100])
            print(f"  [{adapter.name}] FAILED: {e}")
        return collected

    return _run


def _ats_collector(adapter: JobSourceAdapter) -> Callable[[SearchContext], object]:
    async def _run(ctx: SearchContext) -> List[RawJob]:
        if not await adapter.health_check():
            ctx.stats.record_disabled(adapter.name, "not configured / empty boards")
            return []
        try:
            jobs = await adapter.search("*", "India", min(80, ctx.max_per_source))
            ctx.stats.record_success(adapter.name, len(jobs))
            print(f"  [{adapter.name}] {len(jobs)} jobs")
            return jobs
        except Exception as e:
            ctx.errors.append(f"{adapter.name}: {e}")
            ctx.stats.record_blocked(adapter.name, str(e)[:100])
            print(f"  [{adapter.name}] FAILED: {e}")
            return []

    return _run


async def _collect_browser(ctx: SearchContext) -> List[RawJob]:
    from job_agent.discovery.brave_browser import DEFAULT_SEARCH_MATRIX, BraveBrowserAdapter
    from job_agent.discovery.public_browser import PUBLIC_SEARCH_MATRIX, PublicBrowserAdapter

    settings = ctx.settings
    if settings.brave_browser_enabled and ctx.cdp_url:
        try:
            settings.brave_cdp_url = ctx.cdp_url
            brave = BraveBrowserAdapter()
            focused = [
                (q, loc)
                for q in ctx.queries[:5]
                for loc in ctx.locations[:3]
            ]
            matrix = focused or DEFAULT_SEARCH_MATRIX
            jobs, errs, portal_counts, blocked = await brave.search_batch(
                search_matrix=matrix,
                max_per_combo=30,
            )
            ctx.errors.extend(errs)
            ctx.stats.merge_portal_counts(portal_counts, blocked)
            ctx.brave_used = True
            print(f"  Brave browser jobs: {len(jobs)}")
            return jobs
        except Exception as e:
            ctx.errors.append(f"brave_browser: {e}")
            ctx.stats.record_blocked("brave_browser", str(e)[:100])
            print(f"  Brave FAILED: {e}")
            return []
    if settings.brave_browser_enabled:
        public = PublicBrowserAdapter()
        matrix = [(q, loc) for q in ctx.queries[:8] for loc in ("Mumbai", "India", "Remote")]
        try:
            jobs, errs = await public.search_batch(
                search_matrix=matrix or PUBLIC_SEARCH_MATRIX,
                max_per_combo=40,
            )
            ctx.errors.extend(errs)
            ctx.public_browser_used = len(jobs) > 0
            by_portal: dict[str, int] = {}
            for job in jobs:
                portal = job.source.replace("public_", "")
                by_portal[portal] = by_portal.get(portal, 0) + 1
            for portal, count in by_portal.items():
                ctx.stats.record_success(portal, count)
            print(f"  Public browser jobs: {len(jobs)}")
            return jobs
        except Exception as e:
            ctx.errors.append(f"public_browser: {e}")
            ctx.stats.record_blocked("public_browser", str(e)[:100])
    return []


async def _collect_web(ctx: SearchContext) -> List[RawJob]:
    from job_agent.discovery.web_search_jobs import WebSearchJobsAdapter
    from job_agent.profiles.query_generator import generate_web_search_queries

    web = WebSearchJobsAdapter()
    if not await web.health_check():
        ctx.stats.record_disabled("web_search", "disabled")
        return []
    collected: List[RawJob] = []
    try:
        dynamic = generate_web_search_queries(ctx.profiles, max_per_profile=6)
        ws_jobs, ws_errs = await web.search_internet(
            max_total=min(200, ctx.max_per_source)
        )
        collected.extend(ws_jobs)
        ctx.errors.extend(ws_errs)
        breaker_hit = any("circuit breaker" in err.lower() for err in ws_errs)
        if not breaker_hit:
            delay = float(getattr(ctx.settings, "request_delay_seconds", 1) or 1)
            for query in dynamic[:12]:
                try:
                    extra = await web._search_query(query, "India", 15)
                    collected.extend(extra)
                except Exception as e:
                    ctx.errors.append(f"web_search/{query[:40]}: {e}")
                await asyncio.sleep(delay)
        else:
            ctx.errors.append("web_search: skipped dynamic extras after circuit breaker")
        ctx.stats.record_success("web_search", len(ws_jobs))
        print(f"  [web_search] {len(ws_jobs)}+ dynamic extras")
    except Exception as e:
        ctx.errors.append(f"web_search: {e}")
        ctx.stats.record_blocked("web_search", str(e)[:100])
    return collected


async def _collect_serp(ctx: SearchContext) -> List[RawJob]:
    from job_agent.discovery.serpapi_jobs import SerpAPIJobsAdapter

    serp = SerpAPIJobsAdapter()
    if not await serp.health_check():
        ctx.stats.record_disabled("serpapi", "API key not configured")
        return []
    collected: List[RawJob] = []
    for query in ctx.queries[:10]:
        for loc in ctx.locations[:3]:
            try:
                jobs = await serp.search(query, loc, 15)
                collected.extend(jobs)
            except Exception as e:
                ctx.errors.append(f"serpapi/{query}: {e}")
    ctx.stats.record_success("serpapi_google_jobs", len(collected))
    print(f"  [serpapi] {len(collected)} jobs")
    return collected


async def _collect_apify(ctx: SearchContext) -> List[RawJob]:
    from job_agent.discovery.apify_linkedin import ApifyLinkedInJobsAdapter

    settings = ctx.settings
    apify = ApifyLinkedInJobsAdapter()
    if not await apify.health_check():
        if not settings.apify_linkedin_enabled:
            ctx.stats.record_disabled(
                "apify_linkedin", "disabled (APIFY_LINKEDIN_ENABLED=false)"
            )
        else:
            ctx.stats.record_disabled("apify_linkedin", "APIFY_API_TOKEN not set")
        return []
    try:
        titles = ctx.queries[:12]
        locs = [loc for loc in ctx.locations if loc.lower() not in ("remote india",)][:6]
        apify_jobs, apify_errs = await apify.search_batch(
            titles=titles or ["Android Developer", "Backend Engineer"],
            locations=locs or ["Mumbai", "India", "Remote"],
            max_per_search=settings.apify_linkedin_max_rows,
        )
        ctx.errors.extend(apify_errs)
        if apify_jobs:
            ctx.stats.record_success("apify_linkedin", len(apify_jobs))
        elif any("not rented" in err.lower() for err in apify_errs):
            ctx.stats.record_disabled("apify_linkedin", "Actor not rented")
        else:
            ctx.stats.record_success("apify_linkedin", 0)
        print(f"  [apify_linkedin] {len(apify_jobs)} jobs")
        for err in apify_errs[:3]:
            print(f"  ! {err}")
        return apify_jobs
    except Exception as e:
        ctx.errors.append(f"apify_linkedin: {e}")
        ctx.stats.record_blocked("apify_linkedin", str(e)[:100])
        return []
