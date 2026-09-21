"""Find relevant LinkedIn contacts via Brave logged-in session."""

import re
from typing import List, Set
from urllib.parse import quote_plus

from job_agent.discovery.browser.brave_session import BraveSession
from job_agent.logging_config import get_logger
from job_agent.outreach.schemas import ContactPriority, LinkedInContact

logger = get_logger(__name__)

SEARCH_QUERIES = [
    (ContactPriority.RECRUITER, "recruiter"),
    (ContactPriority.RECRUITER, "talent acquisition"),
    (ContactPriority.HIRING_MANAGER, "engineering manager"),
    (ContactPriority.HIRING_MANAGER, "hiring manager"),
    (ContactPriority.ENGINEER, "android engineer"),
    (ContactPriority.ENGINEER, "software engineer"),
    (ContactPriority.ENGINEER, "java developer"),
    (ContactPriority.ENGINEER, "kotlin developer"),
]

MANAGER_KEYWORDS = ["manager", "lead", "head", "director", "vp", "hiring"]
RECRUITER_KEYWORDS = ["recruiter", "talent", "hr", "recruiting", "staffing"]
ENGINEER_KEYWORDS = [
    "engineer", "developer", "architect", "android", "kotlin", "mobile",
    "java", "backend", "software",
]


def _infer_priority(title: str) -> ContactPriority:
    t = title.lower()
    if any(k in t for k in RECRUITER_KEYWORDS):
        return ContactPriority.RECRUITER
    if any(k in t for k in MANAGER_KEYWORDS):
        return ContactPriority.HIRING_MANAGER
    return ContactPriority.ENGINEER


def _relevance_reason(title: str, priority: ContactPriority) -> str:
    if priority == ContactPriority.RECRUITER:
        return "Talent/recruiting role"
    if priority == ContactPriority.HIRING_MANAGER:
        return "Engineering leadership role"
    return "Software engineering role"


class LinkedInContactFinder:
    def __init__(self, session: BraveSession):
        self.session = session

    async def find_contacts(
        self,
        company: str,
        job_title: str,
        max_contacts: int = 3,
    ) -> List[LinkedInContact]:
        contacts: List[LinkedInContact] = []
        seen_urls: Set[str] = set()

        for priority, keyword in SEARCH_QUERIES:
            if len(contacts) >= max_contacts:
                break
            query = f"{keyword} {company}"
            results = await self._search_people(query, company)
            for person in results:
                if len(contacts) >= max_contacts:
                    break
                url_key = person["url"].split("?")[0].rstrip("/")
                if url_key in seen_urls:
                    continue
                seen_urls.add(url_key)
                inferred = _infer_priority(person["title"])
                contacts.append(LinkedInContact(
                    name=person["name"],
                    title=person["title"],
                    profile_url=person["url"],
                    priority=inferred,
                    relevance_reason=_relevance_reason(person["title"], inferred),
                ))
                logger.info(
                    "linkedin_contact_found",
                    name=person["name"],
                    priority=inferred.value,
                    company=company,
                )

        if len(contacts) < max_contacts:
            fallback = await self._search_people(f"{company} engineer OR recruiter", company)
            for person in fallback:
                if len(contacts) >= max_contacts:
                    break
                url_key = person["url"].split("?")[0].rstrip("/")
                if url_key in seen_urls:
                    continue
                seen_urls.add(url_key)
                inferred = _infer_priority(person["title"])
                contacts.append(LinkedInContact(
                    name=person["name"],
                    title=person["title"],
                    profile_url=person["url"],
                    priority=inferred,
                    relevance_reason=_relevance_reason(person["title"], inferred),
                ))

        return contacts[:max_contacts]

    async def _search_people(self, query: str, company: str) -> List[dict]:
        page = self.session.page
        url = (
            "https://www.linkedin.com/search/results/people/"
            f"?keywords={quote_plus(query)}&origin=GLOBAL_SEARCH_HEADER"
        )
        try:
            await self.session.navigate(url, wait_ms=4000)
        except Exception as e:
            logger.warning("linkedin_search_failed", query=query, error=str(e))
            return []

        blocker = await self.session.detect_blocker()
        if blocker:
            logger.warning("linkedin_search_blocked", reason=blocker)
            return []

        results: List[dict] = []
        cards = page.locator(
            "li.reusable-search__result-container, "
            "div.entity-result, "
            "div[data-chameleon-result-urn], "
            "div[data-view-name='search-entity-result-universal-template']"
        )
        count = min(await cards.count(), 10)

        for i in range(count):
            try:
                card = cards.nth(i)
                name = await self._card_text(
                    card,
                    "span.entity-result__title-text a span[aria-hidden='true'], "
                    "a.app-aware-link span[aria-hidden='true'], "
                    ".entity-result__title-line span[aria-hidden='true'], "
                    "span.entity-result__title-text",
                )
                title = await self._card_text(
                    card,
                    ".entity-result__primary-subtitle, "
                    "div.entity-result__summary, "
                    ".entity-result__subtitle",
                )
                href = await self._card_href(
                    card,
                    "a.app-aware-link[href*='/in/'], "
                    "span.entity-result__title-text a[href*='/in/'], "
                    ".entity-result__title-line a[href*='/in/']",
                )

                if not name or not href or "/in/" not in href:
                    continue
                if not href.startswith("http"):
                    href = f"https://www.linkedin.com{href}"
                href = href.split("?")[0]

                results.append({"name": name.strip(), "title": title.strip(), "url": href})
            except Exception:
                continue

        return results

    async def _card_text(self, card, selector: str) -> str:
        el = card.locator(selector).first
        if await el.count() > 0:
            return (await el.inner_text()).strip()
        return ""

    async def _card_href(self, card, selector: str) -> str:
        el = card.locator(selector).first
        if await el.count() > 0:
            return (await el.get_attribute("href")) or ""
        return ""
