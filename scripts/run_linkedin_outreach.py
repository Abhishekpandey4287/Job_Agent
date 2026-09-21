#!/usr/bin/env python3
"""Direct entry point for the LinkedIn / Referral Outreach Agent.

This is separate from job search. It does NOT scrape LinkedIn via CDP.
It uses Hunter.io + public web to find referral contacts and write
personalized messages (≤250 characters).

Usage:
  .venv/bin/python3 scripts/run_linkedin_outreach.py
  .venv/bin/python3 scripts/run_linkedin_outreach.py --yes
  .venv/bin/python3 scripts/run_linkedin_outreach.py --excel output/2026-09-03/android_backend_jobs_2026-09-03.xlsx

Same agent as:
  .venv/bin/python3 scripts/run_referral_agent.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
TARGET = PROJECT_ROOT / "scripts" / "run_referral_agent.py"

if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(__file__), *sys.argv[1:]])

os.execv(
    str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable),
    [
        str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable),
        str(TARGET),
        *sys.argv[1:],
    ],
)
