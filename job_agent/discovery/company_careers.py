"""Bounded company career-page discovery from high-signal job hits.

Uses known ATS public endpoints when company slug can be inferred.
Does not crawl entire websites. Never targets Naukri.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Dict, List, Set, Tuple
from urllib.parse import urlparse

import httpx

from job_agent.discovery.job_ids import is_naukri_listing
from job_agent.discovery.keywords import should_include_ats_job
from job_agent.logging_config import get_logger
from job_agent.schemas import RawJob

logger = get_logger(__name__)

_ENGINEERING_QUERY = "Android Developer OR Backend Engineer OR Software Engineer OR Kotlin OR Java"


def _slug_from_url(url: str) -> Tuple[str, str] | None:
    """Return (ats, slug) if URL clearly maps to a known public ATS board."""
    low = (url or "").lower()
    if "naukri" in low:
        return None
    m = re.search(r"boards\.greenhouse\.io/([^/?#]+)", low)
    if m:
        return "greenhouse", m.group(1)
    m = re.search(r"job-boards\.greenhouse\.io/([^/?#]+)", low)
    if m:
        return "greenhouse", m.group(1)
    m = re.search(r"jobs\.lever\.co/([^/?#]+)", low)
    if m:
        return "lever", m.group(1)
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", low)
    if m:
        return "ashby", m.group(1)
    m = re.search(r"jobs\.smartrecruiters\.com/([^/?#]+)", low)
    if m:
        return "smartrecruiters", m.group(1)
    return None


async def discover_company_careers(
    seed_jobs: List[RawJob],
    *,
    max_companies: int = 50,
    max_jobs_per_company: int = 40,
    delay_s: float = 1.0,
) -> Tuple[List[RawJob], List[str], Dict[str, int]]:
    """From discovered jobs, find ATS boards and fetch more public openings."""
    errors: List[str] = []
    found: List[RawJob] = []
    counts: Dict[str, int] = {"company_careers": 0}

    # Rank companies by frequency of appearance
    company_hits: Counter[str] = Counter()
    company_urls: Dict[str, List[str]] = {}
    for j in seed_jobs:
        if is_naukri_listing(source=j.source, job_url=j.job_url, application_url=j.application_url):
            continue
        key = (j.company or "").strip().lower()
        if not key or key == "unknown":
            continue
        company_hits[key] += 1
        company_urls.setdefault(key, []).append(j.job_url or j.application_url)

    seen_boards: Set[Tuple[str, str]] = set()
    attempted = 0

    headers = {"User-Agent": "job-search-agent/1.0 (personal job search)"}
    async with httpx.AsyncClient(timeout=25.0, headers=headers, follow_redirects=True) as client:
        for company, _hits in company_hits.most_common(max_companies * 2):
            if attempted >= max_companies:
                break
            urls = company_urls.get(company, [])
            board = None
            for u in urls:
                board = _slug_from_url(u)
                if board:
                    break
            if not board:
                continue
            ats, slug = board
            if (ats, slug) in seen_boards:
                continue
            seen_boards.add((ats, slug))
            attempted += 1

            try:
                jobs = await _fetch_board(client, ats, slug, max_jobs_per_company)
                jobs = [
                    j for j in jobs
                    if should_include_ats_job(
                        j.title, j.description or "", j.location,
                        _ENGINEERING_QUERY, "India",
                    )
                ]
                found.extend(jobs)
                counts["company_careers"] += len(jobs)
                logger.info("company_career_fetched", ats=ats, slug=slug, count=len(jobs))
            except Exception as e:
                errors.append(f"company_careers/{ats}/{slug}: {e}")
                logger.warning("company_career_failed", ats=ats, slug=slug, error=str(e)[:100])
            if delay_s > 0:
                await asyncio.sleep(delay_s)

    return found, errors, counts


async def _fetch_board(client: httpx.AsyncClient, ats: str, slug: str, limit: int) -> List[RawJob]:
    jobs: List[RawJob] = []
    if ats == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        for item in (resp.json().get("jobs") or [])[:limit]:
            jurl = item.get("absolute_url") or ""
            jobs.append(
                RawJob(
                    source=f"greenhouse:{slug}",
                    source_job_id=str(item.get("id") or ""),
                    title=str(item.get("title") or ""),
                    company=slug.replace("-", " ").title(),
                    location=str((item.get("location") or {}).get("name") or ""),
                    job_url=jurl,
                    application_url=jurl,
                    description="",
                )
            )
    elif ats == "lever":
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        for item in data[:limit]:
            jurl = item.get("hostedUrl") or item.get("applyUrl") or ""
            jobs.append(
                RawJob(
                    source=f"lever:{slug}",
                    source_job_id=str(item.get("id") or ""),
                    title=str(item.get("text") or ""),
                    company=slug.replace("-", " ").title(),
                    location=str((item.get("categories") or {}).get("location") or ""),
                    description=str(item.get("descriptionPlain") or "")[:8000],
                    job_url=jurl,
                    application_url=jurl,
                )
            )
    elif ats == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        for item in (resp.json().get("jobs") or [])[:limit]:
            jurl = item.get("jobUrl") or ""
            jobs.append(
                RawJob(
                    source=f"ashby:{slug}",
                    source_job_id=str(item.get("id") or ""),
                    title=str(item.get("title") or ""),
                    company=slug.replace("-", " ").title(),
                    location=str(item.get("location") or ""),
                    job_url=jurl,
                    application_url=jurl,
                )
            )
    return jobs
