"""Score jobs against all active resume profiles; pick the best match."""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from job_agent.matching.dual_track import RoleTrack, classify_role_track
from job_agent.matching.scorer import MatchScorer
from job_agent.profiles import ResumeProfile
from job_agent.schemas import JobMatch, NormalizedJob

# Obvious non-engineering titles — never export as Android/Backend matches
NON_ENG_TITLE = re.compile(
    r"\b("
    r"marketing|sales|account\s*manager|finance|accountant|hr\b|human resources|"
    r"recruiter|talent\s*acquisition|customer\s*success|content\s*writer|"
    r"copywriter|graphic\s*design|product\s*marketing|product\s*designer|"
    r"business\s*development|bdm\b|operations\s*manager|legal\b|counsel|"
    r"communications\s*officer|student\s*assistant|internship\s*coordinator|"
    r"channel\s*&\s*partnership|board\s*director|data\s*analyst|product\s*manager"
    r")\b",
    re.I,
)


def _profile_title_hit(job: NormalizedJob, profile: ResumeProfile) -> bool:
    """True if title/description mentions this profile's role signals."""
    text = f"{job.title} {job.description[:2000]}".lower()
    title = (job.title or "").lower()

    # Strong signals preferred in the title
    family = {
        "android": (
            "android", "kotlin", "jetpack", "compose",
            "mobile engineer", "mobile developer", "mobile software",
        ),
        "backend": (
            "backend", "back-end", "back end", "java engineer", "java developer",
            "spring boot", "springboot", "microservices", "api engineer", "server-side",
            "full stack", "full-stack", "fullstack", "software engineer", "software developer",
        ),
        "java": ("java developer", "java engineer", "spring boot", "jvm"),
        "devops": ("devops", "sre", "kubernetes", "ci/cd", "infrastructure engineer"),
        "flutter": ("flutter", "dart"),
        "ios": ("ios", "swift", "objective-c"),
        "fullstack": ("full stack", "full-stack", "fullstack"),
        "cloud": ("aws", "gcp", "azure", "cloud engineer"),
    }
    phrases = list(family.get(profile.id, ()))
    phrases.append(profile.id.lower())
    if profile.label:
        phrases.append(profile.label.lower())
    for hint in profile.title_hints:
        if hint and len(hint) > 2:
            phrases.append(hint.lower())
    # Aliases: skip ultra-broad single words like "Mobile"
    for a in profile.aliases:
        al = (a or "").strip().lower()
        if not al or al in ("mobile", "software", "engineer", "developer"):
            continue
        phrases.append(al)

    # Title match is enough
    if any(p in title for p in phrases if len(p) >= 3):
        return True
    # Mobile profiles: require a clear title signal (descriptions are too noisy on remote boards)
    if profile.id in ("android", "flutter", "ios", "mobile"):
        return False
    # Description: require a stronger engineering phrase (not bare "mobile")
    strong = [p for p in phrases if " " in p or p in (
        "android", "kotlin", "java", "flutter", "devops", "backend", "ios", "swift", "compose",
    )]
    return any(p in text for p in strong)


def score_job_multi(
    job: NormalizedJob,
    profiles: Sequence[ResumeProfile],
    *,
    min_score: float = 0.0,
) -> Tuple[JobMatch, str, str] | None:
    """Return (best_match, role_track_label, profile_id) or None if irrelevant.

    role_track_label stays ANDROID/BACKEND/HYBRID when classifiable, else PROFILE_ID upper.
    profile_id is the folder id used by Resume Agent.
    """
    if not profiles:
        return None
    if job.location_tier == 0:
        return None
    if NON_ENG_TITLE.search(job.title or ""):
        return None

    track = classify_role_track(job.title, job.description)
    best: JobMatch | None = None
    best_profile: ResumeProfile | None = None

    for profile in profiles:
        if track == RoleTrack.IRRELEVANT and not _profile_title_hit(job, profile):
            continue
        # Classic track gating: don't score backend-only jobs against android-only profile
        if track == RoleTrack.ANDROID and profile.id in ("backend", "java", "devops", "cloud"):
            if not _profile_title_hit(job, profile):
                continue
        if track == RoleTrack.BACKEND and profile.id in ("android", "flutter", "ios"):
            if not _profile_title_hit(job, profile):
                continue

        role_hint = profile.id.upper()
        if track == RoleTrack.HYBRID:
            role_hint = "HYBRID"
        elif track == RoleTrack.ANDROID and profile.id == "android":
            role_hint = "ANDROID"
        elif track == RoleTrack.BACKEND and profile.id in ("backend", "java"):
            role_hint = "BACKEND"

        scorer = MatchScorer(profile.resume, role_track=role_hint)
        if profile.id in ("android", "flutter", "ios", "mobile"):
            job.is_android_relevant = True
        match = scorer.score(job)
        if best is None or match.match_score > best.match_score:
            best = match
            best_profile = profile
        elif (
            best is not None
            and match.match_score == best.match_score
            and best_profile is not None
            and profile.priority < best_profile.priority
        ):
            best = match
            best_profile = profile

    if best is None or best_profile is None:
        return None
    if best.match_score < min_score:
        return None
    if not _profile_title_hit(job, best_profile):
        return None

    if track != RoleTrack.IRRELEVANT:
        track_label = track.value
    else:
        track_label = best_profile.role_track_label

    # Prefer human label (Android / Backend / Flutter) for Excel + resume folders
    resume_used = best_profile.label or best_profile.id.title()

    return best, track_label, resume_used


def is_job_relevant_multi(
    job: NormalizedJob,
    profiles: Sequence[ResumeProfile],
) -> Tuple[bool, str, str]:
    """Quick relevance: location OK and at least one profile scores > soft floor."""
    result = score_job_multi(job, profiles, min_score=35.0)
    if not result:
        return False, RoleTrack.IRRELEVANT.value, ""
    _, track, resume_used = result
    return True, track, resume_used
