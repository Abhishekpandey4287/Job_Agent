"""RemoteOK public API adapter (https://remoteok.com/api)."""

from __future__ import annotations

from datetime import datetime
from typing import List

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)

API_URL = "https://remoteok.com/api"


class RemoteOKAdapter(JobSourceAdapter):
    name = "remoteok"

    def __init__(self):
        settings = get_settings()
        self.enabled = bool(getattr(settings, "enable_remoteok", True))

    async def health_check(self) -> bool:
        return self.enabled

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not self.enabled:
            return []
        jobs: List[RawJob] = []
        headers = {"User-Agent": "job-search-agent/1.0 (personal job search)"}
        try:
            async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                resp = await client.get(API_URL)
                if resp.status_code != 200:
                    logger.warning("remoteok_http", status=resp.status_code)
                    return []
                data = resp.json()
        except Exception as e:
            logger.error("remoteok_failed", error=str(e))
            return []

        q = (query or "").lower().strip()
        # Skip metadata first row if present
        rows = data[1:] if isinstance(data, list) and data and "legal" in str(data[0]).lower() else data
        if not isinstance(rows, list):
            return []

        for item in rows:
            if not isinstance(item, dict):
                continue
            title = str(item.get("position") or item.get("title") or "")
            company = str(item.get("company") or "")
            if not title:
                continue
            blob = f"{title} {item.get('tags', [])} {item.get('description', '')}".lower()
            if q and q not in ("*", "") and not all(tok in blob for tok in q.split()[:3] if len(tok) > 2):
                # Soft filter: at least one meaningful token
                tokens = [t for t in q.replace("-", " ").split() if len(t) > 2]
                if tokens and not any(t in blob for t in tokens):
                    continue
            job_id = str(item.get("id") or item.get("slug") or "")
            url = str(item.get("url") or item.get("apply_url") or "")
            if url and not url.startswith("http"):
                url = f"https://remoteok.com{url}"
            posted = None
            epoch = item.get("epoch") or item.get("date")
            try:
                if isinstance(epoch, (int, float)):
                    posted = datetime.utcfromtimestamp(int(epoch))
            except Exception:
                pass
            tags = item.get("tags") or []
            loc = "Remote"
            if isinstance(tags, list) and any("india" in str(t).lower() for t in tags):
                loc = "Remote India"
            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=job_id or url,
                    title=title,
                    company=company or "Unknown",
                    location=loc,
                    description=str(item.get("description") or "")[:8000],
                    job_url=url,
                    application_url=url,
                    date_posted=posted,
                    date_posted_verified=DateVerification.VERIFIED if posted else DateVerification.UNKNOWN,
                    is_remote=True,
                    remote_india_eligible=True,
                    raw_data=item if isinstance(item, dict) else {},
                )
            )
            if len(jobs) >= max_results:
                break
        return jobs
