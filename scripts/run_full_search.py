#!/usr/bin/env python3
"""Execute full multi-profile internet-wide job search pipeline.

Discovery + scoring + Excel only. Resume generation and referral outreach
are separate agents prompted with yes/no after search completes.
"""

import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

import httpx

from job_agent.config import get_settings
from job_agent.database.models import init_db
from job_agent.database.repository import JobRepository
from job_agent.discovery.orchestrator import run_discovery
from job_agent.export.excel_exporter import ExcelExporter
from job_agent.logging_config import setup_logging
from job_agent.resume.folder_names import format_role_label
from job_agent.resume.resume_agent import save_matches_snapshot
from job_agent.schemas import ApplicationStatus


async def detect_cdp() -> str:
    import urllib.request

    urls = []
    settings = get_settings()
    if settings.brave_cdp_url:
        urls.append(settings.brave_cdp_url.rstrip("/"))
    for port in range(9222, 9231):
        urls.append(f"http://127.0.0.1:{port}")

    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        try:
            with urllib.request.urlopen(f"{url}/json/version", timeout=2) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            pass
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{url}/json/version")
                if r.status_code == 200:
                    return url
        except Exception:
            pass
    return ""


async def main():
    settings = get_settings()
    setup_logging(settings.log_level, Path(settings.log_dir))
    init_db()

    print("=" * 60)
    print("JOB SEARCH AGENT — INTERNET-WIDE DISCOVERY")
    print("=" * 60)

    cdp = await detect_cdp()
    if cdp:
        print(f"✓ Brave Switch profile accessible via CDP ({cdp})")
    else:
        print("✗ Brave CDP not open — using public APIs + browser fallback if enabled.\n")

    result = await run_discovery(cdp_url=cdp)
    top = result.top_rows
    repo = JobRepository()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    out_dir = Path(settings.output_dir) / today
    out_dir.mkdir(parents=True, exist_ok=True)

    strong = 0
    exporter = ExcelExporter()
    rows_for_excel = []
    resume_paths: dict[str, tuple[str, str, str]] = {}

    for match, role_track, resume_used in top:
        m = match
        track_label = role_track if isinstance(role_track, str) else getattr(role_track, "value", str(role_track))
        repo.upsert_job(
            m.job,
            match_score=m.match_score,
            match_category=m.match_category.value,
            match_reason=m.match_explanation,
            matching_skills=", ".join(m.matching_skills),
            missing_requirements=", ".join(m.missing_requirements),
            application_status=ApplicationStatus.MATCHED.value,
        )
        if m.match_score >= 80:
            strong += 1
        resume_paths[m.job.job_id] = ("", "", "")
        rows_for_excel.append((m, format_role_label(track_label), resume_used))

    # Snapshot needs RoleTrack-compatible values — store track string via enum when possible
    from job_agent.matching.dual_track import RoleTrack

    snapshot_rows = []
    for match, role_track, resume_used in top:
        if isinstance(role_track, RoleTrack):
            track = role_track
        else:
            try:
                track = RoleTrack(str(role_track).upper())
            except Exception:
                track = RoleTrack.ANDROID
        snapshot_rows.append((match, track, resume_used))

    snapshot_file = save_matches_snapshot(out_dir, snapshot_rows)
    excel_path = out_dir / f"android_backend_jobs_{today}.xlsx"
    exporter.export_dual_track(rows_for_excel, excel_path, resume_paths, outreach_map={})

    resume_eligible = sum(
        1 for m, _, _ in top if m.match_score >= settings.resume_generation_threshold
    )

    # Legacy summary fields
    android_n = result.profile_match_counts.get("android", 0)
    backend_n = result.profile_match_counts.get("backend", 0)
    other_n = sum(v for k, v in result.profile_match_counts.items() if k not in ("android", "backend"))

    within_24h = sum(
        1 for m, _, _ in top
        if m.job.date_posted and _within_hours(m.job.date_posted, settings.job_recency_hours)
    )

    summary = result.stats.format_summary(
        total_raw=result.raw_count,
        after_dedup=result.deduped_count,
        relevant_android=android_n,
        relevant_backend=backend_n,
        relevant_hybrid=other_n,
        within_24h=within_24h,
        top_count=len(top),
    )
    print("\n" + summary)
    print("\n" + result.stats.format_health())

    print("\n" + "=" * 60)
    print("JOB DISCOVERY COMPLETE")
    print("=" * 60)
    print(f"Profiles searched:         {len(result.profiles)} {[p.id for p in result.profiles]}")
    print(f"Query variants used:       {len(result.queries_used)}")
    print(f"Locations used:            {len(result.locations_used)}")
    print(f"Sources attempted:         {result.sources_attempted}")
    print(f"Sources with jobs:         {result.sources_successful}")
    print(f"Brave CDP:                 {'USED' if result.brave_used else 'NOT USED'}")
    if result.public_browser_used:
        print("Public browser fallback:   YES")
    print(f"Raw jobs discovered:       {result.raw_count}")
    print(f"After normalization:       {result.normalized_count}")
    print(f"After deduplication:       {result.deduped_count}")
    print(f"Matched jobs:              {result.matched_count}")
    print(
        f"Top jobs exported:         {len(top)} "
        f"(score>={settings.top_jobs_priority_score}, "
        f"cap={'unlimited' if settings.top_jobs_count <= 0 else settings.top_jobs_count})"
    )
    print(f"Strong matches (>=80):     {strong}")
    print(f"Resume-eligible (>=75):    {resume_eligible} (not generated yet)")
    print("Profiles:")
    for pid, cnt in sorted(result.profile_match_counts.items()):
        print(f"  {pid}: {cnt} matches")
    print(f"\n  Excel:     {excel_path.resolve()}")
    print(f"  Snapshot:  {snapshot_file.resolve()}")
    if result.errors:
        print(f"\n  Errors/skips: {len(result.errors)} (non-fatal)")
        for err in result.errors[:8]:
            print(f"    - {err[:120]}")
    repo.close()

    _maybe_run_resume_agent(snapshot_file)
    _maybe_run_referral_agent(excel_path)


