"""Internet-wide discovery orchestrator — profile-driven, multi-source, fault-isolated."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Sequence, Tuple

from job_agent.config import get_settings
from job_agent.discovery.career_portal import resolve_career_portal_ids
from job_agent.discovery.catalog import SearchContext, list_websites, parallel_collectors
from job_agent.discovery.company_careers import discover_company_careers
from job_agent.discovery.source_stats import DiscoveryStatsCollector
from job_agent.logging_config import get_logger
from job_agent.matching.multi_profile import score_job_multi
from job_agent.normalization.dedupe import deduplicate_jobs_aggressive
from job_agent.normalization.filters import normalize_job
from job_agent.profiles import ResumeProfile, load_profiles
from job_agent.profiles.query_generator import generate_all_queries
from job_agent.schemas import JobMatch, NormalizedJob, RawJob

logger = get_logger(__name__)


@dataclass
class DiscoveryResult:
    top_rows: List[Tuple[JobMatch, str, str]]  # match, role_track, resume_used
    raw_count: int
    normalized_count: int
    deduped_count: int
    matched_count: int
    profile_match_counts: Dict[str, int]
    stats: DiscoveryStatsCollector
    errors: List[str] = field(default_factory=list)
    queries_used: List[str] = field(default_factory=list)
    locations_used: List[str] = field(default_factory=list)
    profiles: List[ResumeProfile] = field(default_factory=list)
    brave_used: bool = False
    public_browser_used: bool = False
    sources_attempted: int = 0
    sources_successful: int = 0


async def _safe_collect(name: str, coro, stats: DiscoveryStatsCollector, errors: List[str]) -> List[RawJob]:
    try:
        result = await coro
        jobs = result[0] if isinstance(result, tuple) else result
        if not isinstance(jobs, list):
            jobs = []
        if jobs:
            stats.record_success(name, len(jobs))
        else:
            stats.record_success(name, 0)
        return jobs
    except Exception as e:
        errors.append(f"{name}: {e}")
        stats.record_blocked(name, str(e)[:120])
        logger.error("source_isolated_failure", source=name, error=str(e)[:160])
        return []


def _job_locations() -> List[str]:
    settings = get_settings()
    raw = (getattr(settings, "job_locations", "") or "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return [
        "Mumbai", "Thane", "Navi Mumbai", "Pune", "Bangalore", "Hyderabad",
        "Chennai", "Delhi NCR", "India", "Remote", "Remote India",
    ]


def _is_expired(job: NormalizedJob, max_age_days: int) -> bool:
    if not job.date_posted or max_age_days <= 0:
        return False
    posted = job.date_posted.replace(tzinfo=None) if job.date_posted.tzinfo else job.date_posted
    return posted < datetime.utcnow() - timedelta(days=max_age_days)


def _looks_closed(job: NormalizedJob) -> bool:
    text = f"{job.title} {job.description[:500]}".lower()
    markers = (
        "no longer accepting", "position filled", "job closed", "no longer available",
        "this job has expired", "posting expired",
    )
    return any(m in text for m in markers)


def _profile_prio(
    resume_used: str,
    profile_priority: Dict[str, int] | None,
) -> int:
    prio_map = profile_priority or {}
    key = (resume_used or "").strip().lower()
    return prio_map.get(key, prio_map.get(key.replace(" ", "-"), 999))


def _rank_key(
    item: Tuple[JobMatch, str, str],
    recency_hours: int,
    profile_priority: Dict[str, int] | None = None,
    *,
    priority_first: bool = False,
) -> tuple:
    m, _, resume_used = item
    posted = m.job.date_posted
    rec = 0
    if posted:
        p = posted.replace(tzinfo=None) if posted.tzinfo else posted
        if p >= datetime.utcnow() - timedelta(hours=recency_hours):
            rec = 2
        elif p >= datetime.utcnow() - timedelta(days=7):
            rec = 1
    prio = _profile_prio(resume_used, profile_priority)
    if priority_first:
        # 1-Android before 2-Backend; score is secondary within the same priority
        return (prio, -m.match_score, -rec, -m.job.location_tier)
    return (-m.match_score, prio, -rec, -m.job.location_tier)


def _diversify(
    rows: List[Tuple[JobMatch, str, str]],
    limit: int,
) -> List[Tuple[JobMatch, str, str]]:
    """Prefer source/company diversity when scores are close — soft, not hard caps."""
    if limit <= 0:
        return []
    if len(rows) <= limit:
        return list(rows)
    selected: List[Tuple[JobMatch, str, str]] = []
    company_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    deferred: List[Tuple[JobMatch, str, str]] = []

    for row in rows:
        m, _, _ = row
        company = (m.job.company or "").lower()
        source = (m.job.source or "").split(":")[0].lower()
        # Soft preference: defer if company already has 4+ or source 12+ and score not exceptional
        if company_counts.get(company, 0) >= 4 and m.match_score < 90:
            deferred.append(row)
            continue
        if source_counts.get(source, 0) >= 14 and m.match_score < 88:
            deferred.append(row)
            continue
        selected.append(row)
        company_counts[company] = company_counts.get(company, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) >= limit:
            break

    for row in deferred:
        if len(selected) >= limit:
            break
        selected.append(row)
    return selected[:limit]


def _select_top_jobs(
    rows: List[Tuple[JobMatch, str, str]],
    limit: int,
    *,
    strong_min_score: float = 70.0,
    recency_hours: int = 24,
    profile_priority: Dict[str, int] | None = None,
) -> List[Tuple[JobMatch, str, str]]:
    """Export jobs that clear the strong-score bar.

    Only jobs with score >= strong_min_score are included. Weaker jobs are never
    filled in. ``limit`` <= 0 means no cap (export every qualifying job).
    """
    if not rows:
        return []

    strong = [r for r in rows if r[0].match_score >= strong_min_score]
    strong.sort(
        key=lambda x: _rank_key(
            x, recency_hours, profile_priority, priority_first=True
        )
    )
    if not strong:
        return []
    if limit <= 0:
        # Soft company/source preference only — keep every qualifying job.
        return _diversify(strong, len(strong))
    return _diversify(strong, limit)


async def run_discovery(
    *,
    cdp_url: str = "",
) -> DiscoveryResult:
    """Run full multi-source discovery for all active resume profiles."""
    settings = get_settings()
    stats = DiscoveryStatsCollector()
    errors: List[str] = []
    raw_jobs: List[RawJob] = []

    profiles = load_profiles()
    if not profiles:
        print(
            "  Profile-based search is off. "
            "Set PROFILE_BASED_SEARCH=true and PROFILE_<ID>=true in .env."
        )
        return DiscoveryResult(
            top_rows=[],
            raw_count=0,
            normalized_count=0,
            deduped_count=0,
            matched_count=0,
            profile_match_counts={},
            stats=stats,
            errors=errors,
            queries_used=[],
            locations_used=[],
            profiles=[],
        )
    max_variants = int(getattr(settings, "search_query_variants_per_profile", 10) or 10)
    queries = generate_all_queries(profiles, max_variants_per_profile=max_variants)
    locations = _job_locations()
    max_per_source = int(getattr(settings, "max_results_per_source", 200) or 200)

    print(f"  Profiles loaded: {[p.id for p in profiles]}")
    print(f"  Query variants: {len(queries)} across {len(profiles)} profiles")
    print(f"  Locations: {len(locations)}")
    websites = list_websites()
    print(f"  Websites registered: {len(websites)}")
    print("\n  --- Searching websites in parallel ---")

    ctx = SearchContext(
        queries=queries,
        locations=locations,
        profiles=profiles,
        max_per_source=max_per_source,
        settings=settings,
        stats=stats,
        errors=errors,
        cdp_url=cdp_url,
    )
    started = time.perf_counter()
    batches = await asyncio.gather(*parallel_collectors(ctx), return_exceptions=True)
    for batch in batches:
        if isinstance(batch, Exception):
            errors.append(f"parallel_source: {batch}")
            logger.error("parallel_source_failed", error=str(batch)[:160])
            continue
        raw_jobs.extend(batch)
    brave_used = ctx.brave_used
    public_browser_used = ctx.public_browser_used
    print(f"  Parallel website search finished in {time.perf_counter() - started:.0f}s")


    # --- Company career feedback loop ---
    if getattr(settings, "enable_company_career_discovery", True):
        print("\n  --- Company career discovery (bounded) ---")
        try:
            career_jobs, career_errs, career_counts = await discover_company_careers(
                raw_jobs,
                max_companies=int(getattr(settings, "max_company_career_discovery", 50) or 50),
                delay_s=float(getattr(settings, "request_delay_seconds", 1) or 1),
            )
            raw_jobs.extend(career_jobs)
            errors.extend(career_errs)
            for k, v in career_counts.items():
                stats.record_success(k, v)
            print(f"  [company_careers] {len(career_jobs)} jobs")
        except Exception as e:
            errors.append(f"company_careers: {e}")
            stats.record_blocked("company_careers", str(e)[:100])

    raw_count = len(raw_jobs)
    print(f"\n  Total raw jobs: {raw_count}")

    # --- Normalize / dedupe / filter ---
    normalized = [normalize_job(r) for r in raw_jobs]
    normalized_count = len(normalized)
    deduped = deduplicate_jobs_aggressive(normalized)
    deduped_count = len(deduped)

    max_age = int(getattr(settings, "max_job_age_days", 30) or 30)
    valid = [j for j in deduped if not _is_expired(j, max_age) and not _looks_closed(j)]

    # --- Score against all profiles ---
    matches_meta: List[Tuple[JobMatch, str, str]] = []
    profile_counts: Dict[str, int] = {p.id: 0 for p in profiles}
    for job in valid:
        result = score_job_multi(job, profiles, min_score=float(getattr(settings, "min_match_score", 0) or 0))
        if not result:
            continue
        match, track, resume_used = result
        # Attach matched profile id for Resume/Referral agents
        pid = (
            "android" if resume_used == "Android"
            else "backend" if resume_used == "Backend"
            else resume_used.lower()
        )
        match.job.matched_profiles = [pid]
        matches_meta.append((match, track, resume_used))
        if pid in profile_counts:
            profile_counts[pid] += 1
        elif resume_used.lower() in profile_counts:
            profile_counts[resume_used.lower()] += 1

    profile_priority = {p.id: p.priority for p in profiles}
    profile_priority.update({p.label.lower(): p.priority for p in profiles})
    profile_priority.setdefault("android", 1)
    profile_priority.setdefault("backend", 2)
    top_n = int(getattr(settings, "top_jobs_count", 0) or 0)
    strong_min = float(getattr(settings, "top_jobs_priority_score", 70) or 70)
    top = _select_top_jobs(
        matches_meta,
        top_n,
        strong_min_score=strong_min,
        recency_hours=settings.job_recency_hours,
        profile_priority=profile_priority,
    )
    cap_label = "unlimited" if top_n <= 0 else str(top_n)
    print(
        f"  Top export: {len(top)} "
        f"(score>={strong_min:g}, cap={cap_label})"
    )

    # --- Career portal (ATS) job id resolution ---
    # Runs only on the already-selected export set. Purely additive.
    if getattr(settings, "enable_career_portal_resolution", True) and top:
        print("\n  --- Career portal job ID resolution ---")
        try:
            resolved = await resolve_career_portal_ids(
                [m.job for m, _, _ in top],
                timeout_seconds=float(getattr(settings, "career_portal_timeout_seconds", 12) or 12),
                delay_seconds=float(getattr(settings, "career_portal_delay_seconds", 0.5) or 0),
                concurrency=int(getattr(settings, "career_portal_concurrency", 4) or 4),
            )
            print(f"  [career_portal] resolved {resolved}/{len(top)} job IDs")
        except Exception as e:
            errors.append(f"career_portal: {e}")
            print(f"  [career_portal] skipped: {e}")

    # Source health
    sources_attempted = len(stats._sources)
    sources_successful = sum(
        1 for s in stats._sources.values() if s.status == "SUCCESS" and s.raw_discovered > 0
    )

    return DiscoveryResult(
        top_rows=top,
        raw_count=raw_count,
        normalized_count=normalized_count,
        deduped_count=deduped_count,
        matched_count=len(matches_meta),
        profile_match_counts=profile_counts,
        stats=stats,
        errors=errors,
        queries_used=queries,
        locations_used=locations,
        profiles=profiles,
        brave_used=brave_used,
        public_browser_used=public_browser_used,
        sources_attempted=sources_attempted,
        sources_successful=sources_successful,
    )
