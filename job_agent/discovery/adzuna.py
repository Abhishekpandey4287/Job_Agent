"""Adzuna job search API adapter (India supported)."""

from datetime import datetime
from typing import List
from urllib.parse import quote

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.schemas import DateVerification, RawJob


class AdzunaAdapter(JobSourceAdapter):
    name = "adzuna"
    BASE_URL = "https://api.adzuna.com/v1/api/jobs/in/search/1"

    def __init__(self):
        settings = get_settings()
        self.app_id = settings.adzuna_app_id
        self.app_key = settings.adzuna_app_key

    async def health_check(self) -> bool:
        return bool(self.app_id and self.app_key)

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not await self.health_check():
            return []

        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": query,
            "where": location,
            "results_per_page": min(max_results, 50),
            "sort_by": "date",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self.BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        jobs: List[RawJob] = []
        for item in data.get("results", []):
            posted = None
            verified = DateVerification.UNKNOWN
            if item.get("created"):
                try:
                    posted = datetime.fromisoformat(item["created"].replace("Z", "+00:00"))
                    verified = DateVerification.VERIFIED
                except (ValueError, TypeError):
                    pass

            redirect_url = item.get("redirect_url", "")
            company = item.get("company", {}).get("display_name", "Unknown")
            location_display = item.get("location", {}).get("display_name", location)

            jobs.append(RawJob(
                source=self.name,
                source_job_id=str(item.get("id", "")),
                title=item.get("title", ""),
                company=company,
                location=location_display,
                description=item.get("description", ""),
                job_url=redirect_url,
                application_url=redirect_url,
                date_posted=posted,
                date_posted_verified=verified,
                salary=_format_salary(item.get("salary_min"), item.get("salary_max")),
                is_remote="remote" in location_display.lower(),
                raw_data=item,
            ))
        return jobs


def _format_salary(min_sal, max_sal) -> str:
    if min_sal and max_sal:
        return f"₹{min_sal:,} - ₹{max_sal:,}"
    if min_sal:
        return f"₹{min_sal:,}+"
    if max_sal:
        return f"Up to ₹{max_sal:,}"
    return ""
