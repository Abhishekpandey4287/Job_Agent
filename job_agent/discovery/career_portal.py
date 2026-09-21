"""Resolve the official company career-portal (ATS) job id for a discovered job.

Job boards rarely expose the employer's own requisition id, so this reads it from
the company's public ATS posting instead. Three sources are tried, cheapest first:

1. the job/application URL, when it already points at a known ATS
2. the final URL after following an application-link redirect
3. the public ATS page body — JSON-LD, embedded JSON, data attributes, or a
   visible "Job ID" / "Requisition ID" label
4. an exact title match on the company's own public Greenhouse/Lever/Ashby board

Anything that cannot be established is left blank. An invented or guessed id is
worse than none, because it gets quoted to a recruiter.

Only publicly reachable pages are read. Responses that look like a login wall,
CAPTCHA, or rate limit are abandoned rather than worked around.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple
from urllib.parse import parse_qs, urlparse

import httpx

from job_agent.logging_config import get_logger

logger = get_logger(__name__)

MAX_PAGE_BYTES = 1_500_000

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

REQUISITION_ID = "requisition_id"
POSTING_ID = "posting_id"
JOB_ID = "job_id"
JOB_NUMBER = "job_number"
REFERENCE_ID = "reference_id"

_PORTAL_URL_PATTERNS: Tuple[Tuple[str, re.Pattern[str], str], ...] = (
    ("Greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/[^/]+/jobs/(\d+)", re.I), POSTING_ID),
    ("Greenhouse", re.compile(r"greenhouse\.io/embed/job_app\?[^#]*\bfor=[^&]+&[^#]*\bgh_jid=(\d+)", re.I), POSTING_ID),
    ("Lever", re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?[^/]+/([0-9a-fA-F-]{8,})", re.I), POSTING_ID),
    ("Ashby", re.compile(r"jobs\.ashbyhq\.com/[^/]+/([0-9a-fA-F-]{8,})", re.I), POSTING_ID),
    ("SmartRecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/(?:[^/]+/)*?(\d{6,})", re.I), POSTING_ID),
    (
        "Workday",
        re.compile(
            r"(?:myworkdayjobs|myworkdaysite)\.com/[^?#]*[/_]([A-Za-z]{1,4}-?\d{3,}[A-Za-z0-9-]*)(?:[/?#]|$)",
            re.I,
        ),
        REQUISITION_ID,
    ),
    ("iCIMS", re.compile(r"icims\.com/jobs/(\d{3,})", re.I), JOB_ID),
    ("Taleo", re.compile(r"taleo\.net/[^?#]*?/job/(\d{3,})", re.I), REQUISITION_ID),
    ("Recruitee", re.compile(r"recruitee\.com/o/([A-Za-z0-9][A-Za-z0-9-]{4,})", re.I), POSTING_ID),
    ("BambooHR", re.compile(r"bamboohr\.com/(?:careers|jobs)/(\d{1,})", re.I), JOB_ID),
    ("Workable", re.compile(r"(?:apply\.)?workable\.com/[^/]+/j/([0-9A-Fa-f]{6,})", re.I), POSTING_ID),
    ("Teamtailor", re.compile(r"teamtailor\.com/jobs/(\d{3,})", re.I), POSTING_ID),
    ("Breezy HR", re.compile(r"breezy\.hr/p/([0-9a-fA-F]{8,})", re.I), POSTING_ID),
    ("Jobvite", re.compile(r"jobvite\.com/[^/]+/job/([A-Za-z0-9]{6,})", re.I), POSTING_ID),
    ("JazzHR", re.compile(r"applytojob\.com/apply/([A-Za-z0-9]{6,})", re.I), POSTING_ID),
    ("Personio", re.compile(r"jobs\.personio\.(?:de|com)/[^/]*/?(?:job/)?(\d{5,})", re.I), POSTING_ID),
    ("Eightfold", re.compile(r"\.eightfold\.ai/careers[^#]*\bpid=(\d{6,})", re.I), POSTING_ID),
    ("Rippling", re.compile(r"ats\.rippling\.com/[^/]+/jobs/([0-9a-fA-F-]{8,})", re.I), POSTING_ID),
)

_QUERY_ID_KEYS: Tuple[Tuple[str, str], ...] = (
    ("requisitionid", REQUISITION_ID),
    ("jobrequisitionid", REQUISITION_ID),
    ("career_job_req_id", REQUISITION_ID),
    ("jobreqid", REQUISITION_ID),
    ("reqid", REQUISITION_ID),
    ("req_id", REQUISITION_ID),
    ("gh_jid", POSTING_ID),
    ("jobid", JOB_ID),
    ("job_id", JOB_ID),
    ("pid", POSTING_ID),
)

_PORTAL_HOSTS: Tuple[Tuple[str, str], ...] = (
    ("greenhouse.io", "Greenhouse"),
    ("lever.co", "Lever"),
    ("ashbyhq.com", "Ashby"),
    ("smartrecruiters.com", "SmartRecruiters"),
    ("myworkdayjobs.com", "Workday"),
    ("myworkdaysite.com", "Workday"),
    ("icims.com", "iCIMS"),
    ("taleo.net", "Taleo"),
    ("successfactors.com", "SAP SuccessFactors"),
    ("successfactors.eu", "SAP SuccessFactors"),
    ("sapsf.com", "SAP SuccessFactors"),
    ("recruitee.com", "Recruitee"),
    ("bamboohr.com", "BambooHR"),
    ("workable.com", "Workable"),
    ("teamtailor.com", "Teamtailor"),
    ("breezy.hr", "Breezy HR"),
    ("jobvite.com", "Jobvite"),
    ("applytojob.com", "JazzHR"),
    ("personio.de", "Personio"),
    ("personio.com", "Personio"),
    ("eightfold.ai", "Eightfold"),
    ("rippling.com", "Rippling"),
    ("oraclecloud.com", "Oracle Recruiting"),
    ("phenompeople.com", "Phenom"),
)

_AGGREGATOR_HOSTS = (
    "shine.com", "indeed.com", "naukri.com", "linkedin.com", "glassdoor.",
    "weworkremotely.com", "remoteok.com", "remotive.", "arbeitnow.com",
    "himalayas.app", "adzuna.", "monster.", "ziprecruiter.", "simplyhired.",
    "jooble.", "talent.com", "foundit.", "timesjobs.com", "instahyre.com",
    "cutshort.io", "angel.co", "wellfound.com", "jobgether.com", "google.com",
)

_LABEL_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"requisition\s*(?:id|number|no\.?)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,30})", re.I), REQUISITION_ID),
    (re.compile(r"\breq\s*(?:id|number|no\.?)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,30})", re.I), REQUISITION_ID),
    (re.compile(r"job\s*(?:id|code)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,30})", re.I), JOB_ID),
    (re.compile(r"job\s*(?:number|no\.?)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,30})", re.I), JOB_NUMBER),
    (re.compile(r"posting\s*id\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,30})", re.I), POSTING_ID),
    (re.compile(r"(?:reference|ref)\s*(?:id|number|no\.?|code)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,30})", re.I), REFERENCE_ID),
    (re.compile(r"vacancy\s*(?:id|number|no\.?)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,30})", re.I), REFERENCE_ID),
)

_JSON_ID_KEYS: Tuple[Tuple[str, str], ...] = (
    ("requisitionId", REQUISITION_ID),
    ("requisition_id", REQUISITION_ID),
    ("jobRequisitionId", REQUISITION_ID),
    ("customFieldRequisitionId", REQUISITION_ID),
    ("jobReqId", REQUISITION_ID),
    ("job_req_id", REQUISITION_ID),
    ("reqId", REQUISITION_ID),
    ("req_id", REQUISITION_ID),
    ("jobPostId", POSTING_ID),
    ("job_post_id", POSTING_ID),
    ("jobPostingId", POSTING_ID),
    ("postingId", POSTING_ID),
    ("posting_id", POSTING_ID),
    ("jobNumber", JOB_NUMBER),
    ("job_number", JOB_NUMBER),
    ("jobCode", JOB_ID),
    ("job_code", JOB_ID),
    ("jobId", JOB_ID),
    ("job_id", JOB_ID),
)

_DATA_ATTR_RE = re.compile(
    r"data-(?:job|posting|requisition|req)[-_]?id\s*=\s*[\"']([A-Za-z0-9][A-Za-z0-9._/-]{2,40})[\"']",
    re.I,
)

_BLOCKED_MARKERS = (
    "captcha", "are you a human", "verify you are human", "access denied",
    "cf-browser-verification", "please enable javascript and cookies",
    "sign in to continue", "unusual traffic",
)

_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,60}$")

_ID_BLOCKLIST = {
    "null", "none", "undefined", "n/a", "na", "nan", "true", "false",
    "job", "jobs", "apply", "careers", "career", "search", "unknown", "id",
}

_COMPANY_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|bv|nv|sa|plc|pvt|private|"
    r"technologies|labs|software|solutions|group|holdings)\b\.?",
    re.I,
)

_GENERIC_COMPANY_NAMES = {"unknown", "confidential", "company", "n/a", "na", "", "various"}


@dataclass
class CareerPortalRef:
    portal: str = ""
    job_id: str = ""
    url: str = ""
    id_type: str = ""

    @property
    def found(self) -> bool:
        return bool(self.portal and self.job_id)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_aggregator_url(url: str) -> bool:
    host = _host(url)
    return any(frag in host for frag in _AGGREGATOR_HOSTS)


def portal_for_url(url: str) -> str:
    host = _host(url)
    for fragment, label in _PORTAL_HOSTS:
        if fragment in host:
            return label
    return ""


def _clean_id(value: object) -> str:
    text = str(value or "").strip().strip("#").strip()
    if not text or len(text) > 60:
        return ""
    if text.lower() in _ID_BLOCKLIST:
        return ""
    if not _ID_OK.match(text):
        return ""
    if "-" in text and not any(c.isdigit() for c in text):
        return ""
    return text


def ref_from_url(url: str) -> CareerPortalRef:
    if not url or is_aggregator_url(url):
        return CareerPortalRef()

    for label, pattern, id_type in _PORTAL_URL_PATTERNS:
        match = pattern.search(url)
        if match:
            candidate = _clean_id(match.group(1))
            if candidate:
                return CareerPortalRef(portal=label, job_id=candidate, url=url, id_type=id_type)

    portal = portal_for_url(url)
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        query = {}
    normalized = {k.lower().replace("-", "_"): v for k, v in query.items()}
    for key, id_type in _QUERY_ID_KEYS:
        values = normalized.get(key.lower().replace("-", "_"))
        if not values:
            continue
        candidate = _clean_id(values[0])
        if candidate:
            return CareerPortalRef(
                portal=portal or "Company Careers",
                job_id=candidate,
                url=url,
                id_type=id_type,
            )
    return CareerPortalRef()


def _walk_json(node: object) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json(item)


def _identifier_from_jsonld(html: str) -> Tuple[str, str]:
    for block in re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html,
        re.S | re.I,
    ):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        for node in _walk_json(data):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else list(types or [])
            if not any(str(t).lower() == "jobposting" for t in types):
                continue
            identifier = node.get("identifier")
            if isinstance(identifier, dict):
                candidate = _clean_id(identifier.get("value") or identifier.get("name"))
                if candidate:
                    return candidate, REQUISITION_ID
            elif identifier is not None:
                candidate = _clean_id(identifier)
                if candidate:
                    return candidate, REQUISITION_ID
            for key, id_type in _JSON_ID_KEYS:
                candidate = _clean_id(node.get(key))
                if candidate:
                    return candidate, id_type
    return "", ""


def _identifier_from_embedded_json(html: str) -> Tuple[str, str]:
    for key, id_type in _JSON_ID_KEYS:
        match = re.search(
            r"[\"']" + re.escape(key) + r"[\"']\s*:\s*[\"']?([A-Za-z0-9][A-Za-z0-9._/-]{2,40})[\"']?",
            html,
        )
        if match:
            candidate = _clean_id(match.group(1))
            if candidate:
                return candidate, id_type
    return "", ""


def _identifier_from_attributes(html: str) -> Tuple[str, str]:
    match = _DATA_ATTR_RE.search(html)
    if match:
        candidate = _clean_id(match.group(1))
        if candidate:
            return candidate, JOB_ID
    return "", ""


def _identifier_from_labels(html: str) -> Tuple[str, str]:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    for pattern, id_type in _LABEL_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _clean_id(match.group(1))
            if candidate:
                return candidate, id_type
    return "", ""


def ref_from_page(html: str, url: str) -> CareerPortalRef:
    if not html:
        return CareerPortalRef()
    portal = portal_for_url(url) or "Company Careers"
    for extractor in (
        _identifier_from_jsonld,
        _identifier_from_embedded_json,
        _identifier_from_attributes,
        _identifier_from_labels,
    ):
        candidate, id_type = extractor(html)
        if candidate:
            return CareerPortalRef(portal=portal, job_id=candidate, url=url, id_type=id_type)
    return CareerPortalRef()


def looks_blocked(status_code: int, body: str) -> bool:
    if status_code in (401, 403, 407, 429, 451):
        return True
    sample = (body or "")[:4000].lower()
    return any(marker in sample for marker in _BLOCKED_MARKERS)


def _ats_links_in_page(html: str, limit: int = 3) -> List[str]:
    found: List[str] = []
    for match in re.finditer(r"href=[\"'](https?://[^\"'\s]+)[\"']", html or "", re.I):
        url = match.group(1)
        if is_aggregator_url(url) or not portal_for_url(url):
            continue
        if url not in found:
            found.append(url)
        if len(found) >= limit:
            break
    return found


async def _fetch(client: httpx.AsyncClient, url: str) -> Tuple[int, str, str]:
    async with client.stream("GET", url) as response:
        chunks: List[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_PAGE_BYTES:
                break
        body = b"".join(chunks).decode("utf-8", errors="replace")
        return response.status_code, body, str(response.url)


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def board_slug_candidates(company: str) -> List[str]:
    name = (company or "").strip()
    if _norm_text(name) in _GENERIC_COMPANY_NAMES or len(_norm_text(name)) < 3:
        return []
    base = _COMPANY_SUFFIX_RE.sub("", name)
    base = re.sub(r"[^\w\s-]", "", base).strip()
    if not base:
        return []
    candidates: List[str] = []
    for slug in (
        re.sub(r"[\s_]+", "", base).lower(),
        re.sub(r"[\s_]+", "-", base).lower(),
    ):
        slug = slug.strip("-")
        if slug and slug not in candidates:
            candidates.append(slug)
    return candidates


async def _greenhouse_postings(client: httpx.AsyncClient, slug: str) -> List[Tuple[str, str, str]]:
    response = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if response.status_code != 200:
        return []
    payload = response.json()
    if not isinstance(payload, dict):
        return []
    return [
        (str(j.get("title", "")), str(j.get("id", "")), str(j.get("absolute_url", "")))
        for j in payload.get("jobs", [])
        if j.get("id")
    ]


async def _lever_postings(client: httpx.AsyncClient, slug: str) -> List[Tuple[str, str, str]]:
    response = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if response.status_code != 200:
        return []
    payload = response.json()
    if not isinstance(payload, list):
        return []
    return [
        (str(j.get("text", "")), str(j.get("id", "")), str(j.get("hostedUrl", "")))
        for j in payload
        if j.get("id")
    ]


async def _ashby_postings(client: httpx.AsyncClient, slug: str) -> List[Tuple[str, str, str]]:
    response = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if response.status_code != 200:
        return []
    payload = response.json()
    if not isinstance(payload, dict):
        return []
    return [
        (str(j.get("title", "")), str(j.get("id", "")), str(j.get("jobUrl", "")))
        for j in payload.get("jobs", [])
        if j.get("id")
    ]


_BOARD_PROVIDERS: Tuple[Tuple[str, object], ...] = (
    ("Greenhouse", _greenhouse_postings),
    ("Lever", _lever_postings),
    ("Ashby", _ashby_postings),
)


async def _greenhouse_board_name(client: httpx.AsyncClient, slug: str) -> str:
    try:
        response = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
        if response.status_code != 200:
            return ""
        payload = response.json()
        return str(payload.get("name", "")) if isinstance(payload, dict) else ""
    except Exception:
        return ""


def _board_identity_matches(company: str, board_name: str) -> bool:
    left = _norm_text(_COMPANY_SUFFIX_RE.sub("", company or ""))
    right = _norm_text(_COMPANY_SUFFIX_RE.sub("", board_name or ""))
    if not right:
        return True
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 8 and shorter in longer


async def resolve_via_company_board(
    client: httpx.AsyncClient,
    company: str,
    title: str,
    *,
    cache: dict | None = None,
) -> CareerPortalRef:
    if not title.strip():
        return CareerPortalRef()
    cache = cache if cache is not None else {}
    wanted = _norm_text(title)

    for slug in board_slug_candidates(company):
        for portal, fetch_postings in _BOARD_PROVIDERS:
            key = (portal, slug)
            if key not in cache:
                try:
                    cache[key] = await fetch_postings(client, slug)
                except Exception:
                    cache[key] = []
            postings = cache[key]
            if not postings:
                continue

            exact = [p for p in postings if _norm_text(p[0]) == wanted]
            if len(exact) != 1:
                continue

            if portal == "Greenhouse":
                name_key = ("gh_name", slug)
                if name_key not in cache:
                    cache[name_key] = await _greenhouse_board_name(client, slug)
                if not _board_identity_matches(company, cache[name_key]):
                    continue

            _, job_id, url = exact[0]
            cleaned = _clean_id(job_id)
            if cleaned:
                return CareerPortalRef(
                    portal=portal, job_id=cleaned, url=url, id_type=POSTING_ID
                )
    return CareerPortalRef()


async def resolve_for_urls(
    client: httpx.AsyncClient,
    urls: Sequence[str],
    *,
    allow_network: bool = True,
) -> CareerPortalRef:
    candidates = [u for u in urls if u and u.startswith("http")]

    for url in candidates:
        ref = ref_from_url(url)
        if ref.found:
            return ref

    if not allow_network:
        return CareerPortalRef()

    for url in candidates:
        try:
            status, body, final_url = await _fetch(client, url)
        except Exception as e:
            logger.debug("career_portal_fetch_failed", url=url[:120], error=str(e)[:120])
            continue

        if looks_blocked(status, body):
            logger.debug("career_portal_blocked", url=url[:120], status=status)
            continue
        if status >= 400:
            continue

        if final_url != url:
            ref = ref_from_url(final_url)
            if ref.found:
                return ref

        if portal_for_url(final_url):
            ref = ref_from_page(body, final_url)
            if ref.found:
                return ref

        for link in _ats_links_in_page(body):
            ref = ref_from_url(link)
            if ref.found:
                return ref

    return CareerPortalRef()


async def resolve_career_portal_ids(
    jobs: Sequence[object],
    *,
    allow_network: bool = True,
    timeout_seconds: float = 12.0,
    delay_seconds: float = 0.5,
    concurrency: int = 4,
) -> int:
    if not jobs:
        return 0

    resolved = 0
    semaphore = asyncio.Semaphore(max(1, concurrency))
    headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,application/json"}
    board_cache: dict = {}

    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:

        async def _one(job) -> bool:
            if getattr(job, "career_portal_job_id", ""):
                return False
            async with semaphore:
                try:
                    ref = await resolve_for_urls(
                        client,
                        [
                            getattr(job, "application_url", "") or "",
                            getattr(job, "job_url", "") or "",
                        ],
                        allow_network=allow_network,
                    )
                    if not ref.found and allow_network:
                        ref = await resolve_via_company_board(
                            client,
                            getattr(job, "company", "") or "",
                            getattr(job, "title", "") or "",
                            cache=board_cache,
                        )
                except Exception as e:
                    logger.debug(
                        "career_portal_resolve_failed",
                        company=getattr(job, "company", "")[:60],
                        error=str(e)[:120],
                    )
                    return False
                if delay_seconds:
                    await asyncio.sleep(delay_seconds)

            if not ref.found:
                return False
            job.career_portal = ref.portal
            job.career_portal_job_id = ref.job_id
            job.career_portal_url = ref.url
            job.career_job_id_type = ref.id_type
            return True

        results = await asyncio.gather(*[_one(j) for j in jobs], return_exceptions=True)

    for outcome in results:
        if outcome is True:
            resolved += 1
    logger.info("career_portal_resolved", resolved=resolved, total=len(jobs))
    return resolved
