#!/usr/bin/env python3
"""Convert master LaTeX resumes → master_resume.json for discovery/matching.

Usage:
  .venv/bin/python3 scripts/tex_to_master_json.py
  .venv/bin/python3 scripts/tex_to_master_json.py --tex data/resume/profiles/1-Android/master.tex --out data/resume/profiles/1-Android/master_resume.json
  .venv/bin/python3 scripts/tex_to_master_json.py --all

Prefer .tex. PDF is not converted here (too lossy); keep editing .tex as source of truth.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from job_agent.resume.tex_to_json import convert_tex_file


DEFAULT_PAIRS = [
    (
        PROJECT_ROOT / "data/resume/profiles/1-Android/master.tex",
        PROJECT_ROOT / "data/resume/profiles/1-Android/master_resume.json",
        PROJECT_ROOT / "data/resume/master_resume.json",
    ),
    (
        PROJECT_ROOT / "data/resume/profiles/2-Backend/master.tex",
        PROJECT_ROOT / "data/resume/profiles/2-Backend/master_resume.json",
        PROJECT_ROOT / "data/resume/master_backend.json",
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert master .tex → master_resume.json")
    ap.add_argument("--tex", type=Path, help="Input .tex file")
    ap.add_argument("--out", type=Path, help="Output .json path")
    ap.add_argument(
        "--all",
        action="store_true",
        default=True,
        help="Convert android + backend masters into profiles/ (default)",
    )
    ap.add_argument("--no-all", action="store_true", help="Disable --all")
    args = ap.parse_args()

    if args.tex and args.out:
        resume = convert_tex_file(args.tex, args.out)
        print(f"✓ {args.tex.name} → {args.out}")
        print(f"  experience={len(resume.experience)} projects={len(resume.projects)} "
              f"skill_cats={len(resume.technical_skills)} certs={len(resume.certifications)}")
        return 0

    if args.no_all:
        ap.error("Provide --tex and --out, or use --all")
        return 2

    for tex, profile_json, legacy_json in DEFAULT_PAIRS:
        if not tex.exists():
            # Fall back to profile copy of tex
            alt = profile_json.parent / "master.tex"
            if alt.exists():
                tex = alt
            else:
                print(f"✗ missing {tex}")
                continue
        resume = convert_tex_file(tex, profile_json)
        convert_tex_file(tex, legacy_json)
        # Keep profile master.tex in sync if converting from root masters
        profile_tex = profile_json.parent / "master.tex"
        if tex.resolve() != profile_tex.resolve() and tex.exists():
            profile_tex.write_text(tex.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"✓ {tex.relative_to(PROJECT_ROOT)} → {profile_json.relative_to(PROJECT_ROOT)}")
        print(f"  also → {legacy_json.relative_to(PROJECT_ROOT)}")
        print(
            f"  jobs={len(resume.experience)} projects={len(resume.projects)} "
            f"skills={sum(len(v) for v in resume.technical_skills.values())} "
            f"certs={len(resume.certifications)}"
        )
        print(f"  title sample: {resume.experience[0].title if resume.experience else '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
