"""Jobicy public remote-jobs API adapter (https://jobicy.com/api/v2/remote-jobs)."""

from __future__ import annotations

from datetime import datetime
from typing import List

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)

API_URL = "https://jobicy.com/api/v2/remote-jobs"


class JobicyAdapter(JobSourceAdapter):
    name = "jobicy"

    def __init__(self):
        settings = get_settings()
        self.enabled = bool(getattr(settings, "enable_jobicy", True))

    async def health_check(self) -> bool:
        return self.enabled

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not self.enabled:
            return []

        # Jobicy caps count around 100; pull a large page then filter locally.
        params = {"count": min(max(max_results, 50), 100)}
        q = (query or "").lower().strip()
        if q and q not in ("*", "") and len(q) < 40:
            # Tag filter is soft — Jobicy tags are coarse (e.g. "software-dev").
            params["tag"] = "software-dev"

        headers = {"User-Agent": "job-search-agent/1.0 (personal job search)"}
        try:
            async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                resp = await client.get(API_URL, params=params)
                if resp.status_code != 200:
                    logger.warning("jobicy_http", status=resp.status_code)
                    return []
                data = resp.json()
        except Exception as e:
            logger.error("jobicy_failed", error=str(e)[:160])
            return []

        rows = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []

        jobs: List[RawJob] = []
        tokens = [t for t in q.replace("-", " ").split() if len(t) > 2] if q and q != "*" else []

        for item in rows:
            if not isinstance(item, dict):
                continue
            title = str(item.get("jobTitle") or item.get("title") or "")
            company = str(item.get("companyName") or item.get("company") or "Unknown")
            if not title:
                continue
            blob = f"{title} {item.get('jobIndustry', '')} {item.get('jobType', '')} {item.get('jobExcerpt', '')}".lower()
            if tokens and not any(t in blob for t in tokens):
                continue

            posted = None
            verified = DateVerification.UNKNOWN
            for key in ("pubDate", "jobPubDate", "publishedDate", "date"):
                raw = item.get(key)
                if not raw:
                    continue
                try:
                    if isinstance(raw, (int, float)):
                        posted = datetime.utcfromtimestamp(int(raw))
                    else:
                        posted = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    verified = DateVerification.VERIFIED
                    break
                except (ValueError, TypeError, OSError):
                    continue

            geo = str(item.get("jobGeo") or item.get("jobLocation") or "Remote Worldwide")
            url = str(item.get("url") or item.get("jobUrl") or item.get("applicationUrl") or "")
            india = "india" in geo.lower()
            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=str(item.get("id") or item.get("jobId") or ""),
                    title=title,
                    company=company,
                    location=geo if geo else "Remote Worldwide",
                    description=str(item.get("jobDescription") or item.get("jobExcerpt") or "")[:8000],
                    job_url=url,
                    application_url=url,
                    date_posted=posted,
                    date_posted_verified=verified,
                    salary=str(item.get("annualSalaryMin") or item.get("salary") or "") or "",
                    is_remote=True,
                    remote_india_eligible=True if india else None,
                    raw_data=item,
                )
            )
            if len(jobs) >= max_results:
                break
        return jobs
