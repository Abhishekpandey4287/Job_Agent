"""Scan official career portals. A failed page is a failed scan, not an empty board."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

from product_java_scan.ats import detail_priority, detect_boards, fetch_board, unescape_markup
from product_java_scan.catalog import Company
from product_java_scan.decide import build_result, failed_result
from product_java_scan.html_jobs import (
    extract_html_postings,
    find_jobs_link,
    has_explicit_empty,
    looks_blocked,
    looks_like_js_shell,
    page_description,
)
from product_java_scan.models import BoardFetch, CompanyResult, RawPosting
from product_java_scan.urls import canonical_url

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_MAX_BYTES = 1_500_000


async def read_page(client: httpx.AsyncClient, url: str) -> tuple[int, str, str]:
    response = await client.get(url)
    content = response.content[:_MAX_BYTES]
    text = content.decode(response.encoding or "utf-8", errors="replace")
    return response.status_code, str(response.url), text


async def _enrich_pages(
    client: httpx.AsyncClient,
    postings: list[RawPosting],
    *,
    cap: int,
) -> list[RawPosting]:
    used = 0
    updated: list[RawPosting] = []
    for posting in postings:
        if posting.description or used >= cap or not (posting.url or "").startswith("http"):
            updated.append(posting)
            continue
        if detail_priority(posting.title) >= 50:
            updated.append(posting)
            continue
        used += 1
        try:
            status, _final, html = await read_page(client, posting.url)
        except httpx.HTTPError:
            updated.append(posting)
            continue
        if status != 200 or looks_blocked(status, html):
            updated.append(posting)
            continue
        posting.description = page_description(html) or posting.description
        updated.append(posting)
    return updated


async def scan_company(
    client: httpx.AsyncClient,
    company: Company,
    *,
    detail_cap: int = 12,
) -> CompanyResult:
    try:
        status, final_url, html = await read_page(client, company.portal_url)
    except httpx.TimeoutException:
        return failed_result(company, "Timed out")
    except httpx.HTTPError as exc:
        return failed_result(company, f"Request failed: {exc.__class__.__name__}")

    if not (html or "").strip():
        return failed_result(company, "Empty response", final_url)
    blocked = looks_blocked(status, html)
    if blocked:
        return failed_result(company, blocked, final_url)

    boards = detect_boards(company.portal_url, final_url, unescape_markup(html))
    html_postings = extract_html_postings(html, final_url)
    explicit_empty = has_explicit_empty(html)
    js_shell = looks_like_js_shell(html)
    if not boards and not html_postings:
        follow = find_jobs_link(html, final_url)
        if follow and canonical_url(follow) != canonical_url(final_url):
            try:
                status_2, final_2, html_2 = await read_page(client, follow)
            except httpx.HTTPError:
                status_2, final_2, html_2 = 0, follow, ""
            if status_2 == 200 and html_2 and not looks_blocked(status_2, html_2):
                final_url = final_2
                html = html_2
                boards = detect_boards(company.portal_url, final_url, unescape_markup(html))
                html_postings = extract_html_postings(html, final_url)
                explicit_empty = has_explicit_empty(html)
                js_shell = looks_like_js_shell(html)

    fetches: list[BoardFetch] = []
    for board in boards[:4]:
        try:
            fetches.append(await fetch_board(client, board, detail_cap=detail_cap))
        except httpx.TimeoutException:
            fetches.append(BoardFetch(label=board.label, error="Timed out"))
        except httpx.HTTPError as exc:
            fetches.append(BoardFetch(label=board.label, error=exc.__class__.__name__))
        except Exception as exc:  # noqa: BLE001 — one broken board must not abort the company
            fetches.append(BoardFetch(label=board.label, error=exc.__class__.__name__))

    if html_postings and not any(item.complete and not item.error for item in fetches):
        html_postings = await _enrich_pages(client, html_postings, cap=min(8, detail_cap))

    if js_shell:
        explicit_empty = False
    return build_result(
        company,
        final_url=final_url,
        board_fetches=fetches,
        html_postings=html_postings,
        js_shell=js_shell,
        explicit_empty=explicit_empty,
    )


async def scan_companies(
    companies: list[Company],
    *,
    concurrency: int = 5,
    timeout: float = 20.0,
    detail_cap: int = 12,
    on_result: Callable[[CompanyResult, int, int], None] | None = None,
) -> list[CompanyResult]:
    timeout_cfg = httpx.Timeout(timeout, connect=min(10.0, timeout))
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    limits = httpx.Limits(max_connections=max(concurrency, 1), max_keepalive_connections=max(concurrency, 1))
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    total = len(companies)
    completed = 0

    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=timeout_cfg,
        limits=limits,
    ) as client:

        async def run(company: Company) -> CompanyResult:
            nonlocal completed
            async with semaphore:
                await asyncio.sleep(0.1)
                try:
                    result = await scan_company(client, company, detail_cap=detail_cap)
                except Exception as exc:  # noqa: BLE001 — one company must not abort the run
                    result = failed_result(company, f"Scan error: {exc.__class__.__name__}")
                completed += 1
                if on_result:
                    on_result(result, completed, total)
                return result

        return list(await asyncio.gather(*(run(company) for company in companies)))
