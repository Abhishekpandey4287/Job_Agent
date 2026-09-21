"""Job posting freshness classification and filtering.

Freshness is established only from real timestamps. An undated job is
``unknown`` — never treated as fresh just to fill a report.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable, List, Sequence, Tuple

from job_agent.schemas import DateVerification, NormalizedJob


class FreshnessBucket(str, Enum):
    H0_24 = "0-24h"
    H24_48 = "24-48h"
    OLD = ">48h"
    UNKNOWN = "unknown"


def _as_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def classify_freshness(
    date_posted: datetime | None,
    *,
    now: datetime | None = None,
) -> FreshnessBucket:
    """Map a posting timestamp into a freshness bucket."""
    if date_posted is None:
        return FreshnessBucket.UNKNOWN
    now = now or datetime.utcnow()
    posted = _as_naive_utc(date_posted)
    age = now - posted
    if age < timedelta(0):
        # Clock skew / future dates — treat as brand new.
        return FreshnessBucket.H0_24
    if age <= timedelta(hours=24):
        return FreshnessBucket.H0_24
    if age <= timedelta(hours=48):
        return FreshnessBucket.H24_48
    return FreshnessBucket.OLD


def is_fresh_enough(
    job: NormalizedJob,
    *,
    max_age_hours: float = 48.0,
    allow_unknown: bool = False,
    now: datetime | None = None,
) -> bool:
    """True when the job has a known date within ``max_age_hours``.

    Unknown dates are rejected by default — inventing freshness is worse than
    exporting fewer than 40 rows.
    """
    bucket = classify_freshness(job.date_posted, now=now)
    if bucket == FreshnessBucket.UNKNOWN:
        return allow_unknown
    if bucket == FreshnessBucket.OLD:
        return False
    # Double-check against the configured window (in case it is tightened).
    now = now or datetime.utcnow()
    posted = _as_naive_utc(job.date_posted)  # type: ignore[arg-type]
    return (now - posted) <= timedelta(hours=max_age_hours)


def freshness_counts(
    jobs: Iterable[NormalizedJob],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    counts = {b.value: 0 for b in FreshnessBucket}
    for job in jobs:
        counts[classify_freshness(job.date_posted, now=now).value] += 1
    return counts


def filter_fresh_jobs(
    jobs: Sequence[NormalizedJob],
    *,
    max_age_hours: float = 48.0,
    allow_unknown: bool = False,
    now: datetime | None = None,
) -> List[NormalizedJob]:
    return [
        j for j in jobs
        if is_fresh_enough(
            j, max_age_hours=max_age_hours, allow_unknown=allow_unknown, now=now
        )
    ]


def freshness_rank_key(job: NormalizedJob, *, now: datetime | None = None) -> Tuple[int, float]:
    """Sort key: freshest first. Lower tuple = better."""
    bucket = classify_freshness(job.date_posted, now=now)
    order = {
        FreshnessBucket.H0_24: 0,
        FreshnessBucket.H24_48: 1,
        FreshnessBucket.UNKNOWN: 2,
        FreshnessBucket.OLD: 3,
    }[bucket]
    if job.date_posted is None:
        age_hours = 1e9
    else:
        now = now or datetime.utcnow()
        age_hours = max(0.0, (now - _as_naive_utc(job.date_posted)).total_seconds() / 3600.0)
    return (order, age_hours)


def verification_prefer(job: NormalizedJob) -> int:
    """Lower = better: verified dates beat estimated, unknown last."""
    v = job.date_posted_verified
    if v == DateVerification.VERIFIED:
        return 0
    if v == DateVerification.ESTIMATED:
        return 1
    return 2
