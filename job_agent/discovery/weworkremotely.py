"""We Work Remotely — public RSS category feeds (no auth)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)

# Public category RSS feeds
FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
]


class WeWorkRemotelyAdapter(JobSourceAdapter):
    name = "weworkremotely"

    def __init__(self):
        settings = get_settings()
        self.enabled = bool(getattr(settings, "enable_wwr", True))

    async def health_check(self) -> bool:
        return self.enabled

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not self.enabled:
            return []
        jobs: List[RawJob] = []
        headers = {"User-Agent": "job-search-agent/1.0 (personal job search)"}
        q_tokens = [t for t in (query or "").lower().replace("-", " ").split() if len(t) > 2 and t != "*"]

        try:
            async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
                for feed in FEEDS:
                    try:
                        resp = await client.get(feed)
                        if resp.status_code != 200:
                            continue
                        root = ET.fromstring(resp.text)
                    except Exception as e:
                        logger.warning("wwr_feed_failed", feed=feed, error=str(e)[:80])
                        continue

                    for item in root.findall(".//item"):
                        title = (item.findtext("title") or "").strip()
                        link = (item.findtext("link") or "").strip()
                        desc = (item.findtext("description") or "").strip()
                        if not title or not link:
                            continue
                        blob = f"{title} {desc}".lower()
                        if q_tokens and not any(t in blob for t in q_tokens):
                            continue
                        # Title often "Company: Role"
                        company, role = "Unknown", title
                        if ":" in title:
                            company, role = [p.strip() for p in title.split(":", 1)]
                        posted = None
                        pub = item.findtext("pubDate")
                        if pub:
                            try:
                                posted = parsedate_to_datetime(pub).replace(tzinfo=None)
                            except Exception:
                                pass
                        sid = re.search(r"/(\d+)", link)
                        jobs.append(
                            RawJob(
                                source=self.name,
                                source_job_id=sid.group(1) if sid else link,
                                title=role,
                                company=company,
                                location="Remote",
                                description=re.sub(r"<[^>]+>", " ", desc)[:8000],
                                job_url=link,
                                application_url=link,
                                date_posted=posted,
                                date_posted_verified=(
                                    DateVerification.VERIFIED if posted else DateVerification.UNKNOWN
                                ),
                                is_remote=True,
                                remote_india_eligible=True,
                            )
                        )
                        if len(jobs) >= max_results:
                            return jobs
        except Exception as e:
            logger.error("wwr_failed", error=str(e))
        return jobs
