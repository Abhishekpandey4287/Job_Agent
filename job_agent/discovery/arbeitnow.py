"""Arbeitnow public API for remote jobs."""

from datetime import datetime
from typing import List

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.schemas import DateVerification, RawJob


class ArbeitnowAdapter(JobSourceAdapter):
    name = "arbeitnow"
    BASE_URL = "https://www.arbeitnow.com/api/job-board-api"
    _cache: list | None = None

    def __init__(self):
        settings = get_settings()
        self.enabled = settings.arbeitnow_enabled

    async def health_check(self) -> bool:
        return self.enabled

    async def _fetch_all(self) -> list:
        if ArbeitnowAdapter._cache is not None:
            return ArbeitnowAdapter._cache
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self.BASE_URL)
            resp.raise_for_status()
            ArbeitnowAdapter._cache = resp.json().get("data", [])
        return ArbeitnowAdapter._cache

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not self.enabled:
            return []

        all_data = await self._fetch_all()
        jobs: List[RawJob] = []
        keywords = [
            "android", "mobile", "kotlin", "java", "backend", "spring boot",
            "springboot", "microservices",
        ]

        for item in all_data:
            title = item.get("title", "")
            tags = " ".join(item.get("tags", []))
            combined = f"{title} {tags}".lower()

            if not any(k in combined for k in keywords):
                continue

            remote = item.get("remote", False)
            job_location = item.get("location", "Remote" if remote else location)
            description = item.get("description", "")
            india_eligible = _check_india_eligible(job_location, description) if remote else False

            posted = None
            verified = DateVerification.UNKNOWN
            if item.get("created_at"):
                try:
                    posted = datetime.fromtimestamp(item["created_at"])
                    verified = DateVerification.VERIFIED
                except (ValueError, TypeError, OSError):
                    pass

            slug = item.get("slug", "")
            url = f"https://www.arbeitnow.com/jobs/{slug}" if slug else ""

            jobs.append(RawJob(
                source=self.name,
                source_job_id=slug,
                title=title,
                company=item.get("company_name", "Unknown"),
                location=job_location,
                description=description,
                job_url=url,
                application_url=url,
                date_posted=posted,
                date_posted_verified=verified,
                is_remote=remote,
                remote_india_eligible=india_eligible,
                raw_data=item,
            ))

            if len(jobs) >= max_results:
                break

        return jobs


def _check_india_eligible(location: str, description: str) -> bool:
    text = f"{location} {description}".lower()
    return any(s in text for s in ["worldwide", "anywhere", "global", "india", "no location"])
