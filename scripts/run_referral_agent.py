#!/usr/bin/env python3
"""Referral Agent — standalone from job search.

Finds up to 3 referral contacts per company from today's job Excel and writes
personalized messages (≤250 characters).

Sources (no LinkedIn login / CDP scraping):
  - Optional Hunter.io API (HUNTER_API_KEY)
  - Public-web curated / search findings

Usage:
  .venv/bin/python3 scripts/run_referral_agent.py
  .venv/bin/python3 scripts/run_referral_agent.py --excel output/2026-09-03/android_backend_jobs_2026-09-03.xlsx
  .venv/bin/python3 scripts/run_referral_agent.py --yes   # skip confirmation prompt
"""

from __future__ import annotations

import argparse
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

# Reuse the builder implementation
from scripts.build_referral_profiles import main as build_main  # noqa: E402


def resolve_excel(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Excel not found: {path}")
        return path

    out = PROJECT_ROOT / "output"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    candidate = out / today / f"android_backend_jobs_{today}.xlsx"
    if candidate.exists():
        return candidate
    matches = sorted(out.glob("*/android_backend_jobs_*.xlsx"), reverse=True)
    if not matches:
        raise SystemExit("No job-tracking Excel found under output/. Run job search first.")
    return matches[0]


def confirm(excel: Path, auto_yes: bool) -> bool:
    print("=" * 60)
    print("REFERRAL AGENT")
    print("=" * 60)
    print("This is separate from job fetching.")
    print("It will research referral contacts and write ≤250-char messages.")
    print("No LinkedIn browser scraping.")
    print(f"\nInput Excel: {excel}")
    if auto_yes:
        return True
    try:
        answer = input("\nProceed? [yes/no]: ").strip().lower()
    except EOFError:
        answer = "no"
    return answer in ("y", "yes")


async def _run_with_excel(excel: Path) -> None:
    """Monkey-patch argv so build_referral_profiles resolves this excel via env."""
    # build_referral_profiles discovers excel by date; set symlink-friendly override
    os.environ["REFERRAL_EXCEL_PATH"] = str(excel)
    # Prefer calling shared logic directly
    from job_agent.config import get_settings
    from job_agent.outreach.hunter_client import HunterClient
    from job_agent.outreach.referral_research import (
        CompanyResearchResult,
        export_referral_excel,
        export_referral_markdown,
        load_jobs_from_excel,
    )
    from scripts.build_referral_profiles import (
        CURATED,
        company_key,
        merge_candidates,
    )

    settings = get_settings()
    jobs = load_jobs_from_excel(excel)
    day = excel.parent

    by_company = {}
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
        else:
            print(f"Hunter.io: ENABLED — {hunter.budget_summary()}")
        print(f"  Local budget: {hunter.budget_summary()}")
        print("  → max 1 Domain Search credit per company; skips when curated ≥3")
        if hunter.remaining <= 0:
            print("  ⚠ Hunter budget exhausted for this month/run — using curated/public only.")
    else:
        print("Hunter.io: DISABLED (set HUNTER_API_KEY in .env)")
    print(f"Companies to research: {len(by_company)}\n")

    # Spend scarce credits on known-domain + highest-match companies first
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
        hunter_people = []
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
            note = "No verifiable profiles found for this company."
        elif len(profiles) < 3:
            note = f"Only {len(profiles)} verifiable profile(s) found."

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

    if hunter.enabled and settings.hunter_enabled:
        print(f"\nHunter credits after run: {hunter.budget_summary()}")

    xlsx_path = day / "referral_profiles.xlsx"
    md_path = day / "referral_profiles.md"
    export_referral_excel(results, xlsx_path)
    export_referral_markdown(results, md_path)

    found = sum(len(r.profiles) for r in results)
    print("\n" + "=" * 60)
    print("REFERRAL AGENT COMPLETE")
    print("=" * 60)
    print(f"  Companies: {len(results)}")
    print(f"  Profiles:  {found}")
    print(f"  Excel:     {xlsx_path.resolve()}")
    print(f"  Markdown:  {md_path.resolve()}")
    print("\n  Open each LinkedIn URL and confirm before sending messages.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Referral Agent")
    parser.add_argument("--excel", help="Path to job-tracking Excel from job search")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip yes/no confirmation")
    args = parser.parse_args()

    excel = resolve_excel(args.excel)
    if not confirm(excel, args.yes):
        print("Cancelled.")
        return
    asyncio.run(_run_with_excel(excel))


if __name__ == "__main__":
    main()
