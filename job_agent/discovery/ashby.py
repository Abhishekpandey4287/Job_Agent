"""Ashby public job board API."""

from datetime import datetime
from typing import List

import httpx

from job_agent.discovery.boards_loader import ashby_orgs
from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.keywords import should_include_ats_job
from job_agent.schemas import DateVerification, RawJob


class AshbyAdapter(JobSourceAdapter):
    name = "ashby"

    async def health_check(self) -> bool:
        return len(ashby_orgs()) > 0

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        orgs = ashby_orgs()
        if not orgs:
            return []

        jobs: List[RawJob] = []
        async with httpx.AsyncClient(timeout=25.0) as client:
            for org in orgs:
                if len(jobs) >= max_results:
                    break
                url = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                except httpx.HTTPError:
                    continue

                for item in data.get("jobs", []):
                    title = item.get("title", "")
                    desc = item.get("descriptionPlain", "") or item.get("descriptionHtml", "")[:3000]
                    loc = item.get("location", "") or ""
                    if isinstance(loc, dict):
                        loc = loc.get("name", "")
                    if query not in ("", "*") and not should_include_ats_job(
                        title, desc, str(loc), query, location
                    ):
                        continue

                    posted = None
                    verified = DateVerification.UNKNOWN
                    pub = item.get("publishedAt") or item.get("updatedAt")
                    if pub:
                        try:
                            posted = datetime.fromisoformat(pub.replace("Z", "+00:00")).replace(tzinfo=None)
                            verified = DateVerification.VERIFIED
                        except (ValueError, TypeError):
                            pass

                    job_url = item.get("jobUrl") or item.get("applyUrl") or ""
                    jobs.append(RawJob(
                        source=f"ashby:{org}",
                        source_job_id=item.get("id", job_url),
                        title=title,
                        company=org.replace("-", " ").title(),
                        location=str(loc) or location,
                        description=str(desc)[:8000],
                        job_url=job_url,
                        application_url=job_url,
                        date_posted=posted,
                        date_posted_verified=verified,
                        is_remote="remote" in str(loc).lower(),
                        raw_data=item,
                    ))
                    if len(jobs) >= max_results:
                        break

        return jobs
