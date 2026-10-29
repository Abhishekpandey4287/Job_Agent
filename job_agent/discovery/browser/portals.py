"""Portal-specific job extractors for read-only browser discovery."""

import re
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import quote_plus

from job_agent.discovery.browser.brave_session import BraveSession
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)


async def _safe_text(page, selector: str, timeout: int = 3000) -> str:
    try:
        el = page.locator(selector).first
        if await el.count() > 0:
            return (await el.inner_text(timeout=timeout)).strip()
    except Exception:
        pass
    return ""


async def _safe_attr(page, selector: str, attr: str = "href") -> str:
    try:
        el = page.locator(selector).first
        if await el.count() > 0:
            return (await el.get_attribute(attr)) or ""
    except Exception:
        pass
    return ""


def _parse_relative_date(text: str) -> tuple[Optional[datetime], DateVerification]:
    text = text.lower().strip()
    now = datetime.utcnow()
    if "just now" in text or "today" in text or "hour" in text:
        hours = 1
        m = re.search(r"(\d+)\s*hour", text)
        if m:
            hours = int(m.group(1))
        return now - timedelta(hours=hours), DateVerification.VERIFIED
    m = re.search(r"(\d+)\s*day", text)
    if m:
        days = int(m.group(1))
        return now - timedelta(days=days), DateVerification.VERIFIED
    return None, DateVerification.UNKNOWN


class LinkedInExtractor:
    """Deprecated for job discovery — LinkedIn portal scraping is disabled.

    Kept only as reference. Use scripts/run_referral_agent.py for referrals.
    """

    name = "linkedin"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={quote_plus(query)}&location={quote_plus(location)}"
            f"&f_TPR=r86400&f_E=2,3,4"
        )
        await session.navigate(url, wait_ms=4000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("linkedin_blocked", reason=blocker)
            return jobs

        page = session.page
        cards = page.locator(".job-card-container, .jobs-search-results__list-item, li.jobs-search-results__list-item")
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(card, ".job-card-list__title, .base-search-card__title, a[data-tracking-control-name]")
                company = await _text_from(card, ".job-card-container__company-name, .base-search-card__subtitle")
                loc = await _text_from(card, ".job-card-container__metadata-item, .job-search-card__location")
                link = await _href_from(card, "a[href*='/jobs/view/']")
                date_text = await _text_from(card, "time, .job-search-card__listdate")

                if not title or not link:
                    continue

                posted, verified = _parse_relative_date(date_text)

                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=_extract_id(link, r"jobs/view/(\d+)"),
                    title=title,
                    company=company or "Unknown",
                    location=loc or location,
                    job_url=link,
                    application_url=link,
                    date_posted=posted,
                    date_posted_verified=verified,
                    is_remote="remote" in (loc or "").lower(),
                ))
            except Exception as e:
                logger.debug("linkedin_card_parse_error", index=i, error=str(e))

        if jobs:
            jobs = await self._enrich_descriptions(session, jobs[:min(len(jobs), 10)])
        return jobs[:max_results]

    async def _enrich_descriptions(self, session: BraveSession, jobs: List[RawJob]) -> List[RawJob]:
        page = session.page
        for job in jobs:
            try:
                await page.goto(job.job_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                desc = await _safe_text(page, ".description__text, .show-more-less-html__markup, .jobs-description")
                if desc:
                    job.description = desc[:8000]
                apply_link = await _safe_attr(page, "a.jobs-apply-button, a[data-tracking-control-name='public_jobs_apply-link-offsite']")
                if apply_link:
                    job.application_url = apply_link
            except Exception:
                pass
        return jobs


class IndeedExtractor:
    name = "indeed"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        url = (
            f"https://in.indeed.com/jobs"
            f"?q={quote_plus(query)}&l={quote_plus(location)}&fromage=1&sort=date"
        )
        await session.navigate(url, wait_ms=4000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("indeed_blocked", reason=blocker)
            return jobs

        page = session.page
        cards = page.locator(".job_seen_beacon, .jobsearch-ResultsList > li, div.slider_item")
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(card, "h2.jobTitle span, a.jcs-JobTitle, .jobTitle")
                company = await _text_from(card, "[data-testid='company-name'], .companyName, .company")
                loc = await _text_from(card, "[data-testid='text-location'], .companyLocation")
                link = await _href_from(card, "a.jcs-JobTitle, h2.jobTitle a, a[href*='/viewjob']")
                date_text = await _text_from(card, "span.date, .date")

                if not title:
                    continue
                if link and not link.startswith("http"):
                    link = f"https://in.indeed.com{link}"

                posted, verified = _parse_relative_date(date_text)

                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=link.split("jk=")[-1].split("&")[0] if "jk=" in link else link,
                    title=title,
                    company=company or "Unknown",
                    location=loc or location,
                    job_url=link or url,
                    application_url=link or url,
                    date_posted=posted,
                    date_posted_verified=verified,
                ))
            except Exception as e:
                logger.debug("indeed_card_parse_error", index=i, error=str(e))

        return jobs[:max_results]


