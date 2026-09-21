"""Daily job search pipeline orchestrator."""

from datetime import datetime
from pathlib import Path
from typing import List

from job_agent.ai.llm_service import LLMService
from job_agent.config import get_settings
from job_agent.database.models import PipelineRun
from job_agent.database.repository import JobRepository
from job_agent.discovery.brave_browser import BraveBrowserAdapter
from job_agent.discovery.adzuna import AdzunaAdapter
from job_agent.discovery.arbeitnow import ArbeitnowAdapter
from job_agent.discovery.base import JobSourceRegistry
from job_agent.discovery.greenhouse import GreenhouseAdapter
from job_agent.discovery.jsearch import JSearchAdapter
from job_agent.discovery.remotive import RemotiveAdapter
from job_agent.export.excel_exporter import ExcelExporter
from job_agent.logging_config import get_logger
from job_agent.matching.scorer import MatchScorer
from job_agent.normalization.filters import (
    deduplicate_jobs,
    filter_by_recency,
    filter_relevant_jobs,
    normalize_job,
)
from job_agent.resume.folder_names import (
    basenames_for_company,
    company_folder_name,
    cover_letter_filename,
    cover_letters_for_company,
    new_company_basename_tracker,
    new_company_cover_letter_tracker,
    resume_file_basename,
)
from job_agent.resume.latex_generator import LatexGenerator
from job_agent.resume.parser import ResumeParser
from job_agent.schemas import ApplicationStatus, JobMatch, PipelineSummary

logger = get_logger(__name__)


