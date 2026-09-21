"""Enrich job descriptions by opening detail pages in Brave (read-only).

Used before resume personalization — Naukri/Indeed search cards often have no JD body.
Never follows Naukri's "report fake recruiter" / impostor warning links.
"""

from __future__ import annotations

from typing import Sequence

from job_agent.discovery.browser.brave_session import BraveSession
from job_agent.logging_config import get_logger
from job_agent.schemas import RawJob

logger = get_logger(__name__)

# Portal-specific description selectors (visible JD body)
DESC_SELECTORS: dict[str, str] = {
    "naukri": (
        ".styles_JDC__dang-inner-html, .dang-inner-html, .job-desc, "
        "#jobDescription, [class*='job-desc'], [class*='JobDescription'], "
        "section.job-desc, .styles_job-desc-container"
    ),
    "indeed": "#jobDescriptionText, .jobsearch-JobComponent-description, .jobsearch-jobDescriptionText",
    "wellfound": "[data-test='JobDescription'], .job-description, article",
    "instahyre": ".job-description, .description, #job-description",
    "cutshort": ".job-description, [class*='description']",
    "hirist": ".job-description, .description, .jd-container",
    "iimjobs": ".job-description, .description",
    "shine": ".job-description, .jobdescription, #jobDescription",
    "timesjobs": ".job-description, .jd-desc, .desc",
    "foundit": ".job-description, .jd-desc, #jobDescription",
    "glassdoor": "[data-test='description'], .jobDescriptionContent, #JobDescriptionContainer",
    "linkedin": ".description__text, .show-more-less-html__markup, .jobs-description",
    "apify_linkedin": ".description__text, .jobs-description",
}

DEFAULT_SELECTORS = (
    "[class*='job-desc'], [class*='JobDescription'], [id*='jobDescription'], "
    ".description, article, main"
)

MIN_USEFUL_DESC = 80

# Naukri safety / report pages — not job descriptions
_BLOCKED_URL_MARKERS = (
    "/imposter/",
    "report-fake-job",
    "report-fake-recruiter",
    "jd_warning",
)


def _is_blocked_url(url: str) -> bool:
    low = (url or "").lower()
    return any(m in low for m in _BLOCKED_URL_MARKERS)


async def enrich_job_descriptions(
    jobs: Sequence[RawJob],
    *,
    max_jobs: int = 30,
) -> int:
    """Open each job URL and fill `description` when missing/short. Returns count enriched."""
    targets = [
        j for j in jobs
        if j.job_url
        and not _is_blocked_url(j.job_url)
        and len((j.description or "").strip()) < MIN_USEFUL_DESC
    ][:max_jobs]
    if not targets:
        return 0

    enriched = 0
    try:
        async with BraveSession() as session:
            for job in targets:
                try:
                    ok = await _enrich_one(session, job)
                    if ok:
                        enriched += 1
                except Exception as e:
                    logger.warning(
                        "jd_enrich_failed",
                        company=job.company,
                        source=job.source,
                        error=str(e) or type(e).__name__,
                    )
    except Exception as e:
        logger.error("jd_enrich_session_failed", error=str(e) or type(e).__name__)
        return enriched

    logger.info("jd_enrich_complete", attempted=len(targets), enriched=enriched)
    return enriched


async def _enrich_one(session: BraveSession, job: RawJob) -> bool:
    await session.navigate(job.job_url, wait_ms=3500)

    page = session.page
    current = page.url or ""
    if _is_blocked_url(current):
        logger.info(
            "jd_enrich_skipped_imposter_page",
            company=job.company,
            url=current[:120],
        )
        return False

    # Don't wait for human here — enrichment is batch; skip if real wall
    blocker = await session.detect_blocker(wait_for_human=False)
    if blocker:
        logger.info("jd_enrich_blocked", source=job.source, reason=blocker)
        return False

    # Expand "read more" only inside JD containers — never click report/fake links
    await _safe_expand_description(page)

    # If a bad click somehow navigated away, abort
    if _is_blocked_url(page.url or ""):
        logger.info(
            "jd_enrich_aborted_after_navigation",
            company=job.company,
            url=(page.url or "")[:120],
        )
        try:
            await session.navigate(job.job_url, wait_ms=2000)
        except Exception:
            pass
        return False

    selectors = DESC_SELECTORS.get((job.source or "").lower(), DEFAULT_SELECTORS)
    desc = await _visible_text(page, selectors)
    if not desc or len(desc.strip()) < MIN_USEFUL_DESC:
        # Fallback: largest text block in main (exclude warning chrome)
        desc = await page.evaluate(
            """() => {
              const bad = /imposter|report.?fake|fake.?recruiter|jd_warning/i;
              const roots = [document.querySelector('main'), document.querySelector('article'), document.body];
              let best = '';
              for (const root of roots) {
                if (!root) continue;
                const t = (root.innerText || '').trim();
                if (bad.test(t) && t.length < 500) continue;
                if (t.length > best.length) best = t;
              }
              return best.slice(0, 8000);
            }"""
        )

    if not desc or len(desc.strip()) < MIN_USEFUL_DESC:
        return False

    cleaned = _clean_desc(desc)
    if len(cleaned) < MIN_USEFUL_DESC:
        return False
    job.description = cleaned[:8000]
    return True


async def _safe_expand_description(page) -> None:
    """Click Read more / Show more only in JD areas; never report/imposter links."""
    jd_roots = (
        "[class*='job-desc'], [class*='JobDescription'], #jobDescription, "
        ".dang-inner-html, .styles_JDC__dang-inner-html, main, article"
    )
    expand_sels = (
        "button:has-text('Read more')",
        "button:has-text('Show more')",
        "a:has-text('Read more')",
        "a:has-text('Show more')",
        ".read-more",
        "[class*='read-more']",
    )
    for root_sel in jd_roots.split(","):
        root_sel = root_sel.strip()
        if not root_sel:
            continue
        try:
            root = page.locator(root_sel).first
            if await root.count() == 0:
                continue
            for sel in expand_sels:
                try:
                    btn = root.locator(sel).first
                    if await btn.count() == 0 or not await btn.is_visible():
                        continue
                    href = ""
                    try:
                        href = (await btn.get_attribute("href")) or ""
                    except Exception:
                        pass
                    if _is_blocked_url(href):
                        continue
                    label = ((await btn.inner_text(timeout=500)) or "").lower()
                    if any(x in label for x in ("report", "fake", "imposter", "fraud")):
                        continue
                    await btn.click(timeout=2000, no_wait_after=True)
                    await page.wait_for_timeout(800)
                    return
                except Exception:
                    continue
        except Exception:
            continue


async def _visible_text(page, selectors: str) -> str:
    for part in selectors.split(","):
        sel = part.strip()
        if not sel:
            continue
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            text = (await loc.inner_text(timeout=3000) or "").strip()
            if len(text) >= MIN_USEFUL_DESC:
                return text
        except Exception:
            continue
    return ""


def _clean_desc(text: str) -> str:
    lines = []
    skip_prefixes = (
        "login", "register", "cookie", "privacy policy", "terms of",
        "similar jobs", "jobs you may", "report this", "report fake",
        "beware of fake", "fake recruiter",
    )
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if any(low.startswith(p) for p in skip_prefixes):
            continue
        if "imposter" in low or "report-fake" in low:
            continue
        lines.append(s)
    return "\n".join(lines)