class NaukriExtractor:
    name = "naukri"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        slug_query = quote_plus(query.replace(" ", "-").lower())
        slug_loc = quote_plus(location.replace(" ", "-").lower())
        url = f"https://www.naukri.com/{slug_query}-jobs-in-{slug_loc}?freshness=1"

        await session.navigate(url, wait_ms=4000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("naukri_blocked", reason=blocker)
            return jobs

        page = session.page
        cards = page.locator(".srp-jobtuple-wrapper, article.jobTuple")
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(card, ".title, a.title")
                company = await _text_from(card, ".comp-name, .subTitle")
                loc = await _text_from(card, ".locWdth, .location")
                link = await _href_from(card, "a.title, a[href*='job-listings']")
                date_text = await _text_from(card, ".job-post-day, .type")

                if not title:
                    continue
                if link and not link.startswith("http"):
                    link = f"https://www.naukri.com{link}"

                posted, verified = _parse_relative_date(date_text)

                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=(
                        re.search(r"-(\d{8,})(?:\?|$)", link).group(1)
                        if link and re.search(r"-(\d{8,})(?:\?|$)", link)
                        else link
                    ),
                    title=title,
                    company=company or "Unknown",
                    location=loc or location,
                    job_url=link or url,
                    application_url=link or url,
                    date_posted=posted,
                    date_posted_verified=verified,
                ))
            except Exception as e:
                logger.debug("naukri_card_parse_error", index=i, error=str(e))

        return jobs[:max_results]


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class WellfoundExtractor:
    name = "wellfound"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        role_slug = _slugify(query)
        loc_slug = _slugify(location or "india") or "india"
        # Query search keeps intent; role pages often redirect to generic location hubs.
        urls = [
            f"https://wellfound.com/jobs?q={quote_plus(query)}&l={quote_plus(location or 'India')}",
            f"https://wellfound.com/role/l/{role_slug}/{loc_slug}",
        ]

        for url in urls:
            await session.navigate(url, wait_ms=5500)
            blocker = await session.detect_blocker()
            if blocker:
                logger.warning("wellfound_blocked", reason=blocker)
                return jobs

            page = session.page
            cards = page.locator("a[href*='/jobs/']")
            count = await cards.count()
            seen: set[str] = set()

            for i in range(min(count, max_results * 4)):
                try:
                    card = cards.nth(i)
                    link = (await card.get_attribute("href")) or ""
                    if not re.search(r"/jobs/\d+", link):
                        continue
                    if any(x in link for x in ("signup", "sign_in", "login")):
                        continue
                    if not link.startswith("http"):
                        link = f"https://wellfound.com{link}"
                    if link in seen:
                        continue
                    seen.add(link)

                    title = (await card.inner_text()).strip()
                    parent = card.locator("xpath=ancestor::*[self::div or self::li or self::article][1]")
                    company = await _text_from(
                        parent,
                        "[data-test='CompanyName'], [data-test='company-name'], a[href*='/company/']",
                    )
                    if not title or len(title) < 4 or title.lower() in ("apply", "view job", "see more"):
                        title = await _text_from(parent, "h2, h3, h4, [data-test='JobTitle']") or title
                    if not title or len(title) < 4:
                        slug = link.rstrip("/").split("/")[-1]
                        title = re.sub(r"^\d+-", "", slug).replace("-", " ").title()

                    jobs.append(RawJob(
                        source=self.name,
                        source_job_id=_extract_id(link, r"/jobs/(\d+)") or link,
                        title=title[:200],
                        company=company or "Unknown",
                        location=location or "Remote",
                        job_url=link,
                        application_url=link,
                        is_remote="remote" in (location or "").lower(),
                        remote_india_eligible=True,
                    ))
                    if len(jobs) >= max_results:
                        return jobs[:max_results]
                except Exception:
                    pass

            if jobs:
                break

        return jobs[:max_results]


