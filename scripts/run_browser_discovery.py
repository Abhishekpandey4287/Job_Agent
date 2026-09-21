#!/usr/bin/env python3
"""Run job discovery using Brave browser CDP connection.

Usage (from any directory):
    /Users/abhishekpandey/Downloads/job/scripts/run_browser_discovery.py

Or from project root:
    source .venv/bin/activate
    python scripts/run_browser_discovery.py
"""

import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

from job_agent.config import get_settings
from job_agent.database.models import init_db
from job_agent.logging_config import setup_logging
from job_agent.pipeline.daily_pipeline import DailyPipeline


async def main():
    print(f"Working directory: {os.getcwd()}")
    settings = get_settings()
    setup_logging(settings.log_level, Path(settings.log_dir))
    init_db()

    import httpx
    for port in range(9222, 9231):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
            if r.status_code == 200:
                settings.brave_cdp_url = f"http://127.0.0.1:{port}"
                print(f"Connected to Brave CDP at port {port}")
                break
        except Exception:
            pass

    if not settings.brave_cdp_url:
        print("WARNING: Brave CDP not available — running API sources only.")
        print("For logged-in portal jobs, launch Brave first:")
        print(f"  {PROJECT_ROOT}/scripts/start_brave_debug.sh")
        print()

    print("Starting job discovery (this may take several minutes)...")
    pipeline = DailyPipeline()
    summary = await pipeline.run()

    print(f"\nPipeline complete!")
    print(f"  Jobs discovered: {summary.jobs_discovered}")
    print(f"  After dedup: {summary.jobs_after_deduplication}")
    print(f"  Relevant Android: {summary.relevant_android_jobs}")
    print(f"  Within recency: {summary.jobs_within_recency}")
    print(f"  Strong matches: {summary.strong_matches}")
    print(f"  Resumes generated: {summary.resumes_generated}")
    print(f"  Output: {summary.output_path}")
    if summary.errors:
        print(f"  Errors ({len(summary.errors)}):")
        for e in summary.errors[:5]:
            print(f"    - {e[:120]}")


if __name__ == "__main__":
    asyncio.run(main())
