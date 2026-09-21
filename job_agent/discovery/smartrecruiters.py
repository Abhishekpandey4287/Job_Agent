"""SmartRecruiters public postings API."""

from datetime import datetime
from typing import List

import httpx

from job_agent.discovery.boards_loader import smartrecruiters_companies
from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.keywords import should_include_ats_job
from job_agent.schemas import DateVerification, RawJob


class SmartRecruitersAdapter(JobSourceAdapter):
    name = "smartrecruiters"

    async def health_check(self) -> bool:
        return len(smartrecruiters_companies()) > 0

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        companies = smartrecruiters_companies()
        if not companies:
            return []

        jobs: List[RawJob] = []
        async with httpx.AsyncClient(timeout=25.0) as client:
            for company in companies:
                if len(jobs) >= max_results:
                    break
                url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
                try:
                    resp = await client.get(url, params={"limit": 100})
                    if resp.status_code != 200:
                        continue
                    content = resp.json().get("content", [])
                except httpx.HTTPError:
                    continue

                for item in content:
                    title = item.get("name", "")
                    loc_data = item.get("location") or {}
                    full_loc = f"{loc_data.get('city', '')} {loc_data.get('country', '')}".strip()
                    if query not in ("", "*") and not should_include_ats_job(
                        title, "", full_loc, query, location
                    ):
                        continue

                    posted = None
                    verified = DateVerification.UNKNOWN
                    released = item.get("releasedDate")
                    if released:
                        try:
                            posted = datetime.fromisoformat(released.replace("Z", "+00:00")).replace(tzinfo=None)
                            verified = DateVerification.VERIFIED
                        except (ValueError, TypeError):
                            pass

                    ref = item.get("ref", "")
                    apply_url = item.get("applyUrl") or ref
                    jobs.append(RawJob(
                        source=f"smartrecruiters:{company}",
                        source_job_id=item.get("id", ref),
                        title=title,
                        company=company.replace("-", " ").title(),
                        location=full_loc or location,
                        description="",
                        job_url=ref,
                        application_url=apply_url,
                        date_posted=posted,
                        date_posted_verified=verified,
                        raw_data=item,
                    ))
                    if len(jobs) >= max_results:
                        break

        return jobs