class InstahyreExtractor:
    name = "instahyre"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        url = (
            f"https://www.instahyre.com/search-jobs/"
            f"?q={quote_plus(query)}&location={quote_plus(location)}"
        )
        await session.navigate(url, wait_ms=5000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("instahyre_blocked", reason=blocker)
            return jobs

        page = session.page
        # Logged-out pages rarely expose cards; still try modern + legacy selectors
        cards = page.locator(
            ".opportunity-card, .job-card, .search-result-item, "
            "[class*='OpportunityCard'], a[href*='/job-']"
        )
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(card, "h2, h3, .job-title, .opportunity-name")
                company = await _text_from(card, ".company, .employer-name, .company-name")
                loc = await _text_from(card, ".location")
                link = await _href_from(card, "a[href*='/job'], a[href*='/opportunity']")

                if not title or len(title) < 5:
                    continue
                if link and not link.startswith("http"):
                    link = f"https://www.instahyre.com{link}"

                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=link or title,
                    title=title,
                    company=company or "Unknown",
                    location=loc or location,
                    job_url=link or url,
                    application_url=link or url,
                ))
            except Exception:
                pass

        return jobs[:max_results]


class CutshortExtractor:
    name = "cutshort"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        # Modern search UI (slug URLs often 404 / empty)
        url = (
            f"https://cutshort.io/profile/job-search"
            f"?search={quote_plus(query)}&location={quote_plus(location)}"
        )
        await session.navigate(url, wait_ms=5000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("cutshort_blocked", reason=blocker)
            return jobs

        page = session.page
        cards = page.locator(
            "[class*='job-card'], [class*='JobCard'], div[data-job-id], "
            "a[href*='/job/'], article"
        )
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(card, "h2, h3, .job-title, [class*='title']")
                company = await _text_from(card, ".company, [class*='company']")
                link = await _href_from(card, "a[href*='/job/']")

                if not title or len(title) < 5:
                    continue
                if link and not link.startswith("http"):
                    link = f"https://cutshort.io{link}"

                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=link or title,
                    title=title,
                    company=company or "Unknown",
                    location=location,
                    job_url=link or url,
                    application_url=link or url,
                ))
            except Exception:
                pass

        return jobs[:max_results]


class HiristExtractor:
    name = "hirist"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        # Domain moved to hirist.tech; keep full query slug
        slug = _slugify(query)
        loc_slug = _slugify(location)
        url = f"https://www.hirist.tech/j/{slug}-jobs-in-{loc_slug}?jobFreshness=7"
        await session.navigate(url, wait_ms=5000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("hirist_blocked", reason=blocker)
            return jobs

        page = session.page
        cards = page.locator(
            "[data-job], .joblist-row, .jobTuple, article[class*='job'], "
            "a[href*='/j/'], div[class*='JobCard']"
        )
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(card, "h2, h3, .job-title, .title, a[href*='/j/']")
                company = await _text_from(card, ".company, .company-name, .comp-name")
                loc = await _text_from(card, ".location, .loc")
                link = await _href_from(card, "a[href*='/j/']")
                if not title or len(title) < 4:
                    continue
                if link and not link.startswith("http"):
                    link = f"https://www.hirist.tech{link}"
                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=link or title,
                    title=title,
                    company=company or "Unknown",
                    location=loc or location,
                    job_url=link or url,
                    application_url=link or url,
                ))
            except Exception:
                pass
        return jobs[:max_results]


class IimjobsExtractor:
    name = "iimjobs"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        slug = _slugify(query)
        loc_slug = _slugify(location)
        url = f"https://www.iimjobs.com/j/{slug}-jobs-in-{loc_slug}"
        await session.navigate(url, wait_ms=4000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("iimjobs_blocked", reason=blocker)
            return jobs

        page = session.page
        cards = page.locator(".joblist-row, .jobTuple, article.mjrow, a[href*='/j/']")
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(card, "h2, .job-title, .title")
                company = await _text_from(card, ".company-name, .comp-name")
                loc = await _text_from(card, ".location")
                link = await _href_from(card, "a[href*='/j/']")
                if not title:
                    continue
                if link and not link.startswith("http"):
                    link = f"https://www.iimjobs.com{link}"
                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=link or title,
                    title=title,
                    company=company or "Unknown",
                    location=loc or location,
                    job_url=link or url,
                    application_url=link or url,
                ))
            except Exception:
                pass
        return jobs[:max_results]


