"""Public job-board discovery via Playwright (no logged-in profile).

Used when Brave CDP is unavailable. Does NOT claim to use the user's Brave Switch profile.
Read-only: never submits applications.
"""

from typing import List, Protocol

from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.browser.portals import (
    CutshortExtractor,
    FounditExtractor,
    GlassdoorExtractor,
    IndeedExtractor,
    WellfoundExtractor,
)
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
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def page(self):
        return self._page

    async def navigate(self, url: str, wait_ms: int = 3000):
        import asyncio

        await self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(wait_ms / 1000)

    async def detect_blocker(self):
        content = (await self._page.content()).lower()
        url = self._page.url.lower()
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
        self.extractors = [
            IndeedExtractor(),
            CutshortExtractor(),
            WellfoundExtractor(),
            FounditExtractor(),
            GlassdoorExtractor(),
        ]

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
        blocked: set[str] = set()

        try:
            async with _PlaywrightPublicSession() as session:
                for extractor in self.extractors:
                    if extractor.name in blocked:
                        continue
                    for query, location in matrix:
                        try:
                            jobs = await extractor.search(session, query, location, max_per_combo)
                            if not jobs:
                                blocker = await session.detect_blocker()
                                if blocker:
                                    blocked.add(extractor.name)
                                    logger.warning(
                                        "public_portal_blocked",
                                        portal=extractor.name,
                                        reason=blocker,
                                    )
                                    break
                            for job in jobs:
                                job.source = f"public_{extractor.name}"
                            all_jobs.extend(jobs)
                            logger.info(
                                "public_portal_complete",
                                portal=extractor.name,
                                query=query,
                                location=location,
                                count=len(jobs),
                            )
                        except Exception as e:
                            msg = f"public_{extractor.name}/{query}/{location}: {e}"
                            errors.append(msg)
                            logger.error("public_portal_failed", portal=extractor.name, error=str(e))
        except Exception as e:
            errors.append(f"public_browser_session: {e}")
            logger.error("public_browser_session_failed", error=str(e))

        return all_jobs, errors
