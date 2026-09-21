"""Extract job identifiers from listing URLs.

Two different things live here, and conflating them is a mistake:

* ``extract_company_reference_id`` — the employer's own requisition/reference id,
  taken only from the company's own ATS. Safe to quote to a recruiter.
* ``extract_shareable_job_id`` — a job board's internal listing number. Useful for
  linking back to the portal, but it is *not* a reference id.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# Sources that are the employer's own ATS rather than an aggregator. Adapters name
# these "greenhouse:<board>", "lever:<slug>", "lever_careers", etc.
_COMPANY_ATS_SOURCES = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workday",
    "company_careers",
)

# Host + id patterns for company-hosted ATS postings.
_ATS_URL_PATTERNS = (
    r"(?:job-)?boards(?:-api)?\.greenhouse\.io/[^/]+/jobs/(\d+)",
    r"jobs\.lever\.co/[^/]+/([0-9a-fA-F-]{8,})",
    r"api\.lever\.co/v0/postings/[^/]+/([0-9a-fA-F-]{8,})",
    r"jobs\.ashbyhq\.com/[^/]+/([0-9a-fA-F-]{8,})",
    r"jobs\.smartrecruiters\.com/(?:[^/]+/)*?(\d{6,})",
    r"careers\.smartrecruiters\.com/(?:[^/]+/)*?(\d{6,})",
    r"myworkdayjobs\.com/[^?]*?/([A-Za-z]*-?[Rr]?\d{4,}[A-Za-z0-9._-]*)",
    r"workable\.com/[^/]+/j/([0-9A-F]{8,})",
    r"recruitee\.com/o/([A-Za-z0-9-]{6,})",
    r"jobvite\.com/[^/]+/job/([A-Za-z0-9]{6,})",
    r"bamboohr\.com/(?:careers|jobs)/(\d{2,})",
)

# Explicit requisition parameters used by enterprise careers portals.
_REQ_QUERY_KEYS = (
    "requisitionid",
    "jobreqid",
    "reqid",
    "req_id",
    "requisition",
    "jobrequisitionid",
)


def _is_company_ats_source(source: str) -> bool:
    src = (source or "").lower()
    return any(src.startswith(prefix) or f"{prefix}:" in src for prefix in _COMPANY_ATS_SOURCES)


def extract_company_reference_id(
    *,
    source: str = "",
    source_job_id: str = "",
    job_url: str = "",
    application_url: str = "",
) -> str:
    """Return the employer's requisition/reference id, or "" when it is unknown.

    Intentionally conservative. A Shine or Indeed listing number identifies the
    board's copy of the ad, not the employer's requisition, so returning one would
    invite quoting the wrong number to a recruiter. Blank means "look it up on the
    company's careers site" rather than "there is no id".
    """
    urls = [(job_url or "").strip(), (application_url or "").strip()]

    for url in urls:
        if not url:
            continue
        for pattern in _ATS_URL_PATTERNS:
            match = re.search(pattern, url, re.I)
            if match:
                return match.group(1)

    for url in urls:
        if not url or "://" not in url:
            continue
        query = parse_qs(urlparse(url).query)
        for key, values in query.items():
            if key.lower().replace("-", "").replace("_", "") in (
                k.replace("_", "") for k in _REQ_QUERY_KEYS
            ):
                candidate = (values[0] if values else "").strip()
                if candidate and re.fullmatch(r"[A-Za-z0-9._\-]{3,60}", candidate):
                    return candidate

    # The adapter pulled straight from a company ATS, so its id is the real one.
    if _is_company_ats_source(source):
        candidate = (source_job_id or "").strip()
        if candidate and "://" not in candidate and re.fullmatch(r"[A-Za-z0-9._\-]{3,80}", candidate):
            return candidate

    return ""


def extract_shareable_job_id(
    *,
    source: str = "",
    source_job_id: str = "",
    job_url: str = "",
    application_url: str = "",
) -> str:
    """Return the portal's public job id when possible (not the internal hash).

    Prefer LinkedIn numeric id, Indeed jk=, Greenhouse/Lever ids, etc.
    Falls back to a cleaned source_job_id or last path segment of the URL.
    """
    src = (source or "").lower()
    candidates = [
        (source_job_id or "").strip(),
        (job_url or "").strip(),
        (application_url or "").strip(),
    ]

    for value in candidates:
        if not value:
            continue
        extracted = _from_value(value, src)
        if extracted:
            return extracted

    return ""


def _from_value(value: str, source: str) -> str:
    # Already a clean id (no URL)
    if "://" not in value and "/" not in value and len(value) >= 4:
        if re.fullmatch(r"[A-Za-z0-9_\-]{4,80}", value):
            return value

    # LinkedIn: .../jobs/view/1234567890
    m = re.search(r"linkedin\.com/jobs/view/(\d+)", value, re.I)
    if m:
        return m.group(1)
    m = re.search(r"/jobs/view/(\d+)", value)
    if m and "linkedin" in source:
        return m.group(1)

    # Indeed: jk=...
    if "jk=" in value:
        return value.split("jk=")[-1].split("&")[0].strip()

    # Greenhouse: /jobs/1234567
    m = re.search(r"greenhouse\.io/[^/]+/jobs/(\d+)", value, re.I)
    if m:
        return m.group(1)

    # Lever: jobs.lever.co/company/uuid
    m = re.search(r"jobs\.lever\.co/[^/]+/([a-f0-9-]{8,})", value, re.I)
    if m:
        return m.group(1)

    # Ashby
    m = re.search(r"ashbyhq\.com/[^/]+/([a-f0-9-]{8,})", value, re.I)
    if m:
        return m.group(1)

    # SmartRecruiters
    m = re.search(r"smartrecruiters\.com/.+/(\d+)", value, re.I)
    if m:
        return m.group(1)

    # Naukri (legacy): trailing numeric id in slug
    m = re.search(r"naukri\.com/job-listings-[^-].*-(\d{6,})", value, re.I)
    if m:
        return m.group(1)
    m = re.search(r"-(\d{8,})(?:\?|$)", value)
    if m and "naukri" in value.lower():
        return m.group(1)

    # Arbeitnow / remotive style slug ids
    m = re.search(r"-(\d{4,})$", value.rstrip("/"))
    if m and "://" in value:
        return m.group(1)

    # Generic: last path segment if it looks like an id
    try:
        path = urlparse(value).path.rstrip("/")
        segment = path.split("/")[-1] if path else ""
        if segment and re.fullmatch(r"[A-Za-z0-9_\-]{4,80}", segment):
            # Prefer query job_id if present
            qs = parse_qs(urlparse(value).query)
            for key in ("job_id", "jobId", "id", "jk"):
                if qs.get(key):
                    return qs[key][0]
            return segment
    except Exception:
        pass

    if source_job_id_clean := _clean_non_url(value):
        return source_job_id_clean
    return ""


def _clean_non_url(value: str) -> str:
    if "://" in value:
        return ""
    cleaned = value.strip()
    return cleaned[:80] if cleaned else ""


def is_naukri_listing(*, source: str = "", job_url: str = "", application_url: str = "") -> bool:
    """True if this job came from Naukri."""
    blob = f"{source} {job_url} {application_url}".lower()
    return "naukri" in blob
