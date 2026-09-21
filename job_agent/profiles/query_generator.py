"""Generate focused search queries from a resume profile."""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Set

from job_agent.profiles import ResumeProfile
from job_agent.schemas import MasterResume

# Synonym expansions for common roles (kept small and focused)
ROLE_SYNONYMS: dict[str, List[str]] = {
    "android": [
        "Android Developer",
        "Android Engineer",
        "Android Software Engineer",
        "Mobile Android Developer",
        "Kotlin Android Developer",
        "Android SDK Developer",
        "Mobile Application Developer",
        "Mobile Software Engineer",
        "Jetpack Compose Developer",
        "Kotlin Developer",
    ],
    "backend": [
        "Backend Developer",
        "Backend Engineer",
        "Java Developer",
        "Java Backend Developer",
        "Spring Boot Developer",
        "Java Software Engineer",
        "API Developer",
        "Microservices Engineer",
        "Server-Side Engineer",
        "Software Engineer Java",
    ],
    "devops": [
        "DevOps Engineer",
        "SRE",
        "Platform Engineer",
        "Cloud Engineer",
        "CI/CD Engineer",
        "Infrastructure Engineer",
    ],
    "flutter": [
        "Flutter Developer",
        "Flutter Engineer",
        "Dart Developer",
        "Cross Platform Mobile Developer",
    ],
    "ios": [
        "iOS Developer",
        "iOS Engineer",
        "Swift Developer",
        "Mobile iOS Engineer",
    ],
    "fullstack": [
        "Full Stack Developer",
        "Full Stack Engineer",
        "Full-Stack Software Engineer",
    ],
    "java": [
        "Java Developer",
        "Java Engineer",
        "Java Software Engineer",
        "Spring Boot Developer",
        "Java Backend Engineer",
    ],
    "cloud": [
        "Cloud Engineer",
        "AWS Engineer",
        "Cloud Infrastructure Engineer",
    ],
}


def _skill_tokens(resume: MasterResume) -> List[str]:
    skills: List[str] = []
    for items in (resume.technical_skills or {}).values():
        skills.extend(items)
    for exp in resume.experience or []:
        skills.extend(exp.technologies or [])
    for proj in resume.projects or []:
        skills.extend(proj.technologies or [])
    # Dedupe preserving order
    seen: Set[str] = set()
    out: List[str] = []
    for s in skills:
        key = s.strip().lower()
        if not key or key in seen or len(s) > 40:
            continue
        seen.add(key)
        out.append(s.strip())
    return out


def _title_seeds(profile: ResumeProfile) -> List[str]:
    seeds: List[str] = list(profile.title_hints)
    for exp in profile.resume.experience or []:
        if exp.title:
            seeds.append(exp.title)
    # Profile-id synonyms
    seeds.extend(ROLE_SYNONYMS.get(profile.id, []))
    # Generic from label
    if profile.label and profile.label.lower() not in {s.lower() for s in seeds}:
        seeds.append(f"{profile.label} Developer")
        seeds.append(f"{profile.label} Engineer")
    return seeds


def _tech_queries(profile: ResumeProfile, skills: Sequence[str]) -> List[str]:
    """Build a few technology-focused queries (not one giant skill dump)."""
    role = profile.label or profile.id.title()
    priority_hints = [
        "kotlin", "jetpack compose", "compose", "android sdk", "java",
        "spring boot", "spring", "microservices", "aws", "docker",
        "flutter", "dart", "swift", "react native", "kubernetes",
        "redis", "postgresql", "mysql", "firebase",
    ]
    picked: List[str] = []
    lower_skills = [(s, s.lower()) for s in skills]
    for hint in priority_hints:
        for original, low in lower_skills:
            if hint in low and original not in picked:
                picked.append(original)
                break
        if len(picked) >= 4:
            break
    # Fill from front of skill list
    for s, _ in lower_skills:
        if s not in picked:
            picked.append(s)
        if len(picked) >= 6:
            break

    queries: List[str] = []
    for tech in picked[:5]:
        queries.append(f"{tech} {role}")
        if profile.id in ("android", "mobile"):
            queries.append(f"{tech} Android")
        if profile.id in ("backend", "java"):
            queries.append(f"{tech} Backend")
    return queries


def generate_queries_for_profile(
    profile: ResumeProfile,
    *,
    max_variants: int = 10,
) -> List[str]:
    """Return focused search query variants for one profile."""
    skills = _skill_tokens(profile.resume)
    candidates: List[str] = []

    for seed in _title_seeds(profile):
        cleaned = re.sub(r"\s+", " ", seed).strip()
        # Drop overly long experience titles
        if len(cleaned) > 60:
            cleaned = cleaned[:57].rstrip() + "..."
        if cleaned:
            candidates.append(cleaned)

    candidates.extend(_tech_queries(profile, skills))

    # Seniority variants (sparse — not every query)
    years = getattr(profile.resume, "total_experience_years", 0) or 0
    base_role = candidates[0] if candidates else f"{profile.label} Developer"
    if years and years < 3:
        candidates.append(f"Junior {base_role}")
        candidates.append(f"{base_role} 1-3 years")
    elif years >= 3:
        candidates.append(f"Senior {base_role}")

    # India / remote hiring phrases for web search later
    candidates.append(f"{base_role} India")
    candidates.append(f"{base_role} Mumbai")

    seen: Set[str] = set()
    out: List[str] = []
    for q in candidates:
        key = q.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(q.strip())
        if len(out) >= max_variants:
            break
    return out


def generate_all_queries(
    profiles: Sequence[ResumeProfile],
    *,
    max_variants_per_profile: int = 10,
) -> List[str]:
    """Union of queries across all active profiles (deduped)."""
    seen: Set[str] = set()
    out: List[str] = []
    for profile in profiles:
        for q in generate_queries_for_profile(profile, max_variants=max_variants_per_profile):
            key = q.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(q)
    return out


def generate_web_search_queries(
    profiles: Sequence[ResumeProfile],
    *,
    max_per_profile: int = 8,
) -> List[str]:
    """Search-engine style queries including ATS and Naukri site: filters."""
    queries: List[str] = []
    ats_sites = [
        "site:boards.greenhouse.io",
        "site:jobs.lever.co",
        "site:jobs.ashbyhq.com",
        "site:jobs.smartrecruiters.com",
        "site:myworkdayjobs.com",
        "site:naukri.com",
    ]
    for profile in profiles:
        base = generate_queries_for_profile(profile, max_variants=max_per_profile)
        for q in base[:5]:
            queries.append(f'"{q}" jobs India')
            queries.append(f'"{q}" careers Mumbai')
            queries.append(f'"we are hiring" "{q}"')
        role = base[0] if base else profile.label
        for site in ats_sites[:4]:
            queries.append(f'{site} "{role}"')
    # Dedup
    seen: Set[str] = set()
    out: List[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out
