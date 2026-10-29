"""Standalone resume generation for scored top jobs.

Same behavior as the former inlined block in run_full_search.py:
JD enrich → optional LLM explanations → tailor → LaTeX/PDF → cover letters → Excel update.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from job_agent.ai.llm_service import LLMService
from job_agent.config import get_settings
from job_agent.database.repository import JobRepository
from job_agent.export.excel_exporter import ExcelExporter
from job_agent.logging_config import get_logger
from job_agent.matching.dual_track import RoleTrack, score_job
from job_agent.matching.multi_profile import score_job_multi
from job_agent.profiles import load_profiles, resolve_profile
from job_agent.resume.folder_names import (
    basenames_for_company,
    company_folder_name,
    cover_letter_filename,
    cover_letters_for_company,
    format_role_label,
    new_company_basename_tracker,
    new_company_cover_letter_tracker,
    resume_file_basename,
)
from job_agent.resume.latex_generator import LatexGenerator
from job_agent.resume.parser import ResumeParser
from job_agent.schemas import ApplicationStatus, JobMatch

logger = get_logger(__name__)

MATCHES_SNAPSHOT = "top_matches.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def snapshot_path(out_dir: Path) -> Path:
    return out_dir / MATCHES_SNAPSHOT


def save_matches_snapshot(
    out_dir: Path,
    rows: list[tuple[JobMatch, RoleTrack | str, str]],
) -> Path:
    """Persist top matches so the Resume Agent can run separately."""
    payload: list[dict[str, Any]] = []
    for match, role_track, resume_used in rows:
        track = role_track.value if isinstance(role_track, RoleTrack) else str(role_track)
        payload.append(
            {
                "match": match.model_dump(mode="json"),
                "role_track": track,
                "resume_used": resume_used,
            }
        )
    path = snapshot_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("matches_snapshot_saved", path=str(path), count=len(payload))
    return path


def load_matches_snapshot(path: Path) -> list[tuple[JobMatch, RoleTrack, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows: list[tuple[JobMatch, RoleTrack, str]] = []
    for item in raw:
        match = JobMatch.model_validate(item["match"])
        track_raw = item.get("role_track", "ANDROID")
        try:
            track = RoleTrack(track_raw)
        except ValueError:
            track = RoleTrack.ANDROID
        resume_used = item.get("resume_used", "Android")
        rows.append((match, track, resume_used))
    return rows


def resolve_excel_for_day(day_dir: Path) -> Path:
    today = day_dir.name
    candidate = day_dir / f"android_backend_jobs_{today}.xlsx"
    if candidate.exists():
        return candidate
    found = sorted(day_dir.glob("android_backend_jobs_*.xlsx"))
    if not found:
        raise FileNotFoundError(f"No job Excel in {day_dir}")
    return found[-1]


def resolve_latest_excel() -> Path:
    out = PROJECT_ROOT / "output"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    candidate = out / today / f"android_backend_jobs_{today}.xlsx"
    if candidate.exists():
        return candidate
    matches = sorted(out.glob("*/android_backend_jobs_*.xlsx"), reverse=True)
    if not matches:
        raise FileNotFoundError("No job-tracking Excel found under output/. Run job search first.")
    return matches[0]


def _parse_role_track(label: str) -> RoleTrack:
    u = (label or "").strip().upper()
    if "BACKEND" in u:
        return RoleTrack.BACKEND
    if "HYBRID" in u:
        return RoleTrack.HYBRID
    return RoleTrack.ANDROID


def _parse_match_category(label: str):
    from job_agent.schemas import MatchCategory

    raw = (label or "").strip()
    for cat in MatchCategory:
        if cat.value == raw or cat.name.lower() == raw.lower():
            return cat
    return MatchCategory.MODERATE


def _parse_enum(enum_cls, value: str, default):
    raw = (value or "").strip()
    for item in enum_cls:
        if item.value == raw or item.name.lower() == raw.lower().replace(" ", "_"):
            return item
    return default


def build_snapshot_from_excel(excel: Path) -> Path:
    """Rebuild top_matches.json from a prior job Excel (for runs before the split)."""
    from openpyxl import load_workbook

    from job_agent.database.repository import generate_job_id
    from job_agent.schemas import (
        DateVerification,
        EmploymentType,
        MatchScoreBreakdown,
        NormalizedJob,
        SeniorityLevel,
    )

    excel = excel.expanduser().resolve()
    if not excel.exists():
        raise FileNotFoundError(f"Excel not found: {excel}")

    wb = load_workbook(str(excel))
    ws = wb.active
    headers = [str(c.value or "").strip() for c in ws[1]]
    header_idx = {h: i + 1 for i, h in enumerate(headers)}

    def cell(row: int, name: str):
        col = header_idx.get(name)
        if not col:
            return None
        return ws.cell(row=row, column=col).value

    def link(row: int, name: str) -> str:
        col = header_idx.get(name)
        if not col:
            return ""
        c = ws.cell(row=row, column=col)
        if c.hyperlink and c.hyperlink.target:
            return str(c.hyperlink.target).strip()
        val = str(c.value or "").strip()
        if val.lower() in ("", "apply here", "not generated", "—", "-"):
            return ""
        return val

    repo = JobRepository()
    rows: list[tuple[JobMatch, RoleTrack, str]] = []

    for row_i in range(2, ws.max_row + 1):
        company = str(cell(row_i, "Company") or "").strip()
        title = str(cell(row_i, "Job Title") or "").strip()
        if not company or not title:
            continue

        url = link(row_i, "Direct Application Link")
        source = str(cell(row_i, "Job Source") or "excel").strip() or "excel"
        location = str(cell(row_i, "Location") or "").strip()
        resume_used = str(cell(row_i, "Resume Used") or "Android").strip() or "Android"
        role_track = _parse_role_track(str(cell(row_i, "Role Category") or ""))
        try:
            score = float(cell(row_i, "Match Score") or 0)
        except (TypeError, ValueError):
            score = 0.0

        skills = [
            s.strip()
            for s in str(cell(row_i, "Matching Skills") or "").split(",")
            if s.strip()
        ]
        missing = [
            s.strip()
            for s in str(cell(row_i, "Missing Skills") or "").split(",")
            if s.strip()
        ]
        explanation = str(cell(row_i, "Match Explanation") or "").strip()

        # Prefer DB row for job_id + description when company/title match
        db = None
        for cand in repo.list_jobs(company=company, limit=50):
            if cand.title.strip().lower() == title.lower():
                db = cand
                break

        job_id = db.job_id if db else generate_job_id(company, title, url)
        description = (db.job_description if db else "") or ""
        job_url = (db.job_url if db and db.job_url else "") or url
        app_url = (db.application_url if db and db.application_url else "") or url
        if db and db.source:
            source = db.source

        job = NormalizedJob(
            job_id=job_id,
            source=source,
            source_job_id=(db.source_job_id if db else "") or "",
            title=title,
            company=company,
            location=location,
            location_tier=(db.location_tier if db else 0) or 0,
            description=description,
            job_url=job_url or f"excel://{company}/{title}",
            application_url=app_url or job_url,
            employment_type=_parse_enum(
                EmploymentType,
                str(cell(row_i, "Employment Type") or ""),
                EmploymentType.UNKNOWN,
            ),
            seniority=_parse_enum(
                SeniorityLevel,
                str(cell(row_i, "Seniority Level") or ""),
                SeniorityLevel.UNKNOWN,
            ),
            salary=str(cell(row_i, "Salary") or "").strip(),
            is_remote="remote" in location.lower() or "hybrid" in location.lower(),
            date_posted_verified=DateVerification.UNKNOWN,
            is_android_relevant=role_track in (RoleTrack.ANDROID, RoleTrack.HYBRID),
            career_portal=str(cell(row_i, "Career Portal") or "").strip()
            or (getattr(db, "career_portal", "") if db else ""),
            career_portal_job_id=str(cell(row_i, "Career Portal Job ID") or "").strip()
            or (getattr(db, "career_portal_job_id", "") if db else ""),
            career_portal_url=link(row_i, "Career Portal URL")
            or (getattr(db, "career_portal_url", "") if db else ""),
            career_job_id_type=str(cell(row_i, "Career Job ID Type") or "").strip()
            or (getattr(db, "career_job_id_type", "") if db else ""),
        )
        match = JobMatch(
            job=job,
            match_score=score,
            match_category=_parse_match_category(str(cell(row_i, "Match Category") or "")),
            matching_skills=skills,
            missing_requirements=missing,
            match_explanation=explanation,
            score_breakdown=MatchScoreBreakdown(total=score),
        )
        rows.append((match, role_track, resume_used))

    wb.close()
    repo.close()
    if not rows:
        raise FileNotFoundError(f"No jobs found in Excel: {excel}")
    path = save_matches_snapshot(excel.parent, rows)
    print(f"  Built snapshot from Excel ({len(rows)} jobs): {path}")
    return path


def resolve_matches_snapshot(
    explicit: str | None = None,
    excel: str | None = None,
) -> Path:
    """Find top_matches.json, or rebuild it from Excel if missing (pre-split runs)."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_dir():
            snap = snapshot_path(path)
            if snap.exists():
                return snap
            xlsx = resolve_excel_for_day(path)
            return build_snapshot_from_excel(xlsx)
        if path.name.endswith(".xlsx"):
            return build_snapshot_from_excel(path)
        if not path.exists():
            raise FileNotFoundError(f"Matches snapshot not found: {path}")
        return path

    if excel:
        return build_snapshot_from_excel(Path(excel).expanduser().resolve())

    out = PROJECT_ROOT / "output"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    candidate = snapshot_path(out / today)
    if candidate.exists():
        return candidate
    matches = sorted(out.glob(f"*/{MATCHES_SNAPSHOT}"), reverse=True)
    if matches:
        return matches[0]

    # Pre-split runs only have Excel — rebuild snapshot from latest report
    return build_snapshot_from_excel(resolve_latest_excel())


