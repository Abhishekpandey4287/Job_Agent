"""Public-web referral profile research (no LinkedIn login / CDP scraping).

Finds publicly indexed LinkedIn profile URLs via search engines and
company/public pages. Does not bypass auth, CAPTCHA, or rate limits.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from job_agent.logging_config import get_logger

logger = get_logger(__name__)

MAX_NOTE_CHARS = 250
MAX_PROFILES_PER_COMPANY = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

ROLE_SEARCH_TERMS: Dict[str, List[str]] = {
    "ANDROID": [
        "Android Engineer",
        "Senior Android Developer",
        "Mobile Engineer",
        "Engineering Manager Android",
        "Technical Recruiter Android",
    ],
    "BACKEND": [
        "Backend Engineer",
        "Java Developer",
        "Staff Backend Engineer",
        "Engineering Manager",
        "Technical Recruiter",
    ],
    "HYBRID": [
        "Full Stack Engineer",
        "Software Engineer",
        "Engineering Manager",
        "Technical Recruiter",
    ],
    "DEFAULT": [
        "Software Engineer",
        "Engineering Manager",
        "Technical Recruiter",
    ],
}

ENGINEER_HINTS = [
    "android", "mobile", "kotlin", "ios", "backend", "java", "spring",
    "software engineer", "engineer", "developer", "tech lead", "staff",
    "principal", "sde", "full stack", "fullstack",
]
MANAGER_HINTS = [
    "engineering manager", "eng manager", "director of engineering",
    "head of engineering", "technical lead", "team lead", "tech lead",
    "vp engineering", "cto",
]
RECRUITER_HINTS = [
    "recruiter", "talent acquisition", "talent partner", "talent",
    "hiring", "sourcer", "people partner",
]
FORMER_HINTS = ["former", "ex-", "previously at", "past experience"]


@dataclass
class TargetJob:
    company: str
    role_category: str
    job_title: str
    job_url: str = ""
    location: str = ""
    match_score: float = 0.0
    job_id: str = ""


@dataclass
class ReferralProfile:
    company: str
    role_category: str
    person: str
    current_role: str
    linkedin_url: str
    why: str
    message: str
    characters: int
    priority: str = ""
    job_title: str = ""
    verified: bool = False
    email: str = ""
    source: str = "public_web"


@dataclass
class CompanyResearchResult:
    company: str
    role_category: str
    job_title: str
    profiles: List[ReferralProfile] = field(default_factory=list)
    notes: str = ""
    job_id: str = ""
    job_url: str = ""


def _role_key(role_category: str) -> str:
    r = (role_category or "").upper()
    if "ANDROID" in r or "MOBILE" in r:
        return "ANDROID"
    if "BACKEND" in r or "JAVA" in r:
        return "BACKEND"
    if "HYBRID" in r or "FULL" in r:
        return "HYBRID"
    return "DEFAULT"


def _priority_for_title(title: str) -> Tuple[int, str]:
    t = title.lower()
    if any(h in t for h in RECRUITER_HINTS):
        return 3, "Recruiter"
    if any(h in t for h in MANAGER_HINTS):
        return 2, "Hiring/Engineering Leadership"
    if any(h in t for h in ENGINEER_HINTS):
        return 1, "Potential Referrer"
    return 4, "Other"


def _looks_former(text: str) -> bool:
    return any(h in text.lower() for h in FORMER_HINTS)


def _clean_name(name: str) -> str:
    name = re.sub(r"\s*\|.*$", "", name)
    name = re.sub(r"\s*[-–—].*$", "", name)
    name = re.sub(r"\s+on LinkedIn.*$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    # Drop generic titles that leaked into name
    if len(name) > 60 or "linkedin" in name.lower():
        return ""
    return name


def _extract_linkedin_url(href: str) -> Optional[str]:
    if not href:
        return None
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if "uddg" in qs:
            href = unquote(qs["uddg"][0])
    if href.startswith("//"):
        href = "https:" + href
    if "linkedin.com/in/" not in href.lower():
        return None
    # Normalize
    m = re.search(r"(https?://(?:[a-z]+\.)?linkedin\.com/in/[A-Za-z0-9_%\-]+)", href, re.I)
    if not m:
        return None
    return m.group(1).rstrip("/")


def _parse_snippet_person(title: str, snippet: str, company: str) -> Tuple[str, str]:
    """Best-effort name + role from public search snippet."""
    combined = f"{title} {snippet}"
    name = ""
    # Common pattern: "Name - Title at Company | LinkedIn"
    m = re.match(
        r"^([A-Za-z][A-Za-z.'\-]+(?:\s+[A-Za-z][A-Za-z.'\-]+){0,3})\s*[-–—|]\s*(.+)$",
        title.strip(),
    )
    if m:
        name = _clean_name(m.group(1))
        role_part = m.group(2)
        role_part = re.split(r"\bat\b|\||LinkedIn", role_part, maxsplit=1, flags=re.I)[0].strip()
        return name, role_part[:120]

    # "Name | LinkedIn"
    m2 = re.match(r"^([A-Za-z][A-Za-z.'\-]+(?:\s+[A-Za-z][A-Za-z.'\-]+){0,3})\s*\|", title)
    if m2:
        name = _clean_name(m2.group(1))

    role = ""
    for hint_list in (ENGINEER_HINTS, MANAGER_HINTS, RECRUITER_HINTS):
        for h in hint_list:
            if h in combined.lower():
                # Grab a short phrase around the hint
                idx = combined.lower().find(h)
                role = combined[max(0, idx - 10): idx + len(h) + 40]
                role = re.sub(r"\s+", " ", role).strip(" -|.,")
                break
        if role:
            break

    if not role:
        role = f"Professional at {company}"
    return name or "LinkedIn Profile", role[:120]


def _trim_message(msg: str) -> str:
    msg = " ".join(msg.split())
    if len(msg) <= MAX_NOTE_CHARS:
        return msg
    cut = msg[: MAX_NOTE_CHARS - 1].rsplit(" ", 1)[0]
    return cut.rstrip(".,;") + "."


def build_referral_message(
    person_name: str,
    current_role: str,
    company: str,
    job_title: str,
    role_category: str,
    priority_label: str,
) -> str:
    first = person_name.split()[0] if person_name and person_name != "LinkedIn Profile" else "there"
    role_key = _role_key(role_category)
    job_short = job_title
    if len(job_short) > 40:
        job_short = job_short[:37] + "..."

    company_l = company.lower()
    if role_key == "ANDROID":
        skill_bit = "Kotlin & Jetpack Compose"
        exp_bit = "Android developer with 2+ yrs"
    elif "gitlab" in company_l:
        # GitLab backend roles are typically Ruby/Go — don't claim Java/Spring
        skill_bit = "backend systems & APIs"
        exp_bit = "backend-focused engineer with 2+ yrs"
    elif role_key == "BACKEND":
        skill_bit = "Java & Spring Boot"
        exp_bit = "backend engineer with 2+ yrs"
    else:
        skill_bit = "Kotlin, Java & Spring Boot"
        exp_bit = "mobile/backend engineer with 2+ yrs"

    role_l = current_role.lower()
    variants = []

    if priority_label.startswith("Potential") or any(h in role_l for h in ENGINEER_HINTS):
        variants.append(
            f"Hi {first}, I’m an {exp_bit} in {skill_bit}. Applying for {job_short} at {company}. "
            f"Would you be open to referring me if my profile fits? Thanks!"
        )
        variants.append(
            f"Hi {first}, saw you’re on the eng team at {company}. I’m applying for {job_short} "
            f"({skill_bit}). Open to a referral if it seems like a fit? Thanks!"
        )
    elif priority_label.startswith("Hiring"):
        variants.append(
            f"Hi {first}, I’m an {exp_bit} focused on {skill_bit}. Interested in the {job_short} "
            f"role at {company}. Would you consider referring me? Thanks!"
        )
    else:
        variants.append(
            f"Hi {first}, I’m an {exp_bit} ({skill_bit}) applying for {job_short} at {company}. "
            f"Would you be open to referring me or pointing me to the right person? Thanks!"
        )

    # Pick variant by stable hash of person+company for diversity
    idx = abs(hash(f"{person_name}|{company}|{current_role}")) % len(variants)
    return _trim_message(variants[idx])


def build_why(current_role: str, company: str, job_title: str, priority_label: str) -> str:
    if priority_label.startswith("Potential"):
        return (
            f"{current_role}. Likely on a team relevant to the {job_title} opening "
            f"and a strong internal referral target."
        )
    if priority_label.startswith("Hiring"):
        return (
            f"{current_role}. Engineering leadership who may influence hiring for "
            f"roles like {job_title}."
        )
    if priority_label == "Recruiter":
        return (
            f"{current_role}. Involved in hiring and can route or support a referral "
            f"for {job_title}."
        )
    return f"Publicly listed professional related to {company}; may be relevant to {job_title}."


class PublicWebReferralResearcher:
    """Research referral candidates via public search engines only."""

    def __init__(self, delay_s: float = 1.8):
        self.delay_s = delay_s
        self._seen_urls: set[str] = set()
        self._consecutive_search_fails = 0

    async def research_companies(self, jobs: List[TargetJob]) -> List[CompanyResearchResult]:
        # Deduplicate by company (keep highest match / first role)
        by_company: Dict[str, TargetJob] = {}
        for job in jobs:
            key = job.company.strip().lower()
            if key not in by_company:
                by_company[key] = job
            else:
                existing = by_company[key]
                if job.match_score > existing.match_score:
                    by_company[key] = job

        results: List[CompanyResearchResult] = []
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=HEADERS) as client:
            for job in by_company.values():
                logger.info("referral_research_start", company=job.company, role=job.role_category)
                result = await self._research_one(client, job)
                results.append(result)
                await asyncio.sleep(self.delay_s)
        return results

    async def _research_one(
        self, client: httpx.AsyncClient, job: TargetJob
    ) -> CompanyResearchResult:
        role_key = _role_key(job.role_category)
        terms = ROLE_SEARCH_TERMS.get(role_key, ROLE_SEARCH_TERMS["DEFAULT"])
        candidates: List[ReferralProfile] = []
        seen_local: set[str] = set()

        queries = [
            f'site:linkedin.com/in "{job.company}" "{term}"'
            for term in terms[:4]
        ]
        queries.append(f'site:linkedin.com/in "{job.company}" "{job.job_title.split(",")[0][:40]}"')
        queries.append(f'"{job.company}" Android Engineer LinkedIn')
        if role_key == "BACKEND":
            queries.append(f'"{job.company}" Backend Engineer LinkedIn')

        for q in queries:
            if len(candidates) >= MAX_PROFILES_PER_COMPANY:
                break
            if self._consecutive_search_fails >= 5:
                logger.warning("referral_search_circuit_breaker")
                break

            hits = await self._search(client, q)
            await asyncio.sleep(self.delay_s)

            for hit in hits:
                url = hit["url"]
                url_key = url.lower().rstrip("/")
                if url_key in seen_local or url_key in self._seen_urls:
                    continue

                title = hit.get("title", "")
                snippet = hit.get("snippet", "")
                blob = f"{title} {snippet} {url}".lower()

                # Soft company presence check
                company_token = job.company.lower().split()[0]
                if len(company_token) >= 4 and company_token not in blob and job.company.lower() not in blob:
                    # Still allow if LinkedIn URL found from company-scoped query
                    if f'"{job.company}"' not in q and job.company.lower() not in q.lower():
                        continue

                if _looks_former(blob):
                    continue

                name, role = _parse_snippet_person(title, snippet, job.company)
                if not name:
                    continue

                # Prefer relevant titles
                prio_rank, prio_label = _priority_for_title(f"{role} {title} {snippet}")
                if prio_rank > 3 and role_key == "ANDROID":
                    # Skip clearly unrelated if we already have some
                    if len(candidates) >= 1 and not any(
                        h in f"{role} {title}".lower() for h in ENGINEER_HINTS + MANAGER_HINTS + RECRUITER_HINTS
                    ):
                        continue

                msg = build_referral_message(
                    name, role, job.company, job.job_title, job.role_category, prio_label
                )
                why = build_why(role, job.company, job.job_title, prio_label)

                profile = ReferralProfile(
                    company=job.company,
                    role_category=job.role_category,
                    person=name,
                    current_role=role or "Role not confirmed from public snippet",
                    linkedin_url=url,
                    why=why,
                    message=msg,
                    characters=len(msg),
                    priority=prio_label,
                    job_title=job.job_title,
                    verified=company_token in blob or job.company.lower() in blob,
                )
                seen_local.add(url_key)
                self._seen_urls.add(url_key)
                candidates.append(profile)

        # Rank: priority 1 > 2 > 3, verified first
        candidates.sort(key=lambda p: (_priority_for_title(p.current_role)[0], not p.verified, p.person))
        selected = candidates[:MAX_PROFILES_PER_COMPANY]

        # Pad with explicit missing markers (as separate rows later)
        notes = ""
        if len(selected) < MAX_PROFILES_PER_COMPANY:
            notes = (
                f"Only {len(selected)} publicly verifiable profile(s) found; "
                f"did not pad with random employees."
            )

        return CompanyResearchResult(
            company=job.company,
            role_category=job.role_category,
            job_title=job.job_title,
            profiles=selected,
            notes=notes,
            job_id=job.job_id,
            job_url=job.job_url,
        )

    async def _search(self, client: httpx.AsyncClient, query: str) -> List[dict]:
        # Try DuckDuckGo HTML, then Bing HTML
        hits = await self._search_ddg(client, query)
        if hits:
            self._consecutive_search_fails = 0
            return hits
        hits = await self._search_bing(client, query)
        if hits:
            self._consecutive_search_fails = 0
            return hits
        self._consecutive_search_fails += 1
        return []

    async def _search_ddg(self, client: httpx.AsyncClient, query: str) -> List[dict]:
        try:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
            )
            if resp.status_code == 202:
                logger.warning("ddg_rate_limited", query=query[:60])
                return []
            if resp.status_code != 200:
                return []
            return self._parse_ddg(resp.text)
        except httpx.HTTPError as e:
            logger.warning("ddg_failed", error=str(e) or type(e).__name__)
            return []

    def _parse_ddg(self, html: str) -> List[dict]:
        soup = BeautifulSoup(html, "lxml")
        hits = []
        for result in soup.select(".result, .web-result, .results_links"):
            a = result.select_one("a.result__a, a.result-link, h2 a")
            if not a:
                continue
            href = a.get("href", "")
            url = _extract_linkedin_url(href)
            if not url:
                continue
            title = a.get_text(" ", strip=True)
            sn = result.select_one(".result__snippet, .result-snippet, a.result__snippet")
            snippet = sn.get_text(" ", strip=True) if sn else ""
            hits.append({"url": url, "title": title, "snippet": snippet})
        return hits

    async def _search_bing(self, client: httpx.AsyncClient, query: str) -> List[dict]:
        try:
            resp = await client.get(
                "https://www.bing.com/search",
                params={"q": query, "count": "10"},
            )
            if resp.status_code != 200:
                return []
            return self._parse_bing(resp.text)
        except httpx.HTTPError as e:
            logger.warning("bing_failed", error=str(e) or type(e).__name__)
            return []

    def _parse_bing(self, html: str) -> List[dict]:
        soup = BeautifulSoup(html, "lxml")
        hits = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if not a:
                continue
            url = _extract_linkedin_url(a.get("href", ""))
            if not url:
                continue
            title = a.get_text(" ", strip=True)
            sn = li.select_one(".b_caption p, .b_algoSlug")
            snippet = sn.get_text(" ", strip=True) if sn else ""
            hits.append({"url": url, "title": title, "snippet": snippet})
        return hits


def load_jobs_from_excel(path) -> List[TargetJob]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    headers = [str(h or "").strip() for h in rows[0]]
    jobs: List[TargetJob] = []
    for row in rows[1:]:
        d = {headers[i]: row[i] for i in range(len(headers))}
        company = str(d.get("Company") or "").strip()
        title = str(d.get("Job Title") or "").strip()
        if not company:
            continue
        score = d.get("Match Score") or 0
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            score_f = 0.0
        apply = str(d.get("Direct Application Link") or "").strip()
        if apply.lower() == "apply here":
            apply = ""
        job_id = str(d.get("Job ID") or "").strip()
        jobs.append(
            TargetJob(
                company=company,
                role_category=str(d.get("Role Category") or "Android").strip(),
                job_title=title or "Software Engineer",
                job_url=apply,
                location=str(d.get("Location") or "").strip(),
                match_score=score_f,
                job_id=job_id,
            )
        )
    wb.close()
    return jobs


def export_referral_excel(results: List[CompanyResearchResult], path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Referral Profiles"
    cols = [
        ("Company", 28),
        ("Role Category", 14),
        ("Target Job Title", 32),
        ("Job ID", 22),
        ("Job URL", 40),
        ("Person", 24),
        ("Current Role", 36),
        ("Priority", 28),
        ("LinkedIn Profile", 48),
        ("Email", 32),
        ("Why This Person", 55),
        ("Personalized Referral Request", 60),
        ("Characters", 12),
        ("Source", 12),
    ]
    fill = PatternFill("solid", fgColor="1F4E79")
    font = Font(color="FFFFFF", bold=True)
    link_font = Font(color="0563C1", underline="single")

    for i, (h, w) in enumerate(cols, 1):
        cell = ws.cell(1, i, h)
        cell.fill = fill
        cell.font = font
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    row_i = 2
    for result in results:
        if not result.profiles:
            for slot in range(MAX_PROFILES_PER_COMPANY):
                ws.cell(row_i, 1, result.company)
                ws.cell(row_i, 2, result.role_category)
                ws.cell(row_i, 3, result.job_title)
                ws.cell(row_i, 4, result.job_id or "—")
                url_cell = ws.cell(row_i, 5, result.job_url or "—")
                if result.job_url.startswith("http"):
                    url_cell.hyperlink = result.job_url
                    url_cell.font = link_font
                ws.cell(row_i, 6, "No reliable profile found")
                ws.cell(row_i, 7, "—")
                ws.cell(row_i, 8, "—")
                ws.cell(row_i, 9, "—")
                ws.cell(row_i, 10, "—")
                note = result.notes if slot == 0 else "Could not verify an additional relevant public profile."
                ws.cell(row_i, 11, note or "No publicly verifiable profiles found.")
                ws.cell(row_i, 12, "—")
                ws.cell(row_i, 13, 0)
                ws.cell(row_i, 14, "—")
                row_i += 1
            continue

        for p in result.profiles:
            ws.cell(row_i, 1, p.company)
            ws.cell(row_i, 2, p.role_category)
            ws.cell(row_i, 3, p.job_title)
            ws.cell(row_i, 4, result.job_id or "—")
            url_cell = ws.cell(row_i, 5, result.job_url or "—")
            if result.job_url.startswith("http"):
                url_cell.hyperlink = result.job_url
                url_cell.font = link_font
            ws.cell(row_i, 6, p.person)
            ws.cell(row_i, 7, p.current_role)
            ws.cell(row_i, 8, p.priority)
            link_val = p.linkedin_url or "—"
            link_cell = ws.cell(row_i, 9, link_val)
            if p.linkedin_url.startswith("http"):
                link_cell.hyperlink = p.linkedin_url
                link_cell.font = link_font
            email_cell = ws.cell(row_i, 10, p.email or "—")
            if p.email and "@" in p.email:
                email_cell.hyperlink = f"mailto:{p.email}"
                email_cell.font = link_font
            ws.cell(row_i, 11, p.why)
            ws.cell(row_i, 12, p.message)
            ws.cell(row_i, 13, p.characters)
            ws.cell(row_i, 14, p.source)
            for c in range(1, 15):
                ws.cell(row_i, c).alignment = Alignment(wrap_text=True, vertical="top")
            row_i += 1

        missing = MAX_PROFILES_PER_COMPANY - len(result.profiles)
        for _ in range(missing):
            ws.cell(row_i, 1, result.company)
            ws.cell(row_i, 2, result.role_category)
            ws.cell(row_i, 3, result.job_title)
            ws.cell(row_i, 4, result.job_id or "—")
            ws.cell(row_i, 5, result.job_url or "—")
            ws.cell(row_i, 6, "No reliable profile found")
            ws.cell(row_i, 11, "Could not verify an additional relevant public profile.")
            ws.cell(row_i, 12, "—")
            ws.cell(row_i, 13, 0)
            ws.cell(row_i, 14, "—")
            row_i += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def export_referral_markdown(results: List[CompanyResearchResult], path) -> None:
    lines = [
        "# Referral Profiles",
        "",
        "Public-web + optional Hunter.io research (no LinkedIn login scraping). "
        "Verify each profile before messaging. Messages are ≤250 characters.",
        "",
    ]
    found = sum(len(r.profiles) for r in results)
    lines.append(f"**Companies:** {len(results)}  ")
    lines.append(f"**Profiles found:** {found}  ")
    lines.append("")

    for result in results:
        lines.append(f"## {result.company}")
        lines.append(f"- **Role category:** {result.role_category}")
        lines.append(f"- **Target job:** {result.job_title}")
        if result.job_id:
            lines.append(f"- **Job ID:** `{result.job_id}`")
        if result.job_url:
            lines.append(f"- **Job URL:** {result.job_url}")
        if result.notes:
            lines.append(f"- **Note:** {result.notes}")
        lines.append("")
        if not result.profiles:
            lines.append("_No reliable profile found_")
            lines.append("")
            continue
        for i, p in enumerate(result.profiles, 1):
            lines.append(f"### {i}. {p.person}")
            lines.append(f"- **Current role:** {p.current_role}")
            lines.append(f"- **Priority:** {p.priority}")
            lines.append(f"- **Source:** {p.source}")
            if p.linkedin_url:
                lines.append(f"- **LinkedIn:** {p.linkedin_url}")
            if p.email:
                lines.append(f"- **Email:** {p.email}")
            lines.append(f"- **Why:** {p.why}")
            lines.append(f"- **Message ({p.characters} chars):**")
            lines.append(f"  > {p.message}")
            lines.append("")
        missing = MAX_PROFILES_PER_COMPANY - len(result.profiles)
        for _ in range(missing):
            lines.append("- _No reliable profile found_")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
