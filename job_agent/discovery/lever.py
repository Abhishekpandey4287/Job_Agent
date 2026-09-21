"""Lever public postings API — company career pages."""

from datetime import datetime
from typing import List

import httpx

from job_agent.discovery.boards_loader import lever_companies
from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.keywords import should_include_ats_job
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)


class LeverAdapter(JobSourceAdapter):
    name = "lever"

    async def health_check(self) -> bool:
        return len(lever_companies()) > 0

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        companies = lever_companies()
        if not companies:
            return []

        jobs: List[RawJob] = []
        async with httpx.AsyncClient(timeout=25.0) as client:
            for company in companies:
                if len(jobs) >= max_results:
                    break
                url = f"https://api.lever.co/v0/postings/{company}?mode=json"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    postings = resp.json()
                except httpx.HTTPError:
                    continue

                for item in postings:
                    title = item.get("text", "")
                    desc = item.get("descriptionPlain", "") or item.get("description", "")[:3000]
                    loc = item.get("categories", {}).get("location", "") or ""
                    if query not in ("", "*") and not should_include_ats_job(
                        title, desc, loc, query, location
                    ):
                        continue

                    posted = None
                    verified = DateVerification.UNKNOWN
                    created = item.get("createdAt")
                    if created:
                        try:
                            posted = datetime.fromtimestamp(created / 1000)
                            verified = DateVerification.VERIFIED
                        except (ValueError, TypeError, OSError):
                            pass

                    host = item.get("hostedUrl") or item.get("applyUrl") or ""
                    jobs.append(RawJob(
                        source=f"lever:{company}",
                        source_job_id=item.get("id", host),
                        title=title,
                        company=company.replace("-", " ").title(),
                        location=loc or location,
                        description=desc[:8000],
                        job_url=host,
                        application_url=host,
                        date_posted=posted,
                        date_posted_verified=verified,
                        is_remote="remote" in loc.lower(),
                        raw_data=item,
                    ))
                    if len(jobs) >= max_results:
                        break

        return jobs
