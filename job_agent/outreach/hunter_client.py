"""Hunter.io API client for company people discovery (official API only).

Free plan has ~50 Domain Search credits/month. This client is intentionally
stingy: 1 call per company, monthly + per-run budgets, in-run cache.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from job_agent.config import get_settings
from job_agent.logging_config import get_logger

logger = get_logger(__name__)

BASE = "https://api.hunter.io/v2"
USAGE_PATH = Path("data/hunter_usage.json")

# Known domains for companies in recent Excel runs (public websites)
COMPANY_DOMAINS: Dict[str, str] = {
    "gitlab": "gitlab.com",
    "ust": "ust.com",
    "rentokil pci": "rentokil-initial.com",
    "taskbob": "taskbob.com",
    "aimbeat technology": "aimbeatsoftech.com",
    "isync evolution": "isyncevolution.com",
    "pagalguy": "pagalguy.com",
    "dex consulting": "dexconsulting.com",
    "4 square technology": "4squaretechnology.com",
    "myinfoline": "myinfoline.in",
    "amploy": "amploy.com",
    "cashos technologies pte ltd": "cashos.com",
    "zaptas technologies": "zaptas.com",
    "capgemini": "capgemini.com",
    "tata consultancy services": "tcs.com",
    "tcs": "tcs.com",
    "camunda": "camunda.com",
    "postman": "postman.com",
    "groww": "groww.in",
    "freshworks": "freshworks.com",
    "meesho": "meesho.com",
    "cred": "cred.club",
}

# Staffing/agency names rarely have useful Domain Search hits — skip to save credits
AGENCY_HINTS = (
    "talent", "staffing", "recruit", "consulting", "solutions", "infosystem",
    "info system", "infotech", "softtech", "technologies pte", "data solutions",
)


ENGINEER_KEYWORDS = [
    "android", "mobile", "kotlin", "ios", "backend", "java", "spring",
    "software engineer", "engineer", "developer", "tech lead", "staff",
    "principal", "sde", "full stack", "fullstack",
]
MANAGER_KEYWORDS = [
    "engineering manager", "eng manager", "director of engineering",
    "head of engineering", "technical lead", "team lead", "tech lead",
    "vp engineering", "cto",
]
RECRUITER_KEYWORDS = [
    "recruiter", "talent acquisition", "talent partner", "talent",
    "hiring", "sourcer", "people partner", "hr ",
]


def _score_person(position: str, role_category: str) -> int:
    """Lower is better. Prefer eng IC > eng manager > recruiter > other."""
    t = (position or "").lower()
    role = (role_category or "").upper()
    if any(k in t for k in RECRUITER_KEYWORDS):
        base = 30
    elif any(k in t for k in MANAGER_KEYWORDS):
        base = 20
    elif any(k in t for k in ENGINEER_KEYWORDS):
        base = 10
    else:
        base = 40

    if "ANDROID" in role or "MOBILE" in role:
        if any(k in t for k in ["android", "mobile", "kotlin", "ios"]):
            base -= 5
    if "BACKEND" in role:
        if any(k in t for k in ["backend", "java", "spring", "ruby", "golang", "go "]):
            base -= 5
    return base


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load_monthly_usage() -> int:
    """Searches already spent this calendar month (local tracker)."""
    if not USAGE_PATH.exists():
        return 0
    try:
        data = json.loads(USAGE_PATH.read_text())
        if data.get("month") != _month_key():
            return 0
        return int(data.get("searches") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def save_monthly_usage(searches: int) -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_PATH.write_text(
        json.dumps({"month": _month_key(), "searches": int(searches)}, indent=2) + "\n"
    )


class HunterClient:
    def __init__(self, api_key: str | None = None):
        settings = get_settings()
        self.api_key = (api_key if api_key is not None else settings.hunter_api_key).strip()
        self.monthly_budget = max(0, int(settings.hunter_monthly_budget))
        self.max_per_run = max(0, int(settings.hunter_max_searches_per_run))
        self.max_calls_per_company = max(1, int(settings.hunter_max_calls_per_company))
        self.only_when_needed = bool(settings.hunter_only_when_needed)
        self.require_known_domain = bool(settings.hunter_require_known_domain)

        self._month_used = load_monthly_usage()
        self._run_used = 0
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._stopped_for_budget = False
        self._synced = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def sync_usage_from_hunter(self) -> Optional[dict]:
        """Pull real monthly search usage from Hunter /account (does not cost a credit)."""
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(f"{BASE}/account", params={"api_key": self.api_key})
            if resp.status_code != 200:
                logger.warning("hunter_account_sync_failed", status=resp.status_code)
                return None
            data = resp.json().get("data") or {}
            searches = (data.get("requests") or {}).get("searches") or {}
            used = int(searches.get("used") or 0)
            available = int(searches.get("available") or 50)
            # Prefer Hunter's truth when higher than local (tests / other tools)
            self._month_used = max(self._month_used, used)
            save_monthly_usage(self._month_used)
            self._synced = True
            info = {
                "used": used,
                "available": available,
                "remaining": max(0, available - used),
                "tracked": self._month_used,
            }
            logger.info("hunter_usage_synced", **info)
            return info
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            logger.warning("hunter_account_sync_error", error=str(e) or type(e).__name__)
            return None

    @property
    def remaining_month(self) -> int:
        return max(0, self.monthly_budget - self._month_used)

    @property
    def remaining_run(self) -> int:
        return max(0, self.max_per_run - self._run_used)

    @property
    def remaining(self) -> int:
        return min(self.remaining_month, self.remaining_run)

    def budget_summary(self) -> str:
        return (
            f"month {self._month_used}/{self.monthly_budget} used, "
            f"run cap {self.max_per_run}, remaining this run ≈ {self.remaining}"
        )

    def can_search(self) -> bool:
        if not self.enabled or self._stopped_for_budget:
            return False
        return self.remaining > 0

    async def find_referral_candidates(
        self,
        company: str,
        role_category: str,
        job_title: str,
        max_people: int = 5,
        curated_count: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return people dicts. Uses at most 1 Domain Search credit per company."""
        if not self.enabled:
            return []

        if self.only_when_needed and curated_count >= 3:
            logger.info("hunter_skip_curated_enough", company=company, curated=curated_count)
            return []

        cache_key = company.strip().lower()
        if cache_key in self._cache:
            return self._cache[cache_key][:max_people]

        domain = self.resolve_domain(company)
        if self.require_known_domain and not domain:
            logger.info("hunter_skip_no_domain", company=company)
            return []
        if self._looks_like_agency(company) and not domain:
            logger.info("hunter_skip_agency", company=company)
            return []

        if not self.can_search():
            self._stopped_for_budget = True
            logger.warning(
                "hunter_budget_exhausted",
                month_used=self._month_used,
                monthly_budget=self.monthly_budget,
                run_used=self._run_used,
            )
            return []

        # Free-tier: ONE Domain Search per company (1 credit)
        query = self._best_single_query(role_category, job_title)

        async with httpx.AsyncClient(timeout=30.0) as client:
            people = await self._domain_search(client, company, domain, query)

        # Dedupe by email or name
        seen: set[str] = set()
        unique: List[Dict[str, Any]] = []
        for p in people:
            key = (p.get("email") or p.get("name") or "").lower()
            if not key or key in seen:
                continue
            if not p.get("name") or p["name"].lower() in ("", "none none"):
                continue
            seen.add(key)
            p["relevance_score"] = _score_person(p.get("position", ""), role_category)
            unique.append(p)

        unique.sort(key=lambda x: (x["relevance_score"], -int(x.get("confidence") or 0)))
        filtered = [p for p in unique if p["relevance_score"] < 40]
        selected = (filtered or unique)[:max_people]
        self._cache[cache_key] = selected
        logger.info(
            "hunter_candidates",
            company=company,
            found=len(selected),
            domain=domain or "company-name",
            credits_this_run=self._run_used,
            credits_month=self._month_used,
        )
        return selected

    @staticmethod
    def resolve_domain(company: str) -> Optional[str]:
        key = (company or "").strip().lower()
        if key in COMPANY_DOMAINS:
            return COMPANY_DOMAINS[key]
        for known, domain in COMPANY_DOMAINS.items():
            if known in key or key in known:
                return domain
        return None

    @staticmethod
    def _looks_like_agency(company: str) -> bool:
        t = (company or "").lower()
        return any(h in t for h in AGENCY_HINTS)

    def _best_single_query(self, role_category: str, job_title: str) -> Dict[str, Any]:
        """Pick one high-yield Domain Search filter (costs 1 credit)."""
        role = (role_category or "").upper()
        if "ANDROID" in role or "MOBILE" in role:
            return {"job_titles": "Android Engineer,Mobile Engineer,Software Engineer", "limit": 10}
        if "BACKEND" in role:
            return {"job_titles": "Backend Engineer,Software Engineer,Engineering Manager", "limit": 10}
        title = (job_title or "").split(",")[0].strip()[:60]
        if title:
            return {"job_titles": title, "limit": 10}
        return {"department": "it", "limit": 10}

    def _consume_credit(self) -> None:
        self._run_used += 1
        self._month_used += 1
        save_monthly_usage(self._month_used)

    async def _domain_search(
        self,
        client: httpx.AsyncClient,
        company: str,
        domain: Optional[str],
        extra: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not self.can_search():
            return []

        params: Dict[str, Any] = {
            "api_key": self.api_key,
            "type": "personal",
            "required_field": "full_name,position",
            **extra,
        }
        if domain:
            params["domain"] = domain
        else:
            params["company"] = company

        try:
            resp = await client.get(f"{BASE}/domain-search", params=params)
            # Count against budget once we hit the API (success or empty result)
            if resp.status_code in (200, 400):
                self._consume_credit()
            if resp.status_code == 401:
                logger.error("hunter_unauthorized")
                return []
            if resp.status_code == 429:
                logger.warning("hunter_rate_limited")
                self._stopped_for_budget = True
                return []
            if resp.status_code != 200:
                logger.warning("hunter_bad_status", status=resp.status_code, body=resp.text[:200])
                return []
            data = resp.json().get("data") or {}
            emails = data.get("emails") or []
        except httpx.HTTPError as e:
            logger.warning("hunter_request_failed", error=str(e) or type(e).__name__)
            return []

        out = []
        for item in emails:
            first = (item.get("first_name") or "").strip()
            last = (item.get("last_name") or "").strip()
            name = f"{first} {last}".strip()
            linkedin = item.get("linkedin") or item.get("linkedin_url") or ""
            if linkedin and not str(linkedin).startswith("http"):
                linkedin = f"https://www.linkedin.com/in/{linkedin}"
            out.append({
                "name": name,
                "first_name": first,
                "last_name": last,
                "position": item.get("position") or item.get("position_raw") or "",
                "email": item.get("value") or "",
                "linkedin_url": linkedin or "",
                "confidence": item.get("confidence") or 0,
                "department": item.get("department") or "",
                "seniority": item.get("seniority") or "",
                "source": "hunter",
            })
        return out