class ShineExtractor:
    name = "shine"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        slug = _slugify(query)
        loc_slug = _slugify(location)
        # freshness=7 avoids empty 24h windows while still staying recent
        url = f"https://www.shine.com/job-search/{slug}-jobs-in-{loc_slug}?freshness=7"
        await session.navigate(url, wait_ms=5000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("shine_blocked", reason=blocker)
            return jobs

        page = session.page
        # Shine moved to CSS-module cards: jobCardNova_bigCard__*
        cards = page.locator(
            "[class*='jobCardNova_bigCard__'], .jdbigCard, "
            "[itemprop='itemListElement'], a[href*='shine.com/jobs/']"
        )
        count = await cards.count()
        seen: set[str] = set()

        for i in range(min(count, max_results * 2)):
            try:
                card = cards.nth(i)
                link = await _href_from(card, "a[href*='/jobs/']")
                if not link:
                    # Card itself may be the anchor
                    tag = await card.evaluate("el => el.tagName")
                    if tag == "A":
                        link = (await card.get_attribute("href")) or ""
                if not link or "/jobs/" not in link:
                    meta = card.locator("meta[itemprop='url']").first
                    if await meta.count() > 0:
                        link = (await meta.get_attribute("content")) or ""
                if not link or "/jobs/" not in link:
                    continue
                if not link.startswith("http"):
                    link = f"https://www.shine.com{link}"
                if link in seen:
                    continue
                seen.add(link)

                title = await _text_from(
                    card,
                    "h3[itemprop='name'], [class*='bigCardTopTitleHeading'], h2, h3, a[href*='/jobs/']",
                )
                company = await _text_from(
                    card,
                    "[class*='bigCardTopTitleName'], [class*='jdTruncationCompany'], .companyname",
                )
                loc = await _text_from(
                    card,
                    "[class*='jobCardNova_bigCardCenterListLoc'], [class*='Location'], .loc",
                )
                if not title or len(title) < 3:
                    # Derive from URL: /jobs/{title}/{company}/{id}
                    parts = [p for p in link.rstrip("/").split("/") if p]
                    if len(parts) >= 3:
                        title = parts[-3].replace("-", " ").title()
                        company = company or parts[-2].replace("-", " ").title()
                if not title:
                    continue

                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=_extract_id(link, r"/(\d+)/?$") or link,
                    title=title.strip(),
                    company=(company or "Unknown").strip(),
                    location=(loc or location).strip(),
                    job_url=link,
                    application_url=link,
                ))
                if len(jobs) >= max_results:
                    break
            except Exception:
                pass
        return jobs[:max_results]


