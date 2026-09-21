"""Internet-wide discovery orchestrator — ATS, search engines, career pages."""

from typing import Dict, List, Tuple

from job_agent.discovery.ashby import AshbyAdapter
from job_agent.discovery.greenhouse import GreenhouseAdapter
from job_agent.discovery.lever import LeverAdapter
from job_agent.discovery.serpapi_jobs import SerpAPIJobsAdapter
from job_agent.discovery.smartrecruiters import SmartRecruitersAdapter
from job_agent.discovery.web_search_jobs import WebSearchJobsAdapter
from job_agent.logging_config import get_logger
from job_agent.schemas import RawJob

logger = get_logger(__name__)

ATS_ADAPTERS = [
    GreenhouseAdapter(),
    LeverAdapter(),
    AshbyAdapter(),
    SmartRecruitersAdapter(),
]


async def discover_internet_wide(
    max_per_ats: int = 80,
    max_web_search: int = 150,
) -> Tuple[List[RawJob], List[str], Dict[str, int]]:
    """Search company career pages, ATS platforms, and the open web.

    Returns (jobs, errors, source_counts).
    """
    all_jobs: List[RawJob] = []
    errors: List[str] = []
    source_counts: Dict[str, int] = {}

    for adapter in ATS_ADAPTERS:
        if not await adapter.health_check():
            source_counts[adapter.name] = 0
            continue
        try:
            jobs = await adapter.search("*", "India", max_per_ats)
            source_counts[adapter.name] = len(jobs)
            all_jobs.extend(jobs)
            logger.info("internet_ats_scan_complete", source=adapter.name, count=len(jobs))
        except Exception as e:
            errors.append(f"{adapter.name}: {e}")
            source_counts[adapter.name] = 0
            logger.error("internet_ats_scan_failed", source=adapter.name, error=str(e))

    web = WebSearchJobsAdapter()
    if await web.health_check():
        try:
            ws_jobs, ws_errs = await web.search_internet(max_total=max_web_search)
            all_jobs.extend(ws_jobs)
            errors.extend(ws_errs)
            source_counts["web_search"] = len(ws_jobs)
            logger.info("internet_web_search_complete", count=len(ws_jobs))
        except Exception as e:
            errors.append(f"web_search: {e}")
            source_counts["web_search"] = 0
    else:
        source_counts["web_search"] = 0

    serp = SerpAPIJobsAdapter()
    if await serp.health_check():
        serp_count = 0
        for query, location in SerpAPIJobsAdapter.build_queries()[:12]:
            try:
                jobs = await serp.search(query, location, 15)
                serp_count += len(jobs)
                all_jobs.extend(jobs)
            except Exception as e:
                errors.append(f"serpapi/{query}: {e}")
        source_counts["serpapi"] = serp_count
        logger.info("internet_serpapi_complete", count=serp_count)
    else:
        source_counts["serpapi"] = 0

    return all_jobs, errors, source_counts
