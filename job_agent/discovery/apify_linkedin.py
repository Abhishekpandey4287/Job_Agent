"""Apify LinkedIn Jobs scraper adapter (official Apify API).

Default Actor: bebity/linkedin-jobs-scraper — public listings, no LinkedIn login.
Requires APIFY_API_TOKEN. The Actor must be rented (or on free trial) in Apify Console.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from job_agent.config import get_settings
from job_agent.discovery.base import JobSourceAdapter
from job_agent.logging_config import get_logger
from job_agent.schemas import DateVerification, RawJob

logger = get_logger(__name__)

API_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR = "bebity~linkedin-jobs-scraper"
RENT_URL = "https://console.apify.com/actors/BHzefUZlZRKWxkTck"

# LinkedIn time filter codes used by the Actor
PUBLISHED_PAST_24H = "r86400"
PUBLISHED_PAST_WEEK = "r604800"

# Keep batch small — Actor accepts one title + one location per run
PRIORITY_TITLES = [
    "Android Developer",
    "Android Engineer",
    "Kotlin Developer",
    "Backend Developer",
    "Java Developer",
    "Spring Boot Developer",
]
PRIORITY_LOCATIONS = [
    "Mumbai",
    "Bengaluru",
    "Pune",
    "Hyderabad",
    "India",
]


def _redact(text: str) -> str:
    return re.sub(r"apify_api_[A-Za-z0-9]+", "apify_api_REDACTED", text or "")


class ApifyRentRequired(RuntimeError):
    """Actor free trial expired — user must rent in Apify Console."""


class ApifyLinkedInJobsAdapter(JobSourceAdapter):
    """Fetch LinkedIn job listings via Apify cloud Actor."""

    name = "apify_linkedin"

    def __init__(self):
        settings = get_settings()
        self.token = (settings.apify_api_token or "").strip()
        self.enabled = settings.apify_linkedin_enabled and bool(self.token)
        self.actor_id = (settings.apify_linkedin_actor or DEFAULT_ACTOR).replace("/", "~")
        self.published_at = settings.apify_linkedin_published_at or PUBLISHED_PAST_24H
        self.max_rows = settings.apify_linkedin_max_rows

    async def health_check(self) -> bool:
        return self.enabled

    async def search(self, query: str, location: str, max_results: int = 50) -> List[RawJob]:
        jobs, _ = await self.search_batch(
            titles=[query],
            locations=[location],
            max_per_search=max_results,
        )
        return jobs[:max_results]

    async def search_batch(
        self,
        titles: List[str],
        locations: List[str],
        max_per_search: int = 50,
    ) -> tuple[List[RawJob], List[str]]:
        """Run a few title×location Actor calls (Actor schema is singular title/location)."""
        if not self.enabled:
            return [], []

        titles = [t.strip() for t in titles if t and t.strip()]
        locations = [loc.strip() for loc in locations if loc and loc.strip()]
        if not titles or not locations:
            return [], []

        # Prefer a compact matrix so we don't burn Apify credits
        use_titles = [t for t in PRIORITY_TITLES if t in titles] or titles[:4]
        use_locations = [loc for loc in PRIORITY_LOCATIONS if loc in locations] or locations[:3]
        rows = min(max_per_search if max_per_search > 0 else self.max_rows, 25)

        logger.info(
            "apify_linkedin_start",
            actor=self.actor_id,
            titles=len(use_titles),
            locations=len(use_locations),
            rows=rows,
        )

        all_jobs: List[RawJob] = []
        errors: List[str] = []
        seen: set[str] = set()

        try:
            for title in use_titles:
                for location in use_locations:
                    if len(all_jobs) >= self.max_rows:
                        break
                    run_input = {
                        "title": title,
                        "location": location,
                        "rows": rows,
                        "publishedAt": self.published_at,
                        "proxy": {
                            "useApifyProxy": True,
                            "apifyProxyGroups": ["RESIDENTIAL"],
                        },
                    }
                    try:
                        items = await self._run_actor(run_input)
                    except ApifyRentRequired as e:
                        logger.error("apify_linkedin_not_rented", hint=RENT_URL)
                        return [], [str(e)]
                    except Exception as e:
                        errors.append(_redact(f"{title}/{location}: {e}"))
                        logger.error(
                            "apify_linkedin_run_failed",
                            title=title,
                            location=location,
                            error=_redact(str(e)),
                        )
                        continue

                    for item in items:
                        job = self._parse_item(item)
                        if not job:
                            continue
                        key = (job.job_url or job.source_job_id or "").lower()
                        if key and key in seen:
                            continue
                        if key:
                            seen.add(key)
                        all_jobs.append(job)
                if len(all_jobs) >= self.max_rows:
                    break
        except Exception as e:
            msg = _redact(f"apify_linkedin: {e}")
            logger.error("apify_linkedin_failed", error=msg)
            return all_jobs, [msg]

        logger.info("apify_linkedin_complete", raw_items=len(seen), jobs=len(all_jobs))
        return all_jobs[: self.max_rows], errors

    async def _run_actor(self, run_input: Dict[str, Any]) -> List[dict]:
        """Start Actor run, wait, return dataset items. Auth via Bearer (no token in URL)."""
        actor_path = quote(self.actor_id, safe="~")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=30.0)) as client:
            sync_url = f"{API_BASE}/acts/{actor_path}/run-sync-get-dataset-items"
            try:
                resp = await client.post(
                    sync_url,
                    headers=headers,
                    params={"format": "json"},
                    json=run_input,
                    timeout=httpx.Timeout(30.0, read=300.0),
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict) and "items" in data:
                        return data["items"]
                    return []
                self._raise_for_apify(resp)
                if resp.status_code not in (408, 449):
                    resp.raise_for_status()
            except httpx.ReadTimeout:
                logger.info("apify_linkedin_sync_timeout_fallback_async")
            except ApifyRentRequired:
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (408, 449):
                    raise

            start = await client.post(
                f"{API_BASE}/acts/{actor_path}/runs",
                headers=headers,
                json=run_input,
            )
            self._raise_for_apify(start)
            start.raise_for_status()
            run = start.json().get("data") or start.json()
            run_id = run.get("id")
            if not run_id:
                raise RuntimeError(f"Apify run missing id: {run}")

            dataset_id = await self._wait_for_run(client, run_id, headers)
            items_resp = await client.get(
                f"{API_BASE}/datasets/{dataset_id}/items",
                headers=headers,
                params={"format": "json", "clean": "true"},
            )
            items_resp.raise_for_status()
            data = items_resp.json()
            return data if isinstance(data, list) else []

    def _raise_for_apify(self, resp: httpx.Response) -> None:
        if resp.status_code != 403:
            return
        try:
            body = resp.json()
        except Exception:
            body = {}
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        err_type = err.get("type") or ""
        if err_type == "actor-is-not-rented" or "rent" in (err.get("message") or "").lower():
            raise ApifyRentRequired(
                "Apify LinkedIn Actor is not rented (free trial expired). "
                f"Open {RENT_URL} → Rent / Start free trial, then re-run. "
                "Or set APIFY_LINKEDIN_ENABLED=false to skip."
            )
        raise httpx.HTTPStatusError(
            f"Apify 403: {_redact(err.get('message') or resp.text[:200])}",
            request=resp.request,
            response=resp,
        )

    async def _wait_for_run(
        self, client: httpx.AsyncClient, run_id: str, headers: dict, max_wait_s: int = 360
    ) -> str:
        deadline = asyncio.get_event_loop().time() + max_wait_s
        while asyncio.get_event_loop().time() < deadline:
            resp = await client.get(f"{API_BASE}/actor-runs/{run_id}", headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data") or resp.json()
            status = data.get("status")
            if status == "SUCCEEDED":
                dataset_id = data.get("defaultDatasetId")
                if not dataset_id:
                    raise RuntimeError("Apify run succeeded but no dataset id")
                return dataset_id
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                raise RuntimeError(f"Apify run {status}: {data.get('statusMessage', '')}")
            await asyncio.sleep(5)
        raise TimeoutError(f"Apify run {run_id} did not finish within {max_wait_s}s")

    def _parse_item(self, item: dict) -> Optional[RawJob]:
        title = (item.get("title") or "").strip()
        company = (item.get("companyName") or item.get("company") or "Unknown")
        if isinstance(company, dict):
            company = company.get("name") or "Unknown"
        company = str(company).strip() or "Unknown"
        if not title:
            return None

        location = item.get("location") or ""
        if isinstance(location, dict):
            location = (
                location.get("defaultLocalizedName")
                or location.get("abbreviatedLocalizedName")
                or location.get("formatted")
                or ""
            )
        loc_parsed = item.get("locationParsed") or {}
        if not location and isinstance(loc_parsed, dict):
            location = loc_parsed.get("formatted") or loc_parsed.get("city") or ""

        job_url = item.get("jobUrl") or item.get("link") or item.get("url") or ""
        apply_url = item.get("applyUrl") or job_url
        source_id = str(item.get("id") or item.get("jobId") or job_url)

        description = item.get("description") or item.get("descriptionText") or ""
        if isinstance(description, dict):
            description = description.get("text") or str(description)

        salary = item.get("salary") or ""
        if isinstance(salary, dict):
            salary = salary.get("formattedBaseSalary") or ""
        if not salary and item.get("salaryMin"):
            currency = item.get("salaryCurrency") or ""
            period = item.get("salaryPeriod") or ""
            salary = f"{item.get('salaryMin')}-{item.get('salaryMax')} {currency} {period}".strip()

        posted, verified = self._parse_date(item)
        work_type = (item.get("workType") or "").lower()
        is_remote = "remote" in work_type or "remote" in str(location).lower()

        return RawJob(
            source=self.name,
            source_job_id=source_id,
            title=title,
            company=company,
            location=str(location) or "India",
            description=str(description)[:8000],
            job_url=job_url or apply_url,
            application_url=apply_url or job_url,
            date_posted=posted,
            date_posted_verified=verified,
            salary=str(salary) if salary else "",
            is_remote=is_remote,
            remote_india_eligible=is_remote,
            raw_data=item,
        )

    def _parse_date(self, item: dict) -> tuple[Optional[datetime], DateVerification]:
        ts = item.get("postedAtTimestamp")
        if ts:
            try:
                if ts > 10_000_000_000:
                    ts = ts / 1000.0
                return datetime.utcfromtimestamp(ts), DateVerification.VERIFIED
            except (TypeError, ValueError, OSError):
                pass

        for key in ("postedAt", "publishedAt", "postedTime"):
            val = item.get(key)
            if not val or not isinstance(val, str):
                continue
            try:
                if "T" in val:
                    return datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None), DateVerification.VERIFIED
                if len(val) >= 10 and val[4] == "-":
                    return datetime.fromisoformat(val[:10]), DateVerification.ESTIMATED
            except ValueError:
                pass
            low = val.lower()
            if "hour" in low or "just" in low or "today" in low or "minute" in low:
                return datetime.utcnow(), DateVerification.ESTIMATED

        return None, DateVerification.UNKNOWN
