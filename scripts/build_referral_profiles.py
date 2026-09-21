#!/usr/bin/env python3
"""Build referral_profiles.xlsx/md from Excel + public web + optional Hunter.io.

Uses Hunter.io official API when HUNTER_API_KEY is set in .env.
Does NOT scrape LinkedIn via CDP / cookies.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

from job_agent.config import get_settings
from job_agent.outreach.hunter_client import HunterClient
from job_agent.outreach.referral_research import (
    CompanyResearchResult,
    ReferralProfile,
    TargetJob,
    build_referral_message,
    build_why,
    export_referral_excel,
    export_referral_markdown,
    load_jobs_from_excel,
    _priority_for_title,
)

# Public-web verified seed profiles (current roles).
CURATED: dict[str, list[dict]] = {
    "gitlab": [
        {
            "person": "Tiffany Rea",
            "current_role": "Sr Backend Engineer - Developer Experience @ GitLab",
            "linkedin_url": "https://www.linkedin.com/in/tiffanyrea",
            "source": "public_web",
        },
        {
            "person": "Shinya Maeda",
            "current_role": "Staff Backend Engineer @ GitLab",
            "linkedin_url": "https://www.linkedin.com/in/shinya-maeda-830467129",
            "source": "public_web",
        },
        {
            "person": "Matthew Nohr",
            "current_role": "Backend Engineering Manager (Code Review) @ GitLab",
            "linkedin_url": "https://www.linkedin.com/in/matthewnohr",
            "source": "public_web",
        },
    ],
    "ust": [
        {
            "person": "Narendra Singh",
            "current_role": "Android Engineer @ UST (Bengaluru)",
            "linkedin_url": "https://www.linkedin.com/in/narendra-singh-87b6aa109",
            "source": "public_web",
        },
        {
            "person": "Jayakrishnan Krishnapillai",
            "current_role": "Lead Android Developer @ UST (Kochi)",
            "linkedin_url": "https://www.linkedin.com/in/jayakrishnan-krishnapillai-828719250",
            "source": "public_web",
        },
        {
            "person": "Nimith Thomas",
            "current_role": "Lead Android Engineer @ UST",
            "linkedin_url": "https://www.linkedin.com/in/nimithth",
            "source": "public_web",
        },
    ],
    "rentokil pci": [
        {
            "person": "KRISHNA NEEHAR REDDY GUNTURU",
            "current_role": "Mobile Engineer @ Rentokil Initial",
            "linkedin_url": "https://www.linkedin.com/in/krishna-neehar-reddy-gunturu-94b30453",
            "source": "public_web",
        },
    ],
    "aimbeat technology": [
        {
            "person": "Uzma Ansari",
            "current_role": "Senior Android Developer @ Aimbeat Softech Pvt Ltd",
            "linkedin_url": "https://www.linkedin.com/in/uzma-ansari-677854196",
            "source": "public_web",
        },
    ],
    "origin info system": [
        {
            "person": "Amarendra Maharana",
            "current_role": "Android Developer @ Origin Info System",
            "linkedin_url": "https://www.linkedin.com/in/amarendra-maharana-51962425b",
            "source": "public_web",
        },
    ],
    "isync evolution": [
        {
            "person": "Alakh Pandya",
            "current_role": "Senior Software Developer @ iSyncEvolution",
            "linkedin_url": "https://www.linkedin.com/in/alakhpandya",
            "source": "public_web",
        },
    ],
}


def company_key(name: str) -> str:
    return name.strip().lower()


def profile_from_dict(job: TargetJob, c: dict) -> ReferralProfile:
    role = c.get("current_role") or c.get("position") or ""
    person = c.get("person") or c.get("name") or ""
    _, prio = _priority_for_title(role)
    msg = build_referral_message(
        person, role, job.company, job.job_title, job.role_category, prio
    )
    assert len(msg) <= 250, f"Message too long ({len(msg)}): {msg}"
    linkedin = c.get("linkedin_url") or ""
    email = c.get("email") or ""
    why = build_why(role, job.company, job.job_title, prio)
    if email and not linkedin:
        why += f" Hunter email available ({email}); confirm LinkedIn profile before messaging."
    return ReferralProfile(
        company=job.company,
        role_category=job.role_category,
        person=person,
        current_role=role or "Role not confirmed",
        linkedin_url=linkedin,
        why=why,
        message=msg,
        characters=len(msg),
        priority=prio,
        job_title=job.job_title,
        verified=bool(linkedin or email),
        email=email,
        source=c.get("source") or "public_web",
    )


def merge_candidates(job: TargetJob, curated: list[dict], hunter: list[dict]) -> list[ReferralProfile]:
    combined: list[dict] = []
    seen_names: set[str] = set()

    for c in curated:
        name = (c.get("person") or "").strip().lower()
        if name and name not in seen_names:
            seen_names.add(name)
            combined.append(c)

    for h in hunter:
        name = (h.get("name") or "").strip().lower()
        if not name or name in seen_names:
            # enrich existing with email/linkedin if same person
            for c in combined:
                if (c.get("person") or "").strip().lower() == name:
                    if h.get("email") and not c.get("email"):
                        c["email"] = h["email"]
                    if h.get("linkedin_url") and not c.get("linkedin_url"):
                        c["linkedin_url"] = h["linkedin_url"]
            continue
        seen_names.add(name)
        combined.append({
            "person": h["name"],
            "current_role": h.get("position") or "",
            "linkedin_url": h.get("linkedin_url") or "",
            "email": h.get("email") or "",
            "source": "hunter",
            "relevance_score": h.get("relevance_score", 40),
        })

    # Prefer curated order, then hunter by relevance
    curated_names = {(c.get("person") or "").lower() for c in curated}
    curated_part = [c for c in combined if (c.get("person") or "").lower() in curated_names]
    hunter_part = [c for c in combined if (c.get("person") or "").lower() not in curated_names]
    hunter_part.sort(key=lambda x: x.get("relevance_score", 40))

    ordered = curated_part + hunter_part
    profiles = []
    for c in ordered:
        # Skip Hunter people with no useful title signal if we already have 3 curated
        if c.get("source") == "hunter" and not (c.get("current_role") or "").strip():
            continue
        profiles.append(profile_from_dict(job, c))
        if len(profiles) >= 3:
            break
    return profiles


async def main():
    settings = get_settings()
    out = PROJECT_ROOT / "output"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    day = out / today
    excel = day / f"android_backend_jobs_{today}.xlsx"
    if not excel.exists():
        matches = sorted(out.glob("*/android_backend_jobs_*.xlsx"), reverse=True)
        if not matches:
            raise SystemExit("No Excel found")
        excel = matches[0]
        day = excel.parent

    jobs = load_jobs_from_excel(excel)
    by_company: dict[str, TargetJob] = {}
    for j in jobs:
        k = company_key(j.company)
        if k not in by_company or j.match_score > by_company[k].match_score:
            by_company[k] = j

    hunter = HunterClient()
    if hunter.enabled and settings.hunter_enabled:
        sync = await hunter.sync_usage_from_hunter()
        if sync:
            print(
                f"Hunter.io: ENABLED — plan used {sync['used']}/{sync['available']} "
                f"(~{sync['remaining']} left this month)"
            )
        print(f"  Local budget: {hunter.budget_summary()}")
    else:
        print("Hunter.io: DISABLED — add HUNTER_API_KEY to .env to enrich contacts")
        print("  Get key: https://hunter.io/api-keys")

    companies = sorted(
        by_company.items(),
        key=lambda kv: (
            0 if HunterClient.resolve_domain(kv[1].company) else 1,
            -kv[1].match_score,
        ),
    )

    results: list[CompanyResearchResult] = []
    for k, job in companies:
        curated = CURATED.get(k, [])
        if not curated:
            for ck, cv in CURATED.items():
                if ck in k or k in ck:
                    curated = cv
                    break

        hunter_people: list[dict] = []
        if hunter.enabled and settings.hunter_enabled:
            try:
                hunter_people = await hunter.find_referral_candidates(
                    job.company,
                    job.role_category,
                    job.job_title,
                    max_people=5,
                    curated_count=len(curated),
                )
            except Exception as e:
                print(f"  Hunter error for {job.company}: {e}")

        profiles = merge_candidates(job, curated, hunter_people)
        note = ""
        if not profiles:
            note = (
                "No publicly verifiable current profiles found. "
                + ("Hunter returned no relevant people." if hunter.enabled else "Set HUNTER_API_KEY to search company domains.")
            )
        elif len(profiles) < 3:
            note = (
                f"Only {len(profiles)} verifiable profile(s) found; "
                "did not pad with random employees."
            )

        results.append(
            CompanyResearchResult(
                company=job.company,
                role_category=job.role_category,
                job_title=job.job_title,
                profiles=profiles,
                notes=note,
                job_id=job.job_id,
                job_url=job.job_url,
            )
        )
        src = ", ".join(sorted({p.source for p in profiles})) or "none"
        print(f"  {job.company}: {len(profiles)} profiles ({src})")

    xlsx_path = day / "referral_profiles.xlsx"
    md_path = day / "referral_profiles.md"
    export_referral_excel(results, xlsx_path)
    export_referral_markdown(results, md_path)

    found = sum(len(r.profiles) for r in results)
    print(f"\nSource Excel: {excel}")
    print(f"Companies: {len(results)}")
    print(f"Profiles found: {found}")
    print(f"Wrote: {xlsx_path}")
    print(f"Wrote: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
