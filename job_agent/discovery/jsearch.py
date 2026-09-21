"""JSearch API adapter via RapidAPI (Indeed, LinkedIn aggregated)."""

from datetime import datetime
from typing import List

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.schemas import DateVerification, RawJob


class JSearchAdapter(JobSourceAdapter):
    name = "jsearch"
    BASE_URL = "https://jsearch.p.rapidapi.com/search"

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.rapidapi_key

    async def health_check(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not await self.health_check():
            return []

        headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        params = {
            "query": f"{query} in {location}",
            "page": "1",
            "num_pages": "1",
            "date_posted": "today",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self.BASE_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        jobs: List[RawJob] = []
        for item in data.get("data", [])[:max_results]:
            posted = None
            verified = DateVerification.UNKNOWN
            if item.get("job_posted_at_datetime_utc"):
                try:
                    posted = datetime.fromisoformat(
                        item["job_posted_at_datetime_utc"].replace("Z", "+00:00")
                    )
                    verified = DateVerification.VERIFIED
                except (ValueError, TypeError):
                    pass

            apply_link = item.get("job_apply_link", item.get("job_google_link", ""))
            is_remote = item.get("job_is_remote", False)

            jobs.append(RawJob(
                source=self.name,
                source_job_id=item.get("job_id", ""),
                title=item.get("job_title", ""),
                company=item.get("employer_name", "Unknown"),
                location=item.get("job_city", location) or location,
                description=item.get("job_description", ""),
                job_url=apply_link,
                application_url=apply_link,
                date_posted=posted,
                date_posted_verified=verified,
                salary=item.get("job_salary", "") or "",
                is_remote=is_remote,
                raw_data=item,
            ))
        return jobs
