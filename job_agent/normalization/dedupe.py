"""Aggressive but careful job deduplication across sources."""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple
from urllib.parse import urlparse

from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, NormalizedJob

logger = get_logger(__name__)


def _canon_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.lower().strip())
    path = parsed.path.rstrip("/")
    # Drop tracking query; keep identity query keys
    return f"{parsed.netloc}{path}"


def _norm_company(name: str) -> str:
    n = (name or "").lower().strip()
    n = re.sub(r"\b(inc|llc|ltd|pvt|private|limited|technologies|technology|labs|solutions)\b\.?", "", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _norm_title(title: str) -> str:
    t = (title or "").lower().strip()
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(r"[^a-z0-9+#.\s]+", " ", t)
    # Collapse seniority noise for fuzzy key but keep junior/senior distinction lightly
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _norm_location(loc: str) -> str:
    l = (loc or "").lower()
    if "remote" in l:
        if "india" in l:
            return "remote-india"
        return "remote"
    for city in ("mumbai", "thane", "navi mumbai", "pune", "bengaluru", "bangalore",
                 "hyderabad", "chennai", "delhi", "noida", "gurgaon", "gurugram"):
        if city in l:
            return "bangalore" if city == "bengaluru" else city
    if "india" in l:
        return "india"
    return re.sub(r"\s+", " ", l)[:40]


def _fuzzy_key(job: NormalizedJob) -> str:
    return f"{_norm_company(job.company)}|{_norm_title(job.title)}|{_norm_location(job.location)}"


def _prefer(a: NormalizedJob, b: NormalizedJob) -> NormalizedJob:
    """Prefer greenhouse/direct ATS URLs, verified dates, richer descriptions."""
    def score(j: NormalizedJob) -> tuple:
        url = (j.application_url or j.job_url or "").lower()
        ats = 1 if any(x in url for x in ("greenhouse", "lever.co", "ashby", "workday")) else 0
        verified = 1 if j.date_posted_verified == DateVerification.VERIFIED else 0
        desc = len(j.description or "")
        return (ats, verified, desc, j.location_tier)

    return a if score(a) >= score(b) else b


def deduplicate_jobs_aggressive(jobs: List[NormalizedJob]) -> List[NormalizedJob]:
    """Deduplicate by job_id, canonical URL, then company+title+location.

    Different cities / seniority variants remain separate.
    Merges source names into job.relevance_reason prefix when collapsing.
    """
    by_id: Dict[str, NormalizedJob] = {}
    by_url: Dict[str, str] = {}  # url -> job_id
    by_fuzzy: Dict[str, str] = {}  # fuzzy -> job_id
    sources_map: Dict[str, Set[str]] = {}

    sorted_jobs = sorted(
        jobs,
        key=lambda j: (
            -j.location_tier,
            0 if j.date_posted_verified == DateVerification.VERIFIED else 1,
            -(len(j.description or "")),
        ),
    )

    for job in sorted_jobs:
        sources_map.setdefault(job.job_id, set()).add(job.source)
        url_key = _canon_url(job.job_url) or _canon_url(job.application_url)
        fuzzy = _fuzzy_key(job)

        existing_id = None
        if job.job_id in by_id:
            existing_id = job.job_id
        elif url_key and url_key in by_url:
            existing_id = by_url[url_key]
        elif fuzzy and fuzzy in by_fuzzy:
            existing_id = by_fuzzy[fuzzy]

        if existing_id is None:
            by_id[job.job_id] = job
            if url_key:
                by_url[url_key] = job.job_id
            if fuzzy:
                by_fuzzy[fuzzy] = job.job_id
            continue

        # Merge into existing
        existing = by_id[existing_id]
        sources_map[existing_id].add(job.source)
        sources_map[existing_id].update(sources_map.get(job.job_id, set()))
        winner = _prefer(existing, job)
        # Keep richer description
        if len(job.description or "") > len(winner.description or ""):
            winner.description = job.description
        if job.application_url and "greenhouse" in job.application_url.lower():
            winner.application_url = job.application_url
        by_id[existing_id] = winner
        if url_key:
            by_url[url_key] = existing_id
        if fuzzy:
            by_fuzzy[fuzzy] = existing_id

    result: List[NormalizedJob] = []
    for jid, job in by_id.items():
        srcs = sorted(sources_map.get(jid, {job.source}))
        if len(srcs) > 1:
            # Preserve multi-source info without breaking schema — stash in relevance_reason tag
            tag = f"[sources: {', '.join(srcs)}] "
            if tag not in (job.relevance_reason or ""):
                job.relevance_reason = tag + (job.relevance_reason or "")
        result.append(job)

    logger.info(
        "dedupe_aggressive_complete",
        input=len(jobs),
        output=len(result),
        collapsed=len(jobs) - len(result),
    )
    return result
