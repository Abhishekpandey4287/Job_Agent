"""Public job-board discovery via Playwright (no logged-in profile).

Used when Brave CDP is unavailable. Does NOT claim to use the user's Brave Switch profile.
Read-only: never submits applications.
"""

import asyncio
from typing import List, Protocol

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.browser.brave_session import (
    bind_page,
    current_page,
    resolve_parallel_tabs,
    unbind_page,
)
from job_agent.discovery.catalog import public_browser_extractors
from job_agent.logging_config import get_logger
from job_agent.schemas import RawJob

logger = get_logger(__name__)

PUBLIC_SEARCH_MATRIX = [
    ("Android Developer", "Mumbai"),
    ("Android Engineer", "Mumbai"),
    ("Kotlin Developer", "Mumbai"),
    ("Mobile Developer", "Mumbai"),
    ("Java Developer", "Mumbai"),
    ("Backend Developer", "Mumbai"),
    ("Spring Boot Developer", "Mumbai"),
    ("Java Backend Developer", "Pune"),
    ("Backend Engineer", "Bengaluru"),
    ("Android Developer", "Navi Mumbai"),
    ("Mobile Engineer", "Thane"),
]


class _BrowserLike(Protocol):
    page: object

    async def navigate(self, url: str, wait_ms: int = 3000): ...
    async def detect_blocker(self): ...


class _PlaywrightPublicSession:
    """Ephemeral Chromium session — not the user's Brave profile."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._owned_pages: list = []

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        logger.info("public_browser_session_started")
        return self

    async def __aexit__(self, *args):
        for page in list(getattr(self, "_owned_pages", [])):
            try:
                await page.close()
            except Exception:
                pass
        self._owned_pages = []
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def page(self):
        return current_page(self._page)

    async def new_tab(self):
        page = await self._context.new_page()
        self._owned_pages.append(page)
        return page

    async def navigate(self, url: str, wait_ms: int = 3000):
        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(wait_ms / 1000)

    async def detect_blocker(self):
        page = self.page
        content = (await page.content()).lower()
        url = page.url.lower()
        blockers = [
            ("captcha", "CAPTCHA detected"),
            ("challenge-platform", "Security challenge detected"),
            ("verify you are human", "Human verification required"),
            ("sign in to continue", "Login required"),
        ]
        for signal, reason in blockers:
            if signal in content or signal in url:
                return reason
        return None


class PublicBrowserAdapter(JobSourceAdapter):
    name = "public_browser"

    def __init__(self):
        self.extractors = public_browser_extractors()

    async def health_check(self) -> bool:
        return True

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        jobs, _ = await self.search_batch([(query, location)], max_per_combo=max_results)
        return jobs[:max_results]

    async def search_batch(
        self,
        search_matrix: List[tuple[str, str]] | None = None,
        max_per_combo: int = 15,
    ) -> tuple[List[RawJob], List[str]]:
        matrix = search_matrix or PUBLIC_SEARCH_MATRIX
        all_jobs: List[RawJob] = []
        errors: List[str] = []
        tab_count = resolve_parallel_tabs(
            int(getattr(get_settings(), "browser_parallel_tabs", 0) or 0),
            len(self.extractors),
        )

        try:
            async with _PlaywrightPublicSession() as session:
                if tab_count:
                    if tab_count >= len(self.extractors):
                        print(f"  Browser tabs: {tab_count} (one tab per site, all at once)")
                    else:
                        print(
                            f"  Browser tabs: {tab_count} at a time "
                            f"across {len(self.extractors)} sites"
                        )
                tabs = [await session.new_tab() for _ in range(tab_count)]
                tab_queue: asyncio.Queue = asyncio.Queue()
                for tab in tabs:
                    tab_queue.put_nowait(tab)

                async def _search_portal(extractor):
                    page = await tab_queue.get()
                    token = None
                    portal_jobs: List[RawJob] = []
                    portal_errors: List[str] = []
                    try:
                        token = bind_page(page)
                        for query, location in matrix:
                            try:
                                jobs = await extractor.search(
                                    session, query, location, max_per_combo
                                )
                                if not jobs:
                                    blocker = await session.detect_blocker()
                                    if blocker:
                                        logger.warning(
                                            "public_portal_blocked",
                                            portal=extractor.name,
                                            reason=blocker,
                                        )
                                        break
                                for job in jobs:
                                    job.source = f"public_{extractor.name}"
                                portal_jobs.extend(jobs)
                                logger.info(
                                    "public_portal_complete",
                                    portal=extractor.name,
                                    query=query,
                                    location=location,
                                    count=len(jobs),
                                )
                            except Exception as e:
                                msg = f"public_{extractor.name}/{query}/{location}: {e}"
                                portal_errors.append(msg)
                                logger.error(
                                    "public_portal_failed", portal=extractor.name, error=str(e)
                                )
                    finally:
                        if token is not None:
                            unbind_page(token)
                        tab_queue.put_nowait(page)
                    print(f"  [public_{extractor.name}] {len(portal_jobs)} jobs")
                    return portal_jobs, portal_errors

                results = await asyncio.gather(
                    *[_search_portal(extractor) for extractor in self.extractors],
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, Exception):
                        errors.append(f"public_portal: {result}")
                        logger.error("public_portal_failed", error=str(result)[:160])
                        continue
                    portal_jobs, portal_errors = result
                    all_jobs.extend(portal_jobs)
                    errors.extend(portal_errors)
        except Exception as e:
            errors.append(f"public_browser_session: {e}")
            logger.error("public_browser_session_failed", error=str(e))

        return all_jobs, errors
