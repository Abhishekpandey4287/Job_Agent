"""Greenhouse public job board API — company career pages."""

from datetime import datetime
from typing import List

import httpx

from job_agent.discovery.boards_loader import greenhouse_boards
from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.keywords import should_include_ats_job
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)


class GreenhouseAdapter(JobSourceAdapter):
    name = "greenhouse"

    async def health_check(self) -> bool:
        return len(greenhouse_boards()) > 0

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        boards = greenhouse_boards()
        if not boards:
            return []

        jobs: List[RawJob] = []
        async with httpx.AsyncClient(timeout=25.0) as client:
            for board in boards:
                if len(jobs) >= max_results:
                    break
                list_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
                try:
                    resp = await client.get(list_url)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                except httpx.HTTPError:
                    continue

                for item in data.get("jobs", []):
                    title = item.get("title", "")
                    loc_name = ""
                    locs = item.get("location", {})
                    if isinstance(locs, dict):
                        loc_name = locs.get("name", "")
                    elif isinstance(locs, str):
                        loc_name = locs

                    if query not in ("", "*") and not should_include_ats_job(
                        title, "", loc_name, query, location
                    ):
                        continue

                    job_id = item.get("id", "")
                    absolute_url = item.get("absolute_url", "")
                    description = ""
                    posted = None
                    verified = DateVerification.UNKNOWN

                    try:
                        detail = await client.get(
                            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
                        )
                        if detail.status_code == 200:
                            d = detail.json()
                            description = d.get("content", "")[:8000]
                            if d.get("updated_at"):
                                posted = datetime.fromisoformat(
                                    d["updated_at"].replace("Z", "+00:00")
                                ).replace(tzinfo=None)
                                verified = DateVerification.VERIFIED
                    except httpx.HTTPError:
                        pass

                    jobs.append(RawJob(
                        source=f"greenhouse:{board}",
                        source_job_id=str(job_id),
                        title=title,
                        company=board.replace("-", " ").title(),
                        location=loc_name or location,
                        description=description,
                        job_url=absolute_url,
                        application_url=absolute_url,
                        date_posted=posted,
                        date_posted_verified=verified,
                        is_remote="remote" in loc_name.lower(),
                        raw_data=item,
                    ))
                    if len(jobs) >= max_results:
                        break

        return jobs