class DailyPipeline:
    def __init__(self):
        self.settings = get_settings()
        self.registry = self._build_registry()
        self.resume_parser = ResumeParser()
        self.llm = LLMService()
        self.latex_gen = LatexGenerator()
        self.excel_exporter = ExcelExporter()

    def _build_registry(self) -> JobSourceRegistry:
        registry = JobSourceRegistry()
        adapters = [
            BraveBrowserAdapter(),
            AdzunaAdapter(), JSearchAdapter(), RemotiveAdapter(),
            ArbeitnowAdapter(), GreenhouseAdapter(),
        ]
        for adapter in adapters:
            if adapter.name == "brave_browser" and not self.settings.brave_browser_enabled:
                continue
            registry.register(adapter)
        return registry

    async def run(self) -> PipelineSummary:
        started = datetime.utcnow()
        errors: List[str] = []
        summary = PipelineSummary(search_started=started)

        logger.info("pipeline_started")

        from job_agent.database.models import init_db
        init_db()

        try:
            resume = self.resume_parser.load()
        except FileNotFoundError as e:
            errors.append(str(e))
            summary.errors = errors
            return summary

        scorer = MatchScorer(resume)
        repo = JobRepository()

        queries = self.settings.target_roles[:4]
        locations = (
            self.settings.target_locations_mumbai[:2]
            + self.settings.target_locations_india[:3]
            + ["Remote"]
        )

        raw_jobs: List = []
        max_per = self.settings.max_jobs_per_search // max(len(queries) * len(locations), 1)

        # Brave browser: single session (logged-in Switch profile)
        if self.settings.brave_browser_enabled:
            brave = BraveBrowserAdapter()
            brave_jobs, brave_errors, _, _ = await brave.search_batch(max_per_combo=50)
            raw_jobs.extend(brave_jobs)
            errors.extend(brave_errors)

        # API-based sources
        api_registry = JobSourceRegistry()
        for adapter in [
            AdzunaAdapter(), JSearchAdapter(), RemotiveAdapter(),
            ArbeitnowAdapter(), GreenhouseAdapter(),
        ]:
            api_registry.register(adapter)

        api_jobs, search_errors = await api_registry.search_all(
            queries=queries,
            locations=locations,
            max_per_source=max_per,
        )
        raw_jobs.extend(api_jobs)
        errors.extend(search_errors)

        total_sources = len(api_registry.get_all()) + (1 if self.settings.brave_browser_enabled else 0)
        summary.sources_searched = total_sources
        summary.jobs_discovered = len(raw_jobs)

        normalized = [normalize_job(r) for r in raw_jobs]
        deduped = deduplicate_jobs(normalized)
        summary.jobs_after_deduplication = len(deduped)

        relevant = filter_relevant_jobs(deduped)
        summary.relevant_android_jobs = len(relevant)

        within_24h_count = sum(
            1 for j in relevant
            if j.date_posted and self._is_within_hours(j.date_posted, self.settings.job_recency_hours)
        )
        summary.jobs_within_recency = within_24h_count

        matches: List[JobMatch] = []
        for job in relevant:
            if repo.exists(job.job_id):
                existing = repo.get_by_job_id(job.job_id)
                if existing and existing.match_score is not None:
                    match = scorer.score(job)
                    match.job = job
                    if existing.match_reason:
                        match.match_explanation = existing.match_reason
                    matches.append(match)
                    continue

            match = scorer.score(job)
            matches.append(match)

        matches.sort(key=self._rank_job)
        min_score = float(getattr(self.settings, "top_jobs_priority_score", 70) or 70)
        qualifying = [m for m in matches if m.match_score >= min_score]
        limit = int(getattr(self.settings, "top_jobs_count", 0) or 0)
        top_matches = qualifying if limit <= 0 else qualifying[:limit]
        summary.strong_matches = sum(1 for m in top_matches if m.match_score >= 80)

        # LLM explanations only for top matches (pacing enforced inside LLMService)
        if self.llm.available:
            for match in top_matches:
                try:
                    match.match_explanation = await self.llm.enhance_match_explanation(
                        match, resume
                    )
                except Exception:
                    pass

        today = datetime.utcnow().strftime("%Y-%m-%d")
        output_dir = Path(self.settings.output_dir) / today
        resumes_dir = output_dir / "resumes"
        resume_paths: dict[str, tuple[str, str, str]] = {}
        basename_tracker = new_company_basename_tracker()
        cover_letter_tracker = new_company_cover_letter_tracker()

        for match in top_matches:
            record = repo.upsert_job(
                match.job,
                match_score=match.match_score,
                match_category=match.match_category.value,
                match_reason=match.match_explanation,
                matching_skills=", ".join(match.matching_skills),
                missing_requirements=", ".join(match.missing_requirements),
                application_status=ApplicationStatus.MATCHED.value,
            )

            if match.match_score >= self.settings.resume_generation_threshold:
                company_dir = resumes_dir / company_folder_name(match.job.company)
                company_dir.mkdir(parents=True, exist_ok=True)
                file_base = resume_file_basename(
                    "ANDROID",
                    match.job.title,
                    basenames_for_company(basename_tracker, match.job.company),
                )
                tailored = await self.llm.tailor_resume(resume, match)
                tex_path, pdf_path = self.latex_gen.generate(
                    tailored, match.job.company, match.job.title, company_dir,
                    contact=resume, match=match, role_category="ANDROID",
                    file_basename=file_base,
                )

                cover_letter = await self.llm.generate_cover_letter(resume, match)
                cl_name = cover_letter_filename(
                    "ANDROID",
                    match.job.title,
                    cover_letters_for_company(cover_letter_tracker, match.job.company),
                )
                cover_path = company_dir / cl_name
                cover_path.write_text(cover_letter, encoding="utf-8")

                resume_paths[match.job.job_id] = (
                    str(pdf_path) if pdf_path else "",
                    str(tex_path),
                    str(cover_path),
                )

                repo.upsert_job(
                    match.job,
                    resume_path=str(pdf_path) if pdf_path else "",
                    resume_tex_path=str(tex_path),
                    cover_letter_path=str(cover_path),
                    application_status=ApplicationStatus.RESUME_GENERATED.value,
                )
                summary.resumes_generated += 1

        excel_path = output_dir / f"android_jobs_{today}.xlsx"
        self.excel_exporter.export(top_matches, excel_path, resume_paths)

        summary.application_ready = summary.resumes_generated
        summary.search_completed = datetime.utcnow()
        summary.errors = errors
        summary.output_path = str(output_dir)

        pipeline_run = PipelineRun(
            started_at=started,
            completed_at=summary.search_completed,
            sources_searched=summary.sources_searched,
            jobs_discovered=summary.jobs_discovered,
            jobs_deduplicated=summary.jobs_after_deduplication,
            relevant_jobs=summary.relevant_android_jobs,
            jobs_within_recency=summary.jobs_within_recency,
            strong_matches=summary.strong_matches,
            resumes_generated=summary.resumes_generated,
            errors="; ".join(errors),
            output_path=str(output_dir),
        )
        repo.save_pipeline_run(pipeline_run)
        repo.close()

        logger.info(
            "pipeline_complete",
            discovered=summary.jobs_discovered,
            relevant=summary.relevant_android_jobs,
            top=len(top_matches),
            resumes=summary.resumes_generated,
            output=str(output_dir),
        )

        return summary

    def _is_within_hours(self, date_posted, hours: int) -> bool:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        posted = date_posted.replace(tzinfo=None) if date_posted.tzinfo else date_posted
        return posted >= cutoff

    def _rank_job(self, match: JobMatch) -> tuple:
        recency = 1 if (
            match.job.date_posted
            and self._is_within_hours(match.job.date_posted, self.settings.job_recency_hours)
        ) else 0
        return (-recency, -match.match_score, -match.job.location_tier)
