"""Detect public ATS boards linked from a career portal and read their postings."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from product_java_scan.models import Board, BoardFetch, RawPosting
from product_java_scan.scoring import is_excluded_title, is_generic_engineering_title
from product_java_scan.textutil import normalize_posted, plain_text

_SLUG_BLOCK = {
    "embed",
    "jobs",
    "job",
    "v1",
    "careers",
    "www",
    "api",
    "en",
    "en-us",
    "job_board",
    "job-board",
}
_GH = re.compile(
    r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)",
    re.I,
)
_GH_API = re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)
_LEVER = re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)", re.I)
_ASHBY = re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", re.I)
_SMART = re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)
_WORKABLE = re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)
_RECRUITEE = re.compile(r"https?://([A-Za-z0-9-]+)\.recruitee\.com", re.I)
_WORKDAY = re.compile(
    r"https?://([A-Za-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#\s]+)",
    re.I,
)
_ORACLE = re.compile(
    r"https?://([A-Za-z0-9.-]+\.oraclecloud\.com)"
    r"/hcmUI/CandidateExperience/[^/\s]+/sites/([^/?#\s]+)",
    re.I,
)
_LOCALE = re.compile(r"^[a-z]{2}-[A-Za-z]{2}/")


def unescape_markup(html: str) -> str:
    return (html or "").replace("\\/", "/").replace("\\u002f", "/").replace("\\u002F", "/")


def _valid_slug(slug: str) -> str:
    cleaned = (slug or "").strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,80}", cleaned):
        return ""
    if cleaned.casefold() in _SLUG_BLOCK:
        return ""
    return cleaned


def _workday_site(path: str) -> str:
    site = _LOCALE.sub("", path.strip("/"))
    return site.split("/")[0]


def board_from_url(url: str) -> Board | None:
    raw = (url or "").strip()
    if not raw:
        return None
    workday = _WORKDAY.search(raw)
    if workday:
        site = _workday_site(workday.group(3))
        if site:
            return Board(
                kind="workday",
                tenant=workday.group(1),
                cluster=workday.group(2),
                site=site,
            )
    oracle = _ORACLE.search(raw)
    if oracle:
        return Board(kind="oracle", site=oracle.group(2), host=oracle.group(1))
    for pattern, kind in (
        (_GH, "greenhouse"),
        (_GH_API, "greenhouse"),
        (_LEVER, "lever"),
        (_ASHBY, "ashby"),
        (_SMART, "smartrecruiters"),
        (_WORKABLE, "workable"),
    ):
        match = pattern.search(raw)
        slug = _valid_slug(match.group(1) if match else "")
        if slug:
            return Board(kind=kind, slug=slug)
    recruitee = _RECRUITEE.search(raw)
    slug = _valid_slug(recruitee.group(1) if recruitee else "")
    if slug:
        return Board(kind="recruitee", slug=slug.lower())
    return None


def detect_boards(portal_url: str, final_url: str, html: str) -> list[Board]:
    found: list[Board] = []

    def add(board: Board | None) -> None:
        if board and board not in found:
            found.append(board)

    add(board_from_url(final_url))
    add(board_from_url(portal_url))
    text = unescape_markup(f"{portal_url}\n{final_url}\n{html or ''}")
    patterns = (
        (_WORKDAY, "workday"),
        (_ORACLE, "oracle"),
        (_GH, "greenhouse"),
        (_GH_API, "greenhouse"),
        (_LEVER, "lever"),
        (_ASHBY, "ashby"),
        (_SMART, "smartrecruiters"),
        (_WORKABLE, "workable"),
        (_RECRUITEE, "recruitee"),
    )
    for pattern, kind in patterns:
        for match in pattern.finditer(text):
            if kind == "workday":
                site = _workday_site(match.group(3))
                if site:
                    add(Board(kind="workday", tenant=match.group(1), cluster=match.group(2), site=site))
            elif kind == "oracle":
                add(Board(kind="oracle", site=match.group(2), host=match.group(1)))
            else:
                slug = _valid_slug(match.group(1))
                if kind == "recruitee" and slug:
                    slug = slug.lower()
                if slug:
                    add(Board(kind=kind, slug=slug))
            if len(found) >= 4:
                return found
    return found


def detail_priority(title: str) -> int:
    if is_excluded_title(title):
        return 99
    lowered = (title or "").lower()
    if "spring" in lowered or re.search(r"\bjava\b", lowered):
        return 0
    if "backend" in lowered or "back-end" in lowered or "microservice" in lowered or "full" in lowered:
        return 1
    if is_generic_engineering_title(title):
        return 2
    return 50


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
) -> tuple[int, object | None]:
    response = await client.get(url, params=params)
    if response.status_code != 200:
        return response.status_code, None
    try:
        return response.status_code, response.json()
    except json.JSONDecodeError:
        return response.status_code, None


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
) -> tuple[int, object | None]:
    response = await client.post(url, json=payload)
    if response.status_code != 200:
        return response.status_code, None
    try:
        return response.status_code, response.json()
    except json.JSONDecodeError:
        return response.status_code, None


def _location_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("locationName") or "")
    if isinstance(value, str):
        return value
    return ""


async def fetch_board(
    client: httpx.AsyncClient,
    board: Board,
    *,
    detail_cap: int = 12,
) -> BoardFetch:
    if board.kind == "greenhouse":
        return await _greenhouse(client, board, detail_cap)
    if board.kind == "lever":
        return await _lever(client, board)
    if board.kind == "ashby":
        return await _ashby(client, board)
    if board.kind == "smartrecruiters":
        return await _smartrecruiters(client, board, detail_cap)
    if board.kind == "workable":
        return await _workable(client, board)
    if board.kind == "workday":
        return await _workday(client, board)
    if board.kind == "oracle":
        return await _oracle(client, board)
    if board.kind == "recruitee":
        return await _recruitee(client, board)
    return BoardFetch(label=board.label, error=f"Unsupported ATS {board.kind}")


async def _greenhouse(client: httpx.AsyncClient, board: Board, detail_cap: int) -> BoardFetch:
    status, data = await get_json(
        client, f"https://boards-api.greenhouse.io/v1/boards/{board.slug}/jobs"
    )
    if status != 200 or not isinstance(data, dict) or "jobs" not in data:
        return BoardFetch(label=board.label, error=f"HTTP {status}")
    jobs = [item for item in data.get("jobs") or [] if isinstance(item, dict)]
    ranked = sorted(jobs, key=lambda item: detail_priority(str(item.get("title") or "")))
    details: dict[str, dict] = {}
    fetched = 0
    for item in ranked:
        if detail_priority(str(item.get("title") or "")) >= 50 or fetched >= detail_cap:
            break
        job_id = item.get("id")
        if not job_id:
            continue
        detail_status, body = await get_json(
            client,
            f"https://boards-api.greenhouse.io/v1/boards/{board.slug}/jobs/{job_id}",
        )
        fetched += 1
        if detail_status == 200 and isinstance(body, dict):
            details[str(job_id)] = body
    postings: list[RawPosting] = []
    for item in jobs:
        job_id = str(item.get("id") or "")
        detail = details.get(job_id) or {}
        postings.append(
            RawPosting(
                title=str(item.get("title") or ""),
                location=_location_name(item.get("location")),
                url=str(item.get("absolute_url") or ""),
                description=plain_text(str(detail.get("content") or "")),
                posting_date=normalize_posted(detail.get("updated_at") or item.get("updated_at")),
                verified_via="ats:greenhouse",
            )
        )
    return BoardFetch(label=board.label, postings=postings, complete=True)


async def _lever(client: httpx.AsyncClient, board: Board) -> BoardFetch:
    status, data = await get_json(
        client, f"https://api.lever.co/v0/postings/{board.slug}", params={"mode": "json"}
    )
    if status != 200 or not isinstance(data, list):
        return BoardFetch(label=board.label, error=f"HTTP {status}")
    postings: list[RawPosting] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        categories = item.get("categories") or {}
        location = str(categories.get("location") or "")
        country = str(categories.get("country") or "")
        if country and country.casefold() not in location.casefold():
            location = ", ".join(part for part in (location, country) if part)
        postings.append(
            RawPosting(
                title=str(item.get("text") or ""),
                location=location,
                url=str(item.get("hostedUrl") or item.get("applyUrl") or ""),
                description=plain_text(
                    str(item.get("descriptionPlain") or item.get("description") or "")
                ),
                posting_date=normalize_posted(item.get("createdAt")),
                verified_via="ats:lever",
            )
        )
    return BoardFetch(label=board.label, postings=postings, complete=True)


async def _ashby(client: httpx.AsyncClient, board: Board) -> BoardFetch:
    status, data = await get_json(
        client, f"https://api.ashbyhq.com/posting-api/job-board/{board.slug}"
    )
    if status != 200 or not isinstance(data, dict) or "jobs" not in data:
        return BoardFetch(label=board.label, error=f"HTTP {status}")
    postings: list[RawPosting] = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        location = _location_name(item.get("location")) or str(address.get("postalAddress") or "")
        postal = address.get("postalAddress") if isinstance(address.get("postalAddress"), dict) else {}
        if postal:
            location = ", ".join(
                part
                for part in (
                    postal.get("addressLocality"),
                    postal.get("addressRegion"),
                    postal.get("addressCountry"),
                )
                if part
            )
        postings.append(
            RawPosting(
                title=str(item.get("title") or ""),
                location=location,
                url=str(item.get("jobUrl") or item.get("applyUrl") or ""),
                description=plain_text(
                    str(item.get("descriptionPlain") or item.get("descriptionHtml") or "")
                ),
                posting_date=normalize_posted(item.get("publishedAt") or item.get("updatedAt")),
                verified_via="ats:ashby",
            )
        )
    return BoardFetch(label=board.label, postings=postings, complete=True)


async def _smartrecruiters(
    client: httpx.AsyncClient, board: Board, detail_cap: int
) -> BoardFetch:
    postings_raw: list[dict] = []
    offset = 0
    total = None
    short_page = False
    while offset < 100:
        status, data = await get_json(
            client,
            f"https://api.smartrecruiters.com/v1/companies/{board.slug}/postings",
            params={"limit": 100, "offset": offset},
        )
        if status != 200 or not isinstance(data, dict):
            return BoardFetch(label=board.label, error=f"HTTP {status}")
        content = [item for item in data.get("content") or [] if isinstance(item, dict)]
        total = data.get("totalFound") if isinstance(data.get("totalFound"), int) else total
        postings_raw.extend(content)
        if len(content) < 100:
            short_page = True
            break
        offset += len(content)
        if total is not None and offset >= total:
            break
    ranked = sorted(postings_raw, key=lambda item: detail_priority(str(item.get("name") or "")))
    details: dict[str, str] = {}
    fetched = 0
    for item in ranked:
        if detail_priority(str(item.get("name") or "")) >= 50 or fetched >= detail_cap:
            break
        job_id = str(item.get("id") or "")
        if not job_id:
            continue
        detail_status, body = await get_json(
            client,
            f"https://api.smartrecruiters.com/v1/companies/{board.slug}/postings/{job_id}",
        )
        fetched += 1
        if detail_status == 200 and isinstance(body, dict):
            sections = ((body.get("jobAd") or {}).get("sections") or {})
            chunks = []
            if isinstance(sections, dict):
                for section in sections.values():
                    if isinstance(section, dict) and section.get("text"):
                        chunks.append(str(section["text"]))
            details[job_id] = plain_text(" ".join(chunks))
    postings: list[RawPosting] = []
    for item in postings_raw:
        job_id = str(item.get("id") or "")
        location_data = item.get("location") or {}
        if not isinstance(location_data, dict):
            location_data = {}
        location = ", ".join(
            part
            for part in (
                location_data.get("city"),
                location_data.get("region"),
                location_data.get("country"),
            )
            if part
        )
        postings.append(
            RawPosting(
                title=str(item.get("name") or ""),
                location=location,
                url=f"https://jobs.smartrecruiters.com/{board.slug}/{job_id}",
                description=details.get(job_id, ""),
                posting_date=normalize_posted(item.get("releasedDate")),
                verified_via="ats:smartrecruiters",
            )
        )
    reached_total = total is not None and len(postings_raw) >= total
    return BoardFetch(
        label=board.label,
        postings=postings,
        complete=reached_total or short_page,
    )


async def _workable(client: httpx.AsyncClient, board: Board) -> BoardFetch:
    status, data = await get_json(
        client, f"https://apply.workable.com/api/v1/widget/accounts/{board.slug}"
    )
    if status != 200 or not isinstance(data, dict) or "jobs" not in data:
        return BoardFetch(label=board.label, error=f"HTTP {status}")
    postings: list[RawPosting] = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue
        location = ", ".join(part for part in (item.get("city"), item.get("country")) if part)
        shortcode = str(item.get("shortcode") or "")
        url = str(item.get("url") or item.get("application_url") or "")
        if not url and shortcode:
            url = f"https://apply.workable.com/{board.slug}/j/{shortcode}"
        postings.append(
            RawPosting(
                title=str(item.get("title") or ""),
                location=location,
                url=url,
                description=plain_text(str(item.get("description") or "")),
                posting_date=normalize_posted(item.get("published_on")),
                verified_via="ats:workable",
            )
        )
    return BoardFetch(label=board.label, postings=postings, complete=True)


async def _workday_pages(
    client: httpx.AsyncClient,
    endpoint: str,
    search_text: str,
) -> tuple[int, list[dict], bool]:
    collected: list[dict] = []
    offset = 0
    total = None
    status = 0
    while offset < 80:
        status, data = await post_json(
            client,
            endpoint,
            {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search_text},
        )
        if status != 200 or not isinstance(data, dict):
            return status, collected, False
        if isinstance(data.get("total"), int):
            total = data["total"]
        page = [item for item in data.get("jobPostings") or [] if isinstance(item, dict)]
        collected.extend(page)
        if not page:
            break
        offset += len(page)
        if total is not None and offset >= total:
            break
    complete = total is not None and len(collected) >= total
    return status, collected, complete


async def _workday(client: httpx.AsyncClient, board: Board) -> BoardFetch:
    endpoint = (
        f"https://{board.tenant}.{board.cluster}.myworkdayjobs.com"
        f"/wday/cxs/{board.tenant}/{board.site}/jobs"
    )
    status, rows, complete = await _workday_pages(client, endpoint, "")
    if status != 200:
        status, rows, _keyword_complete = await _workday_pages(client, endpoint, "Java")
        complete = False
        if status != 200:
            return BoardFetch(label=board.label, error=f"HTTP {status}")
    postings: list[RawPosting] = []
    base = f"https://{board.tenant}.{board.cluster}.myworkdayjobs.com"
    for item in rows:
        path = str(item.get("externalPath") or "")
        bullets = item.get("bulletFields") or []
        description = " ".join(str(bullet) for bullet in bullets if bullet)
        postings.append(
            RawPosting(
                title=str(item.get("title") or ""),
                location=str(item.get("locationsText") or ""),
                url=f"{base}/{board.site}{path}",
                description=plain_text(description),
                posting_date=normalize_posted(item.get("postedOn")),
                verified_via="ats:workday",
            )
        )
    return BoardFetch(label=board.label, postings=postings, complete=complete)


def _oracle_block(data: object) -> tuple[list[dict], int | None]:
    if not isinstance(data, dict):
        return [], None
    items = data.get("items") or []
    if not items or not isinstance(items[0], dict):
        return [], 0
    block = items[0]
    rows = [row for row in block.get("requisitionList") or [] if isinstance(row, dict)]
    total = block.get("TotalJobsCount")
    return rows, total if isinstance(total, int) else None


async def _oracle(client: httpx.AsyncClient, board: Board) -> BoardFetch:
    host = board.host or ""
    if not host:
        parsed = urlsplit(board.site)
        host = parsed.netloc
    endpoint = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    rows: list[dict] = []
    offset = 0
    total: int | None = None
    complete = False
    while offset < 75:
        finder = f"findReqs;siteNumber={board.site},limit=25,offset={offset}"
        status, data = await get_json(
            client,
            endpoint,
            params={
                "onlyData": "true",
                "expand": "requisitionList.secondaryLocations,requisitionList.otherWorkLocations",
                "finder": finder,
            },
        )
        if status != 200:
            return BoardFetch(label=board.label, error=f"HTTP {status}")
        page, page_total = _oracle_block(data)
        if page_total is not None:
            total = page_total
        rows.extend(page)
        if len(page) < 25:
            complete = True
            break
        offset += 25
        if total is not None and offset >= total:
            complete = True
            break
    postings: list[RawPosting] = []
    for item in rows:
        job_id = str(item.get("Id") or "")
        description = plain_text(
            " ".join(
                str(item.get(key) or "")
                for key in ("ShortDescriptionStr", "ExternalDescriptionStr", "ExternalQualificationsStr")
            )
        )
        url = ""
        if job_id and host:
            url = (
                f"https://{host}/hcmUI/CandidateExperience/en/sites/{board.site}/job/{job_id}"
            )
        postings.append(
            RawPosting(
                title=str(item.get("Title") or ""),
                location=str(item.get("PrimaryLocation") or ""),
                url=url,
                description=description,
                posting_date=normalize_posted(item.get("PostedDate")),
                verified_via="ats:oracle",
            )
        )
    if total == 0 or (total is not None and len(rows) >= total):
        complete = True
    return BoardFetch(label=board.label, postings=postings, complete=complete)


async def _recruitee(client: httpx.AsyncClient, board: Board) -> BoardFetch:
    status, data = await get_json(client, f"https://{board.slug}.recruitee.com/api/offers/")
    if status != 200 or not isinstance(data, dict) or "offers" not in data:
        return BoardFetch(label=board.label, error=f"HTTP {status}")
    postings: list[RawPosting] = []
    for item in data.get("offers") or []:
        if not isinstance(item, dict):
            continue
        location = str(item.get("location") or "")
        if not location:
            location = ", ".join(part for part in (item.get("city"), item.get("country")) if part)
        postings.append(
            RawPosting(
                title=str(item.get("title") or ""),
                location=location,
                url=str(item.get("careers_url") or item.get("url") or ""),
                description=plain_text(str(item.get("description") or "")),
                posting_date=normalize_posted(item.get("published_at")),
                verified_via="ats:recruitee",
            )
        )
    return BoardFetch(label=board.label, postings=postings, complete=True)
