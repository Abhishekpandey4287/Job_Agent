"""Brave browser job discovery adapter — read-only, no applications."""

import asyncio
from typing import Dict, List, Tuple

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.discovery.browser.brave_session import (
    BraveSession,
    bind_page,
    resolve_parallel_tabs,
    unbind_page,
)
from job_agent.logging_config import get_logger
from job_agent.schemas import RawJob

logger = get_logger(__name__)

# Focused search matrix — avoids redundant searches per portal
DEFAULT_SEARCH_MATRIX = [
    ("Android Developer", "Mumbai"),
    ("Android Engineer", "Mumbai"),
    ("Kotlin Developer", "Bengaluru"),
    ("Mobile Developer", "Pune"),
    ("Android Developer", "Hyderabad"),
    ("Mobile Engineer", "Remote"),
]

# Portals that often show CAPTCHA — skip remaining queries after block
CAPTCHA_SENSITIVE_PORTALS = frozenset({
    "indeed", "instahyre", "wellfound", "glassdoor", "foundit",
    "hirist", "iimjobs", "cutshort", "shine", "timesjobs",
})


class BraveBrowserAdapter(JobSourceAdapter):
    """Discover jobs via logged-in Brave 'Switch' profile sessions.

    Read-only: searches, parses JDs, extracts URLs. Never clicks Apply/Submit.
    LinkedIn is intentionally excluded — use the separate Referral Agent instead.
    """

    name = "brave_browser"
    # Stop hammering a portal after this many consecutive empty result pages
    MAX_CONSECUTIVE_EMPTY = 3

    def __init__(self):
        settings = get_settings()
        self.enabled = settings.brave_browser_enabled
        self.portals = [
            p for p in (settings.brave_portals_list or [
                "indeed", "glassdoor", "shine", "wellfound", "naukri", "hirist",
                "foundit", "timesjobs", "cutshort", "instahyre", "iimjobs",
            ])
            if p.lower() not in ("linkedin",)
        ]

    async def health_check(self) -> bool:
        return self.enabled

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        jobs, _, _, _ = await self.search_batch([(query, location)], max_per_combo=max_results)
        return jobs[:max_results]

    async def search_batch(
        self,
        search_matrix: List[tuple[str, str]] | None = None,
        max_per_combo: int = 50,
    ) -> Tuple[List[RawJob], List[str], Dict[str, int], Dict[str, str]]:
        """One browser session and one tab per site running at the same time.

        Skips a portal after CAPTCHA or repeated empty pages.

        Returns (jobs, errors, portal_counts, blocked_reasons).
        """
        if not self.enabled:
            return [], [], {}, {}

        matrix = search_matrix or DEFAULT_SEARCH_MATRIX
        all_jobs: List[RawJob] = []
        errors: List[str] = []
        blocked_portals: Dict[str, str] = {}
        portal_counts: Dict[str, int] = {}
        from job_agent.discovery.catalog import browser_extractors

        extractors = [e for e in browser_extractors() if e.name in self.portals]
        tab_count = resolve_parallel_tabs(
            int(getattr(get_settings(), "browser_parallel_tabs", 0) or 0),
            len(extractors),
        )

        try:
            async with BraveSession() as session:
                if tab_count:
                    if tab_count >= len(extractors):
                        print(f"  Browser tabs: {tab_count} (one tab per site, all at once)")
                    else:
                        print(
                            f"  Browser tabs: {tab_count} at a time "
                            f"across {len(extractors)} sites"
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
                    blocked_reason = ""
                    queries_run = 0
                    try:
                        token = bind_page(page)
                        consecutive_empty = 0
                        for query, location in matrix:
                            try:
                                jobs = await extractor.search(
                                    session, query, location, max_per_combo
                                )
                                queries_run += 1

                                if not jobs:
                                    consecutive_empty += 1
                                    blocker = await session.detect_blocker(wait_for_human=False)
                                    if blocker:
                                        blocked_reason = blocker
                                        logger.warning(
                                            "portal_blocked_skipping",
                                            portal=extractor.name,
                                            reason=blocker,
                                        )
                                        break
                                    if consecutive_empty >= self.MAX_CONSECUTIVE_EMPTY:
                                        logger.warning(
                                            "portal_empty_abort",
                                            portal=extractor.name,
                                            empty_streak=consecutive_empty,
                                            hint="selector drift, login wall, or no results",
                                        )
                                        blocked_reason = (
                                            f"0 jobs after {consecutive_empty} queries "
                                            "(selector/login/empty)"
                                        )
                                        break
                                else:
                                    consecutive_empty = 0

                                portal_jobs.extend(jobs)
                                logger.info(
                                    "brave_portal_complete",
                                    portal=extractor.name,
                                    query=query,
                                    location=location,
                                    count=len(jobs),
                                )
                            except Exception as e:
                                msg = f"{extractor.name}/{query}/{location}: {e}"
                                portal_errors.append(msg)
                                logger.error(
                                    "brave_portal_failed", portal=extractor.name, error=str(e)
                                )
                    finally:
                        if token is not None:
                            unbind_page(token)
                        tab_queue.put_nowait(page)

                    status = "BLOCKED" if blocked_reason else "SUCCESS"
                    if status == "SUCCESS" and len(portal_jobs) == 0:
                        status = "EMPTY"
                    logger.info(
                        "brave_portal_summary",
                        portal=extractor.name,
                        total=len(portal_jobs),
                        queries_run=queries_run,
                        status=status,
                    )
                    print(f"  [{extractor.name}] {len(portal_jobs)} jobs")
                    return extractor.name, portal_jobs, portal_errors, blocked_reason

                results = await asyncio.gather(
                    *[_search_portal(extractor) for extractor in extractors],
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, Exception):
                        errors.append(f"brave_portal: {result}")
                        logger.error("brave_portal_failed", error=str(result)[:160])
                        continue
                    name, portal_jobs, portal_errors, blocked_reason = result
                    portal_counts[name] = len(portal_jobs)
                    all_jobs.extend(portal_jobs)
                    errors.extend(portal_errors)
                    if blocked_reason:
                        blocked_portals[name] = blocked_reason
        except Exception as e:
            errors.append(f"brave_session: {e}")
            logger.error("brave_session_failed", error=str(e))

        return all_jobs, errors, portal_counts, blocked_portals
