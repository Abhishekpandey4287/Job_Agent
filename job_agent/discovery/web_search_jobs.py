"""Internet-wide job discovery via search engines (DuckDuckGo HTML).

Discovers jobs on company career pages, ATS platforms, and job boards
that may not appear in portal-only searches.
"""

import asyncio
import re
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)

# Patterns that indicate a legitimate job posting URL
JOB_URL_PATTERNS = [
    r"boards\.greenhouse\.io/[^/]+/jobs/\d+",
    r"jobs\.lever\.co/[^/]+/[a-f0-9-]+",
    r"jobs\.ashbyhq\.com/[^/]+/[a-f0-9-]+",
    r"\.myworkdayjobs\.com/",
    r"smartrecruiters\.com/.+/(\d+)",
    r"jobs\.smartrecruiters\.com/",
    r"\.bamboohr\.com/careers/",
    r"\.recruitee\.com/o/",
    r"\.teamtailor\.com/jobs/",
    r"\.jobvite\.com/careers/",
    r"careers\.[^/]+",
    r"/careers/job",
    r"/jobs/view/",
    r"in\.indeed\.com/viewjob",
    r"linkedin\.com/jobs/view",
    r"wellfound\.com/(jobs|role|l)/",
    r"cutshort\.io/job",
    r"instahyre\.com/job",
    r"foundit\.in/job",
    r"glassdoor\.co\.in/job-listing",
    r"glassdoor\.com/job-listing",
    r"hirist\.com/j/",
    r"iimjobs\.com/j/",
    r"shine\.com/job-search",
    r"timesjobs\.com/job-detail",
    r"timesjobs\.com/candidate/job-search",
]

INTERNET_SEARCH_QUERIES = [
    '"Android Engineer" Mumbai jobs site:greenhouse.io OR site:lever.co',
    '"Android Developer" India careers site:greenhouse.io OR site:lever.co',
    '"Kotlin Developer" India hiring',
    '"Mobile Engineer" Mumbai careers',
    '"Backend Engineer" Java India site:greenhouse.io',
    '"Java Developer" "Spring Boot" Mumbai',
    '"Java Backend Engineer" India careers',
    '"Spring Boot Developer" India remote',
    '"Software Engineer" Android Mumbai hiring',
    'Android developer Mumbai site:linkedin.com/jobs OR site:indeed.com',
    'Java backend developer India site:jobs.lever.co OR site:boards.greenhouse.io',
    'fintech android developer India careers',
    'startup kotlin developer Mumbai hiring',
    'SaaS backend engineer Java India jobs',
    'product company android engineer India apply',
    'mobile developer Mumbai site:cutshort.io OR site:instahyre.com',
    'android engineer Bengaluru site:wellfound.com OR site:foundit.in',
    'java developer Pune careers site:myworkdayjobs.com',
    'backend engineer Hyderabad India site:ashbyhq.com',
    'remote android developer India eligible',
    'android developer Mumbai site:hirist.com OR site:iimjobs.com',
    'java developer India site:shine.com OR site:timesjobs.com',
]

LINK_SELECTORS = (
    "a.result__a",
    "a.result-link",
    ".result__body a[href]",
    ".links_main a[href]",
    "a[href*='uddg=']",
)


