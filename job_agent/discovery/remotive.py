"""Remotive public API for remote jobs."""

from datetime import datetime
from typing import List

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.schemas import DateVerification, RawJob


class RemotiveAdapter(JobSourceAdapter):
    name = "remotive"
    BASE_URL = "https://remotive.com/api/remote-jobs"
    _cache: list | None = None

    def __init__(self):
        settings = get_settings()
        self.enabled = settings.remotive_enabled

    async def health_check(self) -> bool:
        return self.enabled

    async def _fetch_all(self) -> list:
        if RemotiveAdapter._cache is not None:
            return RemotiveAdapter._cache
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self.BASE_URL)
            resp.raise_for_status()
            RemotiveAdapter._cache = resp.json().get("jobs", [])
        return RemotiveAdapter._cache

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not self.enabled:
            return []

        all_jobs_data = await self._fetch_all()
        jobs: List[RawJob] = []
        query_lower = query.lower()
        keywords = [
            "android", "mobile", "kotlin", "java", "backend", "spring boot",
            "springboot", "microservices",
        ]

        for item in all_jobs_data:
            title = item.get("title", "")
            category = item.get("category", "")
            tags = " ".join(item.get("tags", []))

            combined = f"{title} {category} {tags}".lower()
            if not any(k in combined for k in keywords):
                continue

            candidate_location = item.get("candidate_required_location", "")
            india_eligible = _check_india_eligible(candidate_location, item.get("description", ""))

            posted = None
            verified = DateVerification.UNKNOWN
            if item.get("publication_date"):
                try:
                    posted = datetime.fromisoformat(item["publication_date"])
                    verified = DateVerification.VERIFIED
                except (ValueError, TypeError):
                    pass

            jobs.append(RawJob(
                source=self.name,
                source_job_id=str(item.get("id", "")),
                title=title,
                company=item.get("company_name", "Unknown"),
                location=candidate_location or "Remote",
                description=item.get("description", ""),
                job_url=item.get("url", ""),
                application_url=item.get("url", ""),
                date_posted=posted,
                date_posted_verified=verified,
                salary=item.get("salary", "") or "",
                is_remote=True,
                remote_india_eligible=india_eligible,
                raw_data=item,
            ))

            if len(jobs) >= max_results:
                break

        return jobs


def _check_india_eligible(location: str, description: str) -> bool:
    text = f"{location} {description}".lower()
    india_signals = [
        "worldwide", "anywhere", "global", "india", "asia",
        "no location restriction", "remote - worldwide", "emea",
    ]
    exclude_signals = ["us only", "usa only", "eu only", "europe only", "uk only", "canada only"]

    if any(ex in text for ex in exclude_signals):
        if "india" not in text and "worldwide" not in text and "anywhere" not in text:
            return False
    return any(sig in text for sig in india_signals)