class TimesJobsExtractor:
    name = "timesjobs"

    async def search(self, session: BraveSession, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        # Site migrated to a JS shell ("Cand"); try both legacy + new search paths.
        urls = [
            (
                f"https://www.timesjobs.com/candidate/job-search.html"
                f"?searchType=personalizedSearch&from=submit"
                f"&txtKeywords={quote_plus(query)}&txtLocation={quote_plus(location)}"
            ),
            (
                f"https://www.timesjobs.com/job-search"
                f"?searchType=personalizedSearch"
                f"&txtKeywords={quote_plus(query)}&txtLocation={quote_plus(location)}"
            ),
        ]

        for url in urls:
            await session.navigate(url, wait_ms=6000)

            blocker = await session.detect_blocker()
            if blocker:
                logger.warning("timesjobs_blocked", reason=blocker)
                return jobs

            page = session.page
            title_text = (await page.title()).lower()
            if "cand" in title_text and await page.locator("a[href*='job']").count() < 3:
                # Empty SPA shell — no public listings to parse
                continue

            cards = page.locator(
                "li.clearfix, .job-bx, .joblistli, article, "
                "[class*='JobCard'], a[href*='job-detail'], a[href*='/job/']"
            )
            count = min(await cards.count(), max_results)
            for i in range(count):
                try:
                    card = cards.nth(i)
                    title = await _text_from(card, "h2, h3, .job-title, a")
                    company = await _text_from(card, ".company-name, .comp-name, [class*='company']")
                    loc = await _text_from(card, ".location, [class*='location']")
                    link = await _href_from(card, "a[href*='job'], a")
                    if not title or len(title) < 4:
                        continue
                    if link and not link.startswith("http"):
                        link = f"https://www.timesjobs.com{link}"
                    jobs.append(RawJob(
                        source=self.name,
                        source_job_id=link or title,
                        title=title,
                        company=company or "Unknown",
                        location=loc or location,
                        job_url=link or url,
                        application_url=link or url,
                    ))
                except Exception:
                    pass
            if jobs:
                break
        return jobs[:max_results]


class FounditExtractor:
    name = "foundit"

    async def search(self, session, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        url = (
            f"https://www.foundit.in/srp/results"
            f"?query={quote_plus(query)}&locations={quote_plus(location)}&sort=2&freshness=7"
        )
        await session.navigate(url, wait_ms=5500)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("foundit_blocked", reason=blocker)
            return jobs

        page = session.page
        # SPA cards (visible when session cookies allow results)
        cards = page.locator(
            ".cardContainer, .jobTuple, article.jobTuple, "
            "[class*='jobCard'], a[href*='/job/']"
        )
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(
                    card, ".jobTitle, .title, h2 a, a.title, [class*='jobTitle']"
                )
                company = await _text_from(
                    card, ".companyName, .companyInfo, .company-name, .subTitle"
                )
                loc = await _text_from(card, ".loc, .location, [class*='location']")
                link = await _href_from(
                    card, "a[href*='/job/'], a[href*='job-listings'], a.title, a"
                )
                if not title or len(title) < 4:
                    continue
                if link and not link.startswith("http"):
                    link = f"https://www.foundit.in{link}"
                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=link or title,
                    title=title,
                    company=company or "Unknown",
                    location=loc or location,
                    job_url=link or url,
                    application_url=link or url,
                ))
            except Exception:
                pass
        return jobs[:max_results]


class GlassdoorExtractor:
    name = "glassdoor"

    async def search(self, session, query: str, location: str, max_results: int) -> List[RawJob]:
        jobs: List[RawJob] = []
        url = (
            f"https://www.glassdoor.co.in/Job/jobs.htm"
            f"?sc.keyword={quote_plus(query)}&locT=C&locId=293959&locKeyword={quote_plus(location)}"
            f"&fromAge=1&sortBy=date_desc"
        )
        await session.navigate(url, wait_ms=5000)

        blocker = await session.detect_blocker()
        if blocker:
            logger.warning("glassdoor_blocked", reason=blocker)
            return jobs

        page = session.page
        cards = page.locator("[data-test='jobListing'], .react-job-listing, li[data-adveid]")
        count = min(await cards.count(), max_results)

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await _text_from(card, "[data-test='job-title'], .jobLink, a.jobLink")
                company = await _text_from(card, "[data-test='employer-name'], .jobEmpolyerName")
                loc = await _text_from(card, "[data-test='emp-location'], .jobLocation")
                link = await _href_from(card, "a[data-test='job-link'], a.jobLink")
                if not title:
                    continue
                if link and not link.startswith("http"):
                    link = f"https://www.glassdoor.co.in{link}"
                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=link or title,
                    title=title,
                    company=company or "Unknown",
                    location=loc or location,
                    job_url=link or url,
                    application_url=link or url,
                ))
            except Exception:
                pass
        return jobs[:max_results]


# Browser websites. To add one: new extractor class above, then append it here,
# and add its name to BRAVE_PORTALS. See job_agent/discovery/catalog.py.
PORTAL_EXTRACTORS = [
    IndeedExtractor(),
    NaukriExtractor(),
    WellfoundExtractor(),
    InstahyreExtractor(),
    CutshortExtractor(),
    HiristExtractor(),
    IimjobsExtractor(),
    ShineExtractor(),
    TimesJobsExtractor(),
    FounditExtractor(),
    GlassdoorExtractor(),
]


async def _text_from(locator, selector: str) -> str:
    try:
        el = locator.locator(selector).first
        if await el.count() > 0:
            return (await el.inner_text()).strip()
    except Exception:
        pass
    return ""


async def _href_from(locator, selector: str) -> str:
    try:
        el = locator.locator(selector).first
        if await el.count() > 0:
            return (await el.get_attribute("href")) or ""
    except Exception:
        pass
    return ""


def _extract_id(url: str, pattern: str) -> str:
    m = re.search(pattern, url)
    return m.group(1) if m else url
