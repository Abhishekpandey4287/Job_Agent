"""Role track classification and dual-resume matching."""

from enum import Enum
from typing import List, Tuple

from job_agent.matching.scorer import MatchScorer
from job_agent.schemas import JobMatch, MasterResume, NormalizedJob


class RoleTrack(str, Enum):
    ANDROID = "ANDROID"
    BACKEND = "BACKEND"
    HYBRID = "HYBRID"
    IRRELEVANT = "IRRELEVANT"


ANDROID_TITLE_KEYWORDS = [
    "android", "kotlin", "mobile developer", "mobile engineer",
    "mobile software", "jetpack", "android sdk",
]

BACKEND_TITLE_KEYWORDS = [
    "backend", "java developer", "java engineer", "spring boot",
    "springboot", "api developer", "microservices", "server-side",
    "software engineer - java", "software engineer - backend",
]

HYBRID_SIGNALS = [
    "android and backend", "mobile and backend", "full stack mobile",
    "full-stack mobile", "kotlin and spring", "android + backend",
    "mobile + backend", "android with backend", "mobile with backend",
]


def classify_role_track(title: str, description: str) -> RoleTrack:
    text = f"{title} {description[:3000]}".lower()
    title_lower = title.lower()

    android_hits = sum(1 for k in ANDROID_TITLE_KEYWORDS if k in text)
    backend_hits = sum(1 for k in BACKEND_TITLE_KEYWORDS if k in text)
    hybrid = any(h in text for h in HYBRID_SIGNALS)

    if hybrid or (android_hits >= 2 and backend_hits >= 2):
        return RoleTrack.HYBRID
    if android_hits >= 1 and "ios only" not in text:
        if backend_hits >= 2:
            return RoleTrack.HYBRID
        return RoleTrack.ANDROID
    if backend_hits >= 1:
        return RoleTrack.BACKEND
    return RoleTrack.IRRELEVANT


def is_job_relevant(job: NormalizedJob) -> Tuple[bool, RoleTrack, str]:
    track = classify_role_track(job.title, job.description)
    if track == RoleTrack.IRRELEVANT:
        return False, track, "No Android/Backend/Hybrid relevance"
    if job.location_tier == 0:
        return False, track, "Location not eligible (Mumbai/India/Remote-India)"
    return True, track, f"Relevant {track.value} role"


def score_job(
    job: NormalizedJob,
    android_resume: MasterResume,
    backend_resume: MasterResume,
) -> Tuple[JobMatch, RoleTrack, str]:
    track = classify_role_track(job.title, job.description)

    if track == RoleTrack.HYBRID:
        android_scorer = MatchScorer(android_resume, role_track="HYBRID")
        backend_scorer = MatchScorer(backend_resume, role_track="HYBRID")
        android_match = android_scorer.score(job)
        backend_match = backend_scorer.score(job)
        if android_match.match_score >= backend_match.match_score:
            return android_match, RoleTrack.HYBRID, "Android"
        return backend_match, RoleTrack.HYBRID, "Backend"

    if track == RoleTrack.BACKEND:
        return MatchScorer(backend_resume, role_track="BACKEND").score(job), track, "Backend"

    return MatchScorer(android_resume, role_track="ANDROID").score(job), track, "Android"