class WebSearchJobsAdapter(JobSourceAdapter):
    name = "web_search"

    def __init__(self):
        self.enabled = get_settings().web_search_enabled
        self._seen_urls: set[str] = set()
        self.last_status: str = "PENDING"
        self.last_error: str = ""

    async def health_check(self) -> bool:
        return self.enabled

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        if not self.enabled:
            return []
        search_q = f'"{query}" "{location}" India jobs careers hiring apply'
        return await self._search_query(search_q, location, max_results)

    async def search_internet(self, max_total: int = 150) -> tuple[List[RawJob], List[str]]:
        """Run broad internet search queries independent of registry loop."""
        if not self.enabled:
            self.last_status = "DISABLED"
            return [], []

        all_jobs: List[RawJob] = []
        errors: List[str] = []
        queries = list(INTERNET_SEARCH_QUERIES)
        settings = get_settings()
        for role in settings.target_roles[:6]:
            for loc in ["Mumbai", "India", "Remote"]:
                queries.append(
                    f'"{role}" "{loc}" careers jobs apply '
                    f"site:greenhouse.io OR site:lever.co OR site:ashbyhq.com"
                )

        seen_queries: set[str] = set()
        consecutive_failures = 0
        for q in queries:
            if q in seen_queries:
                continue
            seen_queries.add(q)
            if len(all_jobs) >= max_total:
                break
            if consecutive_failures >= 3:
                errors.append("web_search: circuit breaker — DuckDuckGo rate limit")
                logger.warning("web_search_circuit_breaker", failures=consecutive_failures)
                break
            try:
                jobs = await self._search_query(q, "India", max_results=25)
                if jobs:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                all_jobs.extend(jobs)
                logger.info("web_search_query_complete", query=q[:80], count=len(jobs))
                await asyncio.sleep(2.5)
            except Exception as e:
                consecutive_failures += 1
                errors.append(f"web_search/{q[:40]}: {e}")

        self.last_status = "SUCCESS" if all_jobs else ("FAILED" if errors else "SUCCESS")
        return all_jobs[:max_total], errors

    async def _fetch_ddg_html(
        self, client: httpx.AsyncClient, query: str
    ) -> Tuple[Optional[str], str]:
        """POST to DuckDuckGo HTML; retry on 202 rate-limit."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        last_err = ""
        for attempt in range(3):
            try:
                resp = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query, "b": ""},
                    headers=headers,
                )
                if resp.status_code == 200:
                    return resp.text, ""
                if resp.status_code == 202:
                    wait = 3 + attempt * 2
                    logger.warning(
                        "ddg_rate_limited",
                        query=query[:60],
                        attempt=attempt + 1,
                        wait_s=wait,
                    )
                    await asyncio.sleep(wait)
                    last_err = "rate limited (202)"
                    continue
                last_err = f"HTTP {resp.status_code}"
                logger.warning("ddg_search_bad_status", query=query[:60], status=resp.status_code)
                return None, last_err
            except httpx.HTTPError as e:
                last_err = str(e) or type(e).__name__
                logger.warning("ddg_search_failed", query=query[:60], error=last_err)
                await asyncio.sleep(2)
        return None, last_err or "rate limited (202)"

    async def _search_query(self, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers) as client:
            html, err = await self._fetch_ddg_html(client, query)
            if not html:
                self.last_status = "FAILED"
                self.last_error = err or "empty response"
                logger.warning("web_search_no_html", query=query[:60], error=self.last_error)
                return []

            soup = BeautifulSoup(html, "lxml")
            links: List[tuple[str, str]] = []
            seen_hrefs: set[str] = set()
            for selector in LINK_SELECTORS:
                for a in soup.select(selector):
                    href = a.get("href", "")
                    title = a.get_text(strip=True)
                    real_url = self._extract_url(href)
                    if real_url and real_url not in seen_hrefs:
                        seen_hrefs.add(real_url)
                        links.append((real_url, title))

            parsed = 0
            rejected = 0
            for url, snippet_title in links:
                if len(jobs) >= max_results:
                    break
                if not self._is_job_url(url):
                    rejected += 1
                    continue
                key = self._url_key(url)
                if key in self._seen_urls:
                    continue
                self._seen_urls.add(key)

                job = await self._url_to_job(client, url, snippet_title, location)
                if job:
                    jobs.append(job)
                    parsed += 1
                else:
                    rejected += 1

            logger.info(
                "web_search_query_parsed",
                query=query[:60],
                links=len(links),
                parsed=parsed,
                rejected=rejected,
                jobs=len(jobs),
            )
            self.last_status = "SUCCESS"

        return jobs

    def _extract_url(self, href: str) -> Optional[str]:
        if href.startswith("//duckduckgo.com/l/?"):
            href = "https:" + href
        if "uddg=" in href:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        if href.startswith("http"):
            return href
        return None

    def _is_job_url(self, url: str) -> bool:
        return any(re.search(p, url, re.I) for p in JOB_URL_PATTERNS)

    def _url_key(self, url: str) -> str:
        parsed = urlparse(url.lower())
        return f"{parsed.netloc}{parsed.path.rstrip('/')}"

    async def _url_to_job(
        self, client: httpx.AsyncClient, url: str, snippet_title: str, location: str
    ) -> Optional[RawJob]:
        source = self._detect_source(url)
        company = self._company_from_url(url)
        title = snippet_title or "Job Opening"
        description = ""

        try:
            resp = await client.get(url, timeout=15.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                h1 = soup.find("h1")
                if h1 and h1.get_text(strip=True):
                    title = h1.get_text(strip=True)[:200]
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    title = og_title["content"][:200]
                desc_meta = soup.find("meta", attrs={"name": "description"})
                if desc_meta and desc_meta.get("content"):
                    description = desc_meta["content"][:3000]
        except httpx.HTTPError:
            pass

        if len(title) < 5:
            return None

        return RawJob(
            source=source,
            source_job_id=self._url_key(url),
            title=title,
            company=company,
            location=location,
            description=description,
            job_url=url,
            application_url=url,
            date_posted_verified=DateVerification.UNKNOWN,
        )

    def _detect_source(self, url: str) -> str:
        if "greenhouse.io" in url:
            return "greenhouse_careers"
        if "lever.co" in url:
            return "lever_careers"
        if "ashbyhq.com" in url:
            return "ashby_careers"
        if "workday" in url:
            return "workday_careers"
        if "smartrecruiters" in url:
            return "smartrecruiters_careers"
        if "naukri.com" in url:
            return "naukri"
        if "linkedin.com" in url:
            return "linkedin"
        if "indeed.com" in url:
            return "indeed"
        if "foundit" in url:
            return "foundit"
        if "glassdoor" in url:
            return "glassdoor"
        if "hirist.com" in url:
            return "hirist"
        if "iimjobs.com" in url:
            return "iimjobs"
        if "shine.com" in url:
            return "shine"
        if "timesjobs.com" in url:
            return "timesjobs"
        if "careers" in url:
            return "company_careers"
        return "web_search"

    def _company_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.replace("www.", "")
        parts = parsed.path.strip("/").split("/")
        if "greenhouse.io" in host and parts:
            return parts[0].replace("-", " ").title()
        if "lever.co" in host and parts:
            return parts[0].replace("-", " ").title()
        if "ashbyhq.com" in host and parts:
            return parts[0].replace("-", " ").title()
        return host.split(".")[0].replace("-", " ").title()
