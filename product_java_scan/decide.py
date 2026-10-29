"""Turn portal and ATS reads into a hiring status without guessing when a page fails."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from product_java_scan.catalog import Company
from product_java_scan.locations import location_check
from product_java_scan.models import BoardFetch, CompanyResult, FoundJob, RawPosting
from product_java_scan.scoring import (
    RoleMatch,
    actively_hiring,
    classify_role,
    experience_acceptable,
    extract_experience,
    extract_skills,
    is_excluded_title,
    is_generic_engineering_title,
)
from product_java_scan.textutil import posted_within_week
from product_java_scan.urls import canonical_url

_KEEP_NOTE = "No vacancy decision was made because the careers portal did not load."


def failed_result(company: Company, reason: str, final_url: str = "") -> CompanyResult:
    return CompanyResult(
        company=company.name,
        target_profiles=company.target_profiles,
        portal_url=company.portal_url,
        final_url=final_url or company.portal_url,
        hiring_status="Scan failed",
        scanning_failure=(reason or "Request failed")[:300],
        needs_rescan=True,
        notes=_KEEP_NOTE,
        ats_checked="",
        jobs=[],
        portal_loaded=False,
        listing_complete=False,
        actively_hiring=False,
    )


def _identity(posting: RawPosting) -> str:
    url = canonical_url(posting.url)
    if url:
        return f"url:{url}"
    return f"title:{(posting.title or '').casefold()}|{(posting.location or '').casefold()}"


def _to_job(company: str, posting: RawPosting, score: int, role: str) -> FoundJob:
    checked = location_check(posting.location)
    return FoundJob(
        company=company,
        title=re_space(posting.title),
        location=(posting.location or "").strip() or "Not listed",
        experience=extract_experience(posting.title, posting.description),
        skills=extract_skills(posting.title, posting.description),
        url=(posting.url or "").strip(),
        posting_date=posting.posting_date or "",
        match_score=score,
        role_match=role,
        verified_via=posting.verified_via or "official_portal",
        location_check=checked,
    )


def re_space(value: str) -> str:
    return " ".join((value or "").split())


def select_jobs(
    company: str,
    postings: list[RawPosting],
    *,
    today: date,
) -> tuple[list[FoundJob], int, int, int]:
    ordered = sorted(postings, key=lambda posting: 0 if (posting.description or "").strip() else 1)
    found: dict[str, FoundJob] = {}
    seen_unmatched: set[str] = set()
    unchecked = 0
    known_ids: set[str] = set()
    unknown_ids: set[str] = set()
    for posting in ordered:
        title = posting.title or ""
        if is_excluded_title(title):
            continue
        if location_check(posting.location) == "outside_india":
            continue
        identity = _identity(posting)
        match = classify_role(title, posting.description)
        if match:
            job = _to_job(company, posting, match.score, match.role)
            acceptable = experience_acceptable(job.experience)
            recent = posted_within_week(job.posting_date, today)
            if acceptable is not True or recent is not True:
                if identity not in found:
                    if acceptable is None or recent is None:
                        unknown_ids.add(identity)
                    else:
                        known_ids.add(identity)
                continue
            unknown_ids.discard(identity)
            known_ids.discard(identity)
            current = found.get(identity)
            if current is None or job.match_score > current.match_score:
                found[identity] = job
            continue
        if identity in found or identity in seen_unmatched:
            continue
        seen_unmatched.add(identity)
        if is_generic_engineering_title(title) and not (posting.description or "").strip():
            unchecked += 1
    jobs = sorted(found.values(), key=lambda job: (-job.match_score, job.title.casefold()))
    return jobs, unchecked, len(known_ids), len(unknown_ids)


def build_result(
    company: Company,
    *,
    final_url: str,
    board_fetches: list[BoardFetch],
    html_postings: list[RawPosting],
    js_shell: bool,
    explicit_empty: bool,
    today: date | None = None,
) -> CompanyResult:
    notes: list[str] = []
    errors = [f"{item.label}: {item.error}" for item in board_fetches if item.error]
    successful = [item for item in board_fetches if not item.error]
    postings: list[RawPosting] = []
    for item in successful:
        postings.extend(item.postings)
    postings.extend(html_postings)
    scanned_on = today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    jobs, unchecked, dropped_known, dropped_unknown = select_jobs(
        company.name,
        postings,
        today=scanned_on,
    )

    if errors:
        notes.append("Linked ATS could not be fully read: " + "; ".join(errors))
    if unchecked:
        notes.append(
            f"{unchecked} generic engineering title(s) had no description, "
            "so Java and Spring Boot requirements were not fully verified."
        )
    if js_shell and not postings:
        notes.append("Page loaded but job listings are not in the HTML response.")
    elif not postings and not successful and not explicit_empty:
        notes.append("Job list was not verified on the official portal.")
    if html_postings and not any(item.complete for item in successful):
        notes.append("Openings were read from the portal page. A full requisition list was not confirmed.")

    if dropped_unknown:
        notes.append(
            "A matching role was missing a posting date or an experience requirement, so it was not added."
        )
    if dropped_known:
        notes.append(
            "Roles posted more than 7 days ago, or asking for 4 or more years at the minimum, were left out."
        )

    any_complete = any(item.complete for item in successful)
    any_partial = any(not item.complete for item in successful)
    listing_complete = (
        not errors and unchecked == 0 and dropped_unknown == 0 and any_complete and not any_partial
    )
    if (
        not listing_complete
        and not errors
        and not board_fetches
        and not html_postings
        and explicit_empty
        and not js_shell
    ):
        listing_complete = True

    active = actively_hiring([RoleMatch(score=job.match_score, role=job.role_match) for job in jobs])
    if jobs:
        hiring_status = "Actively hiring Java/Spring" if active else "Priority roles found"
        # A complete ATS list that already contains a matching role is usable.
        # Unread generic titles stay in the notes and do not wipe older rows,
        # because listing_complete remains false until every title is checked.
        needs_rescan = not (any_complete and not any_partial and not errors)
    elif listing_complete:
        hiring_status = "No priority roles on verified listing"
        needs_rescan = False
        if dropped_known:
            notes.append(
                "Verified listing had no Java or Spring Boot role from the last 7 days "
                "whose required experience starts under 4 years."
            )
        else:
            notes.append("Verified listing did not include a matching Java or Spring Boot role.")
    else:
        hiring_status = "Needs another scan"
        needs_rescan = True

    if needs_rescan and not notes:
        notes.append("Another scan is required before treating this portal as having no matching roles.")

    return CompanyResult(
        company=company.name,
        target_profiles=company.target_profiles,
        portal_url=company.portal_url,
        final_url=final_url or company.portal_url,
        hiring_status=hiring_status,
        scanning_failure="",
        needs_rescan=needs_rescan,
        notes=" ".join(notes)[:500],
        ats_checked=", ".join(item.label for item in board_fetches),
        jobs=jobs,
        portal_loaded=True,
        listing_complete=listing_complete,
        actively_hiring=active,
    )
