"""Read job postings that are present in a career page response."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from product_java_scan.models import RawPosting
from product_java_scan.textutil import normalize_posted, plain_text
from product_java_scan.urls import canonical_url

_NO_OPENINGS = re.compile(
    r"no (current |open )?(openings|positions|jobs|vacancies)|"
    r"\b0 (open )?jobs\b|no results found|no open positions",
    re.I,
)
_JOB_HREF = re.compile(
    r"(gh_jid=|/jobs/\d|/job/\d|requisition|/positions/\d|lever\.co/[^/]+/.+|ashbyhq\.com/[^/]+/.+)",
    re.I,
)
_NAV_TEXT = re.compile(
    r"^(search jobs|view all|view all jobs|careers|learn more|apply now|read more|"
    r"see openings|job openings|open positions)$",
    re.I,
)
_FOLLOW_TEXT = re.compile(
    r"view (all |open )?(jobs|roles|positions|openings)|"
    r"see (all |open )?(jobs|roles|openings)|open positions|job openings|"
    r"search jobs|explore roles|see openings",
    re.I,
)
_FOLLOW_PATH = re.compile(r"/(jobs|openings|open-positions|vacancies|positions)(/|$)", re.I)


def looks_blocked(status: int, html: str) -> str:
    if status in {401, 403, 429, 503}:
        return f"HTTP {status}"
    if status >= 400:
        return f"HTTP {status}"
    sample = (html or "")[:2500].lower()
    if "just a moment" in sample or "cf-browser-verification" in sample or "attention required" in sample:
        return "Blocked by a browser challenge"
    if "access denied" in sample and len(html or "") < 8000:
        return "Access denied"
    return ""


def looks_like_js_shell(html: str) -> bool:
    soup = BeautifulSoup(html or "", "lxml")
    text = soup.get_text(" ", strip=True)
    return len(text) < 500 and len(soup.find_all("script")) >= 2


def has_explicit_empty(html: str) -> bool:
    text = BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)
    return bool(_NO_OPENINGS.search(text))


def _walk_jsonld(node: object):
    if isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    if "JobPosting" in kinds:
        yield node
    graph = node.get("@graph")
    if graph:
        yield from _walk_jsonld(graph)


def _jsonld_location(node: dict) -> str:
    location = node.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else {}
    if isinstance(location, str):
        return location
    if not isinstance(location, dict):
        return ""
    address = location.get("address") or {}
    if isinstance(address, list):
        address = address[0] if address else {}
    if isinstance(address, str):
        return address
    if not isinstance(address, dict):
        return str(location.get("name") or "")
    parts = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("addressCountry"),
    ]
    return ", ".join(str(part) for part in parts if part)


def _jsonld_postings(soup: BeautifulSoup, base_url: str) -> list[RawPosting]:
    postings: list[RawPosting] = []
    for script in soup.find_all("script"):
        script_type = " ".join(script.get("type") or []).lower() if isinstance(script.get("type"), list) else str(script.get("type") or "").lower()
        if "ld+json" not in script_type:
            continue
        raw = script.string or script.get_text() or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _walk_jsonld(payload):
            title = str(node.get("title") or "").strip()
            if not title:
                continue
            url_value = node.get("url")
            url = urljoin(base_url, url_value) if isinstance(url_value, str) and url_value else ""
            postings.append(
                RawPosting(
                    title=title,
                    location=_jsonld_location(node),
                    url=url,
                    description=plain_text(str(node.get("description") or "")),
                    posting_date=normalize_posted(node.get("datePosted")),
                    verified_via="official_portal",
                )
            )
    return postings


def extract_html_postings(html: str, base_url: str) -> list[RawPosting]:
    soup = BeautifulSoup(html or "", "lxml")
    postings = _jsonld_postings(soup, base_url)
    seen = {canonical_url(posting.url) or posting.title.casefold() for posting in postings}
    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True)
        if not text or len(text) < 8 or len(text) > 140 or _NAV_TEXT.search(text):
            continue
        href = urljoin(base_url, anchor["href"])
        if not href.startswith(("http://", "https://")) or not _JOB_HREF.search(href):
            continue
        key = canonical_url(href) or text.casefold()
        if key in seen:
            continue
        seen.add(key)
        postings.append(
            RawPosting(
                title=text,
                location="",
                url=href,
                verified_via="official_portal",
            )
        )
    return postings


def page_description(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for posting in _jsonld_postings(soup, "https://example.com"):
        if posting.description:
            return posting.description[:5000]
    for selector in ("article", "main", "[class*=job-description]", "[id*=job-description]"):
        node = soup.select_one(selector)
        if not node:
            continue
        text = node.get_text(" ", strip=True)
        if len(text) > 80:
            return text[:5000]
    return ""


def find_jobs_link(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    base = canonical_url(base_url)
    host = urlsplit(base_url).netloc.lower()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"])
        if not href.startswith(("http://", "https://")):
            continue
        parts = urlsplit(href)
        if parts.netloc.lower() != host:
            continue
        text = anchor.get_text(" ", strip=True)
        if not (_FOLLOW_TEXT.search(text) or _FOLLOW_PATH.search(parts.path or "")):
            continue
        if canonical_url(href) and canonical_url(href) != base:
            return href
    return ""
