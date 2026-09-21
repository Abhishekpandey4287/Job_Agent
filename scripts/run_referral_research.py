#!/usr/bin/env python3
"""Deprecated entrypoint — use run_referral_agent.py instead."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
print("Use: .venv/bin/python3 scripts/run_referral_agent.py")
os.execv(
    str(PROJECT_ROOT / ".venv" / "bin" / "python3"),
    [str(PROJECT_ROOT / ".venv" / "bin" / "python3"), str(PROJECT_ROOT / "scripts" / "run_referral_agent.py"), *sys.argv[1:]],
)