def _python() -> str:
    return str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)


def _maybe_run_resume_agent(snapshot_path: Path) -> None:
    print("\n" + "=" * 60)
    print("RESUME AGENT (separate from job search)")
    print("=" * 60)
    print("Enrich JDs, tailor resumes, write cover letters for top matches.")
    print(f"Input snapshot: {snapshot_path}")
    try:
        answer = input("\nRun Resume Agent now? [yes/no]: ").strip().lower()
    except EOFError:
        answer = "no"
    if answer not in ("y", "yes"):
        print("Skipped. Run later with:")
        print("  .venv/bin/python3 scripts/run_resume_agent.py")
        return

    script = PROJECT_ROOT / "scripts" / "run_resume_agent.py"
    print("\nStarting Resume Agent...\n")
    subprocess.run(
        [_python(), str(script), "--snapshot", str(snapshot_path), "--yes"],
        cwd=str(PROJECT_ROOT),
        check=False,
    )


def _maybe_run_referral_agent(excel_path: Path) -> None:
    print("\n" + "=" * 60)
    print("REFERRAL AGENT (separate from job search)")
    print("=" * 60)
    print("Find referral contacts + personalized messages (≤250 chars).")
    print("Uses Hunter.io / public web only — does NOT scrape LinkedIn.")
    print(f"Input Excel: {excel_path}")
    try:
        answer = input("\nRun Referral Agent now? [yes/no]: ").strip().lower()
    except EOFError:
        answer = "no"
    if answer not in ("y", "yes"):
        print("Skipped. Run later with:")
        print("  .venv/bin/python3 scripts/run_referral_agent.py")
        return

    script = PROJECT_ROOT / "scripts" / "run_referral_agent.py"
    print("\nStarting Referral Agent...\n")
    subprocess.run(
        [_python(), str(script), "--excel", str(excel_path), "--yes"],
        cwd=str(PROJECT_ROOT),
        check=False,
    )


def _within_hours(dt, hours: int) -> bool:
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    posted = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return posted >= cutoff


if __name__ == "__main__":
    asyncio.run(main())
