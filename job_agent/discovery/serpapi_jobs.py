"""SerpAPI Google Jobs search — internet-wide discovery."""

from datetime import datetime
from typing import List
from urllib.parse import quote_plus

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.keywords import matches_track
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)


class SerpAPIJobsAdapter(JobSourceAdapter):
    name = "serpapi_google_jobs"

    async def health_check(self) -> bool:
        return bool(get_settings().serpapi_key)

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        api_key = get_settings().serpapi_key
        if not api_key:
            return []

        q = f"{query} {location} India".strip()
        jobs: List[RawJob] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(
                    "https://serpapi.com/search.json",
                    params={
                        "engine": "google_jobs",
                        "q": q,
                        "api_key": api_key,
                        "hl": "en",
                        "gl": "in",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                logger.warning("serpapi_failed", query=q, error=str(e))
                return []

            for item in data.get("jobs_results", []):
                title = item.get("title", "")
                desc = item.get("description", "")
                if not matches_track(title, desc, query):
                    continue

                company = item.get("company_name", "Unknown")
                loc = ""
                if item.get("location"):
                    loc = item["location"]
                elif item.get("detected_extensions", {}).get("schedule_type"):
                    loc = location

                posted = None
                verified = DateVerification.UNKNOWN
                ext = item.get("detected_extensions", {})
                if ext.get("posted_at"):
                    verified = DateVerification.ESTIMATED

                apply_options = item.get("apply_options") or []
                app_url = item.get("share_link", "")
                if apply_options:
                    app_url = apply_options[0].get("link") or app_url

                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=item.get("job_id", app_url),
                    title=title,
                    company=company,
                    location=loc or location,
                    description=desc[:8000],
                    job_url=app_url,
                    application_url=app_url,
                    date_posted=posted,
                    date_posted_verified=verified,
                    raw_data=item,
                ))
                if len(jobs) >= max_results:
                    break

        return jobs

    @staticmethod
    def build_queries() -> List[tuple[str, str]]:
        """Extra query matrix for internet-wide search."""
        roles = [
            "Android Engineer", "Android Developer", "Kotlin Developer",
            "Mobile Engineer", "Backend Engineer", "Java Developer",
            "Spring Boot Developer", "Java Backend Engineer",
        ]
        locations = ["Mumbai", "India", "Bengaluru", "Pune", "Remote India"]
        return [(r, loc) for r in roles for loc in locations]
