"""Shared role/location matching for all discovery adapters."""

from datetime import datetime, timedelta
from typing import Optional

ANDROID_KEYWORDS = [
    "android", "kotlin", "mobile developer", "mobile engineer",
    "mobile software", "jetpack", "android sdk",
]

BACKEND_KEYWORDS = [
    "backend", "java developer", "java engineer", "spring boot",
    "springboot", "microservices", "server-side", "api developer",
    "java backend", "software engineer - java", "software engineer - backend",
]

HYBRID_KEYWORDS = [
    "full stack mobile", "full-stack mobile", "android and backend",
    "mobile and backend", "kotlin and spring",
]

MUMBAI_LOCATIONS = ["mumbai", "navi mumbai", "thane", "mmr"]
INDIA_LOCATIONS = [
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "delhi", "ncr",
    "noida", "gurgaon", "gurugram", "chennai", "kolkata", "ahmedabad",
    "remote india", "india remote",
]

INDIA_REMOTE_EXCLUDE = [
    "us only", "usa only", "eu only", "europe only", "uk only", "canada only",
]


def matches_track(title: str, description: str = "", query: str = "") -> bool:
    text = f"{title} {description} {query}".lower()
    all_kw = ANDROID_KEYWORDS + BACKEND_KEYWORDS + HYBRID_KEYWORDS
    if query:
        for w in query.lower().split():
            if len(w) > 2 and w in text:
                return True
    return any(k in text for k in all_kw)


def should_include_ats_job(
    title: str,
    description: str,
    loc_name: str,
    query: str,
    target_location: str,
) -> bool:
    """Broad ATS inclusion — filter relevance later in pipeline, not at fetch."""
    if matches_track(title, description, query):
        return True
    if matches_location(loc_name, description, target_location or "India"):
        return True
    return False


def matches_location(location: str, description: str, target: str) -> bool:
    combined = f"{location} {description}".lower().strip()
    target_l = target.lower()

    if not combined:
        return target_l in ("remote", "india", "")

    if any(m in combined for m in MUMBAI_LOCATIONS):
        return target_l in ("mumbai", "navi mumbai", "thane", "mumbai metropolitan region", "remote", "india", "")
    if any(i in combined for i in INDIA_LOCATIONS):
        return True
    if "remote" in combined:
        return _remote_india_ok(combined)
    return False


def _remote_india_ok(text: str) -> bool:
    if any(ex in text for ex in INDIA_REMOTE_EXCLUDE):
        return "india" in text or "worldwide" in text or "anywhere" in text
    return any(s in text for s in ["worldwide", "anywhere", "global", "india", "remote"])


def within_hours(dt: Optional[datetime], hours: int) -> bool:
    if dt is None:
        return False
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    posted = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return posted >= cutoff
