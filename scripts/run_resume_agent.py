#!/usr/bin/env python3
"""Resume Agent — standalone from job search.

Generates tailored resumes + cover letters for today's top matches.
Uses the same logic formerly inlined in run_full_search.py.

Usage:
  .venv/bin/python3 scripts/run_resume_agent.py
  .venv/bin/python3 scripts/run_resume_agent.py --excel output/2026-09-04/android_backend_jobs_2026-09-04.xlsx
  .venv/bin/python3 scripts/run_resume_agent.py --snapshot output/2026-09-04/top_matches.json
  .venv/bin/python3 scripts/run_resume_agent.py --yes   # skip confirmation prompt
"""

from __future__ import annotations

import argparse
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

from job_agent.logging_config import setup_logging  # noqa: E402
from job_agent.config import get_settings  # noqa: E402
from job_agent.resume.resume_agent import (  # noqa: E402
    generate_resumes_from_snapshot,
    resolve_excel_for_day,
    resolve_matches_snapshot,
)


def confirm(snapshot: Path, auto_yes: bool) -> bool:
    excel = resolve_excel_for_day(snapshot.parent)
    print("=" * 60)
    print("RESUME AGENT")
    print("=" * 60)
    print("This is separate from job fetching.")
    print("It will enrich JDs, tailor resumes, and write cover letters.")
    print(f"\nMatches snapshot: {snapshot}")
    print(f"Excel to update:  {excel}")
    if auto_yes:
        return True
    try:
        answer = input("\nProceed? [yes/no]: ").strip().lower()
    except EOFError:
        answer = "no"
    return answer in ("y", "yes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Resume Agent")
    parser.add_argument(
        "--snapshot",
        help="Path to top_matches.json (or its parent output day folder)",
    )
    parser.add_argument(
        "--excel",
        help="Path to job Excel — builds top_matches.json if snapshot is missing",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Skip yes/no confirmation")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.log_level, Path(settings.log_dir))

    try:
        snapshot = resolve_matches_snapshot(args.snapshot, excel=args.excel)
    except FileNotFoundError as e:
        raise SystemExit(str(e)) from e

    if not confirm(snapshot, args.yes):
        print("Skipped. Run later with:")
        print("  .venv/bin/python3 scripts/run_resume_agent.py")
        return

    print("\nStarting Resume Agent...\n")
    asyncio.run(generate_resumes_from_snapshot(snapshot))


if __name__ == "__main__":
    main()
