"""Himalayas remote jobs public API adapter."""

from __future__ import annotations

from datetime import datetime
from typing import List

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)

API_URL = "https://himalayas.app/jobs/api"


class HimalayasAdapter(JobSourceAdapter):
    name = "himalayas"

    def __init__(self):
        settings = get_settings()
        self.enabled = bool(getattr(settings, "enable_himalayas", True))

    async def health_check(self) -> bool:
        return self.enabled

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not self.enabled:
            return []
        jobs: List[RawJob] = []
        headers = {"User-Agent": "job-search-agent/1.0 (personal job search)"}
        params = {"limit": min(max_results, 100)}
        try:
            async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                resp = await client.get(API_URL, params=params)
                if resp.status_code != 200:
                    logger.warning("himalayas_http", status=resp.status_code)
                    return []
                data = resp.json()
        except Exception as e:
            logger.error("himalayas_failed", error=str(e))
            return []

        rows = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []

        q_tokens = [t for t in (query or "").lower().replace("-", " ").split() if len(t) > 2 and t != "*"]
        for item in rows:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            company = str(
                (item.get("companyName") or item.get("company") or "")
                if not isinstance(item.get("company"), dict)
                else item.get("company", {}).get("name", "")
            )
            if not title:
                continue
            blob = f"{title} {item.get('excerpt', '')} {item.get('description', '')}".lower()
            if q_tokens and not any(t in blob for t in q_tokens):
                continue
            url = str(item.get("applicationLink") or item.get("url") or item.get("guid") or "")
            job_id = str(item.get("id") or item.get("slug") or url)
            posted = None
            for key in ("pubDate", "publishedDate", "createdAt"):
                raw = item.get(key)
                if not raw:
                    continue
                try:
                    posted = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
                    break
                except Exception:
                    pass
            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=job_id,
                    title=title,
                    company=company or "Unknown",
                    location="Remote",
                    description=str(item.get("description") or item.get("excerpt") or "")[:8000],
                    job_url=url,
                    application_url=url,
                    date_posted=posted,
                    date_posted_verified=DateVerification.ESTIMATED if posted else DateVerification.UNKNOWN,
                    is_remote=True,
                    remote_india_eligible=True,
                    raw_data=item,
                )
            )
            if len(jobs) >= max_results:
                break
        return jobs
