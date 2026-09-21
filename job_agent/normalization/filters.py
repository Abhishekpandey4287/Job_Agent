"""Job normalization, filtering, and deduplication."""

import re
from datetime import datetime, timedelta
from typing import List, Optional, Set
from urllib.parse import urlparse

from job_agent.config import get_settings
from job_agent.database.repository import generate_job_id
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, NormalizedJob, RawJob, SeniorityLevel

logger = get_logger(__name__)

ANDROID_KEYWORDS = [
    "android", "kotlin", "mobile developer", "mobile engineer",
    "mobile software", "jetpack compose", "android sdk",
    "android application", "android platform",
]

ANDROID_TITLE_PATTERNS = [
    r"android\s+(developer|engineer|architect|lead|software)",
    r"mobile\s+(developer|engineer|architect|lead|software)",
    r"kotlin\s+(developer|engineer)",
    r"software\s+engineer\s*[-–]\s*(android|mobile)",
    r"(android|mobile)\s+software\s+engineer",
]

MUMBAI_LOCATIONS = ["mumbai", "navi mumbai", "thane", "mmr", "mumbai metropolitan"]
INDIA_LOCATIONS = [
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "delhi", "ncr",
    "noida", "gurgaon", "gurugram", "chennai", "kolkata", "ahmedabad",
    "remote india",
]

INDIA_EXCLUDE_REMOTE = [
    "us only", "usa only", "eu only", "europe only", "uk only",
    "united states only", "canada only",
]


def normalize_job(raw: RawJob) -> NormalizedJob:
    job_id = generate_job_id(raw.company, raw.title, raw.job_url, raw.source_job_id)
    location_tier = classify_location(raw)
    is_relevant, reason = is_android_relevant(raw.title, raw.description)
    seniority = detect_seniority(raw.title, raw.description)
    app_url = raw.application_url or raw.job_url

    return NormalizedJob(
        job_id=job_id,
        source=raw.source,
        source_job_id=raw.source_job_id,
        title=raw.title,
        company=raw.company,
        location=raw.location,
        location_tier=location_tier,
        description=raw.description,
        job_url=raw.job_url,
        application_url=app_url,
        date_posted=raw.date_posted,
        date_posted_verified=raw.date_posted_verified,
        employment_type=raw.employment_type,
        seniority=seniority,
        salary=raw.salary,
        is_remote=raw.is_remote,
        remote_india_eligible=raw.remote_india_eligible or False,
        is_android_relevant=is_relevant,
        relevance_reason=reason,
        company_url=str((raw.raw_data or {}).get("company_url") or ""),
    )


def classify_location(raw: RawJob) -> int:
    loc = raw.location.lower()
    desc = raw.description.lower()
    combined = f"{loc} {desc}"

    if any(m in combined for m in MUMBAI_LOCATIONS):
        return 1
    if any(i in combined for i in INDIA_LOCATIONS):
        return 2
    if raw.is_remote:
        if raw.remote_india_eligible is True:
            return 3
        if raw.remote_india_eligible is False:
            return 0
        if _remote_india_eligible(combined):
            return 3
    return 0


def _remote_india_eligible(text: str) -> bool:
    if any(ex in text for ex in INDIA_EXCLUDE_REMOTE):
        return "india" in text or "worldwide" in text or "anywhere" in text
    return any(s in text for s in ["worldwide", "anywhere", "global", "india eligible", "remote - india"])


def is_android_relevant(title: str, description: str) -> tuple[bool, str]:
    title_lower = title.lower()
    desc_lower = description.lower()[:2000]

    for pattern in ANDROID_TITLE_PATTERNS:
        if re.search(pattern, title_lower, re.IGNORECASE):
            return True, f"Title matches Android/mobile pattern: {title}"

    title_hits = sum(1 for k in ANDROID_KEYWORDS if k in title_lower)
    desc_hits = sum(1 for k in ANDROID_KEYWORDS if k in desc_lower)

    if title_hits >= 1 and desc_hits >= 2:
        return True, "Title and description contain Android/mobile keywords"

    if title_hits >= 2:
        return True, "Title strongly indicates Android/mobile role"

    minor_indicators = ["ios and android", "mobile (ios/android)", "cross-platform mobile"]
    if any(m in title_lower for m in minor_indicators) and "android" in desc_lower:
        return True, "Cross-platform mobile role with Android component"

    if desc_hits >= 4 and ("android" in desc_lower or "kotlin" in desc_lower):
        return True, "Description strongly indicates Android development focus"

    return False, "Insufficient Android/mobile relevance"


def detect_seniority(title: str, description: str) -> SeniorityLevel:
    text = f"{title} {description[:500]}".lower()
    if any(s in text for s in ["staff engineer", "staff android", "staff mobile"]):
        return SeniorityLevel.STAFF
    if any(s in text for s in ["lead", "tech lead", "team lead"]):
        return SeniorityLevel.LEAD
    if any(s in text for s in ["senior", "sr.", "sr "]):
        return SeniorityLevel.SENIOR
    if any(s in text for s in ["junior", "jr.", "entry level", "graduate"]):
        return SeniorityLevel.JUNIOR
    if "intern" in text:
        return SeniorityLevel.INTERN
    return SeniorityLevel.MID


def filter_by_recency(jobs: List[NormalizedJob], hours: int = 24) -> List[NormalizedJob]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    dated = []
    undated = []
    for job in jobs:
        if job.date_posted is None:
            undated.append(job)
            continue
        posted = job.date_posted.replace(tzinfo=None) if job.date_posted.tzinfo else job.date_posted
        if posted >= cutoff:
            dated.append(job)
    if dated:
        return dated
    logger.warning("recency_no_dated_jobs", undated=len(undated))
    return undated


def filter_relevant_jobs(jobs: List[NormalizedJob]) -> List[NormalizedJob]:
    return [j for j in jobs if j.is_android_relevant and j.location_tier > 0]


def deduplicate_jobs(jobs: List[NormalizedJob]) -> List[NormalizedJob]:
    seen_ids: Set[str] = set()
    seen_urls: Set[str] = set()
    result: List[NormalizedJob] = []

    sorted_jobs = sorted(jobs, key=lambda j: (
        -j.location_tier,
        0 if j.application_url and "greenhouse" in j.application_url else 1,
        0 if j.date_posted_verified == DateVerification.VERIFIED else 1,
    ))

    for job in sorted_jobs:
        if job.job_id in seen_ids:
            continue
        url_key = _normalize_url(job.job_url)
        if url_key and url_key in seen_urls:
            continue
        seen_ids.add(job.job_id)
        if url_key:
            seen_urls.add(url_key)
        result.append(job)

    logger.info("deduplication_complete", input=len(jobs), output=len(result))
    return result


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.lower().strip())
    return f"{parsed.netloc}{parsed.path.rstrip('/')}"
