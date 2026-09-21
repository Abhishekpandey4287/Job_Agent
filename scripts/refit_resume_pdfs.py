#!/usr/bin/env python3
"""Recompile generated resume PDFs so each fits on a single page.

Usage:
  .venv/bin/python3 scripts/refit_resume_pdfs.py                 # latest output day
  .venv/bin/python3 scripts/refit_resume_pdfs.py output/2026-09-15
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

from job_agent.config import get_settings  # noqa: E402
from job_agent.logging_config import setup_logging  # noqa: E402
from job_agent.resume.latex_generator import (  # noqa: E402
    LatexGenerator,
    _prepare_tex_for_tectonic,
    pdf_page_count,
)


def resolve_target(arg: str | None) -> Path:
    if arg:
        path = Path(arg).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Not found: {path}")
        return path
    days = sorted((PROJECT_ROOT / "output").glob("20*-*-*"), reverse=True)
    if not days:
        raise SystemExit("No output/<date> folders found")
    return days[0]


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, Path(settings.log_dir))

    target = resolve_target(sys.argv[1] if len(sys.argv) > 1 else None)
    tex_files = sorted(target.rglob("*.tex"))
    if not tex_files:
        raise SystemExit(f"No .tex files under {target}")

    latex = LatexGenerator()
    fixed = already_ok = failed = 0

    print(f"Refitting {len(tex_files)} resumes under {target}\n")
    for tex_path in tex_files:
        pdf_path = tex_path.with_suffix(".pdf")
        before = pdf_page_count(pdf_path) if pdf_path.exists() else 0

        tex_content = _prepare_tex_for_tectonic(
            tex_path.read_text(encoding="utf-8")
        )
        tex_path.write_text(tex_content, encoding="utf-8")

        compiled = latex._compile_pdf(tex_path, pdf_path)
        if not compiled:
            failed += 1
            print(f"  FAIL   {tex_path.relative_to(target)}")
            continue

        compiled = latex._fit_to_max_pages(tex_content, tex_path, pdf_path, compiled)
        after = pdf_page_count(compiled)

        label = tex_path.relative_to(target)
        if after == 1 and before != 1:
            fixed += 1
            print(f"  FIXED  {label}  ({before or '?'} -> 1 page)")
        elif after == 1:
            already_ok += 1
            print(f"  OK     {label}")
        else:
            failed += 1
            print(f"  STILL {after} PAGES  {label}")

    print(
        f"\nDone. fixed={fixed} already_single_page={already_ok} problems={failed}"
    )


if __name__ == "__main__":
    main()