async def generate_resumes_from_snapshot(
    snapshot: Path,
    *,
    excel_path: Path | None = None,
) -> dict[str, Any]:
    """Run the full resume pipeline for a saved top-matches snapshot."""
    settings = get_settings()
    top = load_matches_snapshot(snapshot)
    out_dir = snapshot.parent
    excel = excel_path or resolve_excel_for_day(out_dir)

    # Search flags (PROFILE_ANDROID=false etc.) must not hide master templates —
    # resume generation still needs every profile's tex/json.
    profiles = load_profiles(respect_search_flags=False)
    android_resume = None
    backend_resume = None
    for p in profiles:
        if p.id == "android":
            android_resume = p.resume
        elif p.id == "backend":
            backend_resume = p.resume
    if android_resume is None:
        legacy_android = PROJECT_ROOT / "data/resume/master_resume.json"
        if legacy_android.exists():
            android_resume = ResumeParser(json_path=str(legacy_android)).load()
    if backend_resume is None:
        legacy_backend = PROJECT_ROOT / "data/resume/master_backend.json"
        if legacy_backend.exists():
            backend_resume = ResumeParser(json_path=str(legacy_backend)).load()
    if android_resume is None and profiles:
        android_resume = profiles[0].resume
    if backend_resume is None and profiles:
        backend_resume = profiles[-1].resume
    if android_resume is None or backend_resume is None:
        raise FileNotFoundError(
            "No resume profiles found under data/resume/profiles/. "
            "Add at least one profile folder with master_resume.json or master.tex."
        )

    def _resolve_for_job(resume_used: str, match: JobMatch):
        """Pick profile master resume + master.tex from role / company match.

        Android role → profiles/*/android master.tex
        Backend role → backend master.tex
        Flutter role → flutter master.tex (when folder exists)
        Hybrid / unknown → re-score against all loaded profiles.
        """
        resolved = resolve_profile(profiles, resume_used=resume_used)
        if resolved is None:
            multi = score_job_multi(match.job, profiles)
            if multi:
                _, _, best_used = multi
                resolved = resolve_profile(profiles, resume_used=best_used)
        if resolved is None and profiles:
            # Last resort: prefer android, else first by priority
            resolved = resolve_profile(profiles, resume_used="Android") or profiles[0]
        if resolved is None:
            return (
                android_resume,
                PROJECT_ROOT / "data/resume/master_android.tex",
                "Android",
                None,
            )
        return resolved.resume, resolved.tex_path, resolved.label, resolved

    llm = LLMService()
    latex = LatexGenerator()
    repo = JobRepository()

    resumes_dir = out_dir / "resumes"
    resumes_dir.mkdir(parents=True, exist_ok=True)

    # --- Enrich JDs for resume targets (skip Naukri URLs) ---
    # Include near-threshold jobs: an empty JD suppresses the score, so jobs
    # just below the cutoff can never rise without fetching the description.
    enrich_floor = max(
        0,
        settings.resume_generation_threshold - settings.resume_enrich_score_margin,
    )
    resume_targets = [m for m, _, _ in top if m.match_score >= enrich_floor]
    thin_jd = [m for m in resume_targets if len((m.job.description or "").strip()) < 80]
    if thin_jd:
        print(
            f"\n  Enriching job descriptions for {len(thin_jd)} jobs "
            f"(score>={enrich_floor}) via Brave..."
        )
        from job_agent.discovery.browser.jd_enricher import enrich_job_descriptions
        from job_agent.discovery.job_ids import is_naukri_listing

        enrichable = [
            m.job for m in thin_jd
            if not is_naukri_listing(
                source=m.job.source, job_url=m.job.job_url, application_url=m.job.application_url
            )
        ]
        n = await enrich_job_descriptions(enrichable, max_jobs=len(enrichable))
        print(f"  JD enriched: {n}/{len(enrichable)}")
        rescored: list[tuple[JobMatch, RoleTrack, str]] = []
        for match, role_track, resume_used in top:
            if len((match.job.description or "").strip()) >= 80:
                multi = score_job_multi(match.job, profiles)
                if multi:
                    match, track_label, resume_used = multi
                    try:
                        role_track = RoleTrack(track_label)
                    except Exception:
                        pass
                else:
                    match, role_track, resume_used = score_job(
                        match.job, android_resume, backend_resume
                    )
            rescored.append((match, role_track, resume_used))
        top = rescored
        save_matches_snapshot(out_dir, top)

    if not llm.available:
        print(
            "\n  ⚠ No LLM keys — resumes use deterministic tailor only.\n"
            "  Set GEMINI_API_KEY and/or GROQ_API_KEY and/or CEREBRAS_API_KEY in .env\n"
        )
    else:
        print(
            f"\n  LLM providers: {llm.providers_summary()} "
            f"(active: {llm.provider}/{llm.model})"
        )
        lanes = llm.worker_providers()
        if len(lanes) >= 2:
            print(
                "  Job lanes: "
                + ", ".join(f"job {i + 1} → {name}" for i, name in enumerate(lanes))
                + ", then whichever model is free"
            )
        pace_scope = "per provider" if len(lanes) >= 2 else "between calls"
        print(f"  Pace: {llm._min_interval:.0f}s {pace_scope}")
        print(f"  Profiles: {[p.output_folder_name for p in profiles]}")
        # Only LLM-enhance jobs that will get resumes — cuts Gemini load vs all 40
        explain_targets = [
            (m, rt, ru) for m, rt, ru in top
            if m.match_score >= settings.resume_generation_threshold
        ]
        print(
            f"  Enhancing explanations for {len(explain_targets)} "
            f"resume-eligible jobs (score>={settings.resume_generation_threshold})..."
        )

        async def _explain(match: JobMatch, resume_used: str) -> None:
            resume_for_llm, _, _, _ = _resolve_for_job(resume_used, match)
            try:
                match.match_explanation = await llm.enhance_match_explanation(
                    match, resume_for_llm
                )
            except Exception as e:
                logger.warning("llm_explain_skip", error=str(e)[:120])

        await asyncio.gather(*[
            _explain(match, resume_used)
            for match, _role_track, resume_used in explain_targets
        ])
        print("  Explanations done.\n")

    resume_paths: dict[str, tuple[str, str, str]] = {}
    basename_tracker = new_company_basename_tracker()
    cover_letter_tracker = new_company_cover_letter_tracker()
    file_lock = asyncio.Lock()

    async def _one_job(match, role_track, resume_used, provider: str | None):
        m = match
        async with file_lock:
            repo.upsert_job(
                m.job,
                match_score=m.match_score,
                match_category=m.match_category.value,
                match_reason=m.match_explanation,
                matching_skills=", ".join(m.matching_skills),
                missing_requirements=", ".join(m.missing_requirements),
                application_status=ApplicationStatus.MATCHED.value,
            )

        pdf_path = tex_path = cover_path_str = ""
        generated_one = False
        if m.match_score >= settings.resume_generation_threshold:
            master, tex_master, used, resolved = _resolve_for_job(resume_used, m)
            token = llm.pin(provider) if provider else None
            if provider:
                print(f"  [{provider}] {m.job.company} — {m.job.title}")
            try:
                # Resume, then cover letter, both on this job's provider.
                tailored = await llm.tailor_resume(master, m)
                cover = await llm.generate_cover_letter(master, m)
            finally:
                if token is not None:
                    llm.unpin(token)
            async with file_lock:
                profile_dir_name = (
                    resolved.output_folder_name if resolved else format_role_label(used)
                )
                company_dir = (
                    resumes_dir / profile_dir_name / company_folder_name(m.job.company)
                )
                company_dir.mkdir(parents=True, exist_ok=True)
                file_base = resume_file_basename(
                    used,
                    m.job.title,
                    basenames_for_company(
                        basename_tracker, m.job.company, profile_folder=profile_dir_name
                    ),
                )
                latex.master_path = Path(tex_master)
                if not latex.master_path.exists():
                    fallback_tex = (
                        resolved.tex_path if resolved and resolved.tex_path.exists()
                        else PROJECT_ROOT / "data/resume/master_android.tex"
                    )
                    logger.warning(
                        "profile_tex_missing",
                        wanted=str(tex_master),
                        fallback=str(fallback_tex),
                        profile=used,
                    )
                    latex.master_path = fallback_tex

                tex_path_obj, pdf_path_obj = latex.generate(
                    tailored, m.job.company, m.job.title, company_dir,
                    contact=master, match=m,
                    role_category=used,
                    file_basename=file_base,
                )
                tex_path = str(tex_path_obj)
                pdf_path = str(pdf_path_obj) if pdf_path_obj else ""

                cl_name = cover_letter_filename(
                    used,
                    m.job.title,
                    cover_letters_for_company(
                        cover_letter_tracker, m.job.company, profile_folder=profile_dir_name
                    ),
                )
                cover_path = company_dir / cl_name
                cover_path.write_text(cover, encoding="utf-8")
                cover_path_str = str(cover_path)

                repo.upsert_job(
                    m.job,
                    resume_path=pdf_path,
                    resume_tex_path=tex_path,
                    cover_letter_path=cover_path_str,
                    application_status=ApplicationStatus.RESUME_GENERATED.value,
                )
            resume_used = used
            generated_one = True

        track_for_excel = role_track.value if hasattr(role_track, "value") else str(role_track)
        row = (m, format_role_label(track_for_excel), resume_used)
        return row, (pdf_path, tex_path, cover_path_str), m.match_score >= 80, generated_one

    providers = llm.worker_providers() or [None]
    job_queue: asyncio.Queue = asyncio.Queue()
    for index, item in enumerate(top):
        job_queue.put_nowait((index, *item))
    processed_slots: list = [None] * len(top)

    async def _worker(provider: str | None) -> None:
        while True:
            try:
                index, match, role_track, resume_used = job_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                processed_slots[index] = await _one_job(
                    match, role_track, resume_used, provider
                )
            finally:
                job_queue.task_done()

    await asyncio.gather(*[_worker(provider) for provider in providers])
    processed = processed_slots
    rows_for_excel: list[tuple[JobMatch, str, str]] = []
    strong = 0
    generated = 0
    for row, paths, is_strong, did_generate in processed:
        rows_for_excel.append(row)
        resume_paths[row[0].job.job_id] = paths
        if is_strong:
            strong += 1
        if did_generate:
            generated += 1

    exporter = ExcelExporter()
    exporter.export_dual_track(rows_for_excel, excel, resume_paths, outreach_map={})
    save_matches_snapshot(out_dir, top)
    repo.close()

    print("\n" + "=" * 60)
    print("RESUME AGENT COMPLETED")
    print("=" * 60)
    print(f"  Top jobs:                 {len(top)}")
    print(f"  Strong matches (>=80):    {strong}")
    print(f"  Resumes generated (>=75): {generated}")
    print(f"\n  Excel:   {excel.resolve()}")
    print(f"  Resumes: {resumes_dir.resolve()}")

    return {
        "excel": excel,
        "resumes_dir": resumes_dir,
        "generated": generated,
        "strong": strong,
        "top_count": len(top),
    }
