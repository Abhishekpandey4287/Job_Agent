"""FastAPI application and REST API routes."""

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from job_agent.application.manager import ApplicationManager
from job_agent.config import get_settings
from job_agent.database.models import init_db
from job_agent.database.repository import ConfigRepository, JobRepository
from job_agent.logging_config import setup_logging, get_logger
from job_agent.pipeline.daily_pipeline import DailyPipeline
from job_agent.resume.parser import ResumeParser
from job_agent.scheduler.daily_scheduler import start_scheduler, stop_scheduler
from job_agent.schemas import ApplicationStatus, ConfigUpdate, DashboardStats, JobResponse

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level, Path(settings.log_dir))
    init_db()
    start_scheduler()
    logger.info("app_started")
    yield
    stop_scheduler()
    logger.info("app_stopped")


app = FastAPI(
    title="Job Search Agent",
    description="AI-Powered Job Search & Application Agent for Android/Mobile roles",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StatusUpdate(BaseModel):
    status: ApplicationStatus
    notes: str = ""


class ApplyRequest(BaseModel):
    approve: bool = True


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats():
    repo = JobRepository()
    stats = repo.get_dashboard_stats()
    repo.close()
    return DashboardStats(**stats)


@app.get("/api/jobs", response_model=List[JobResponse])
async def list_jobs(
    status: Optional[str] = None,
    min_score: Optional[float] = Query(None),
    company: Optional[str] = None,
    location: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    repo = JobRepository()
    records = repo.list_jobs(
        status=status, min_score=min_score, company=company,
        location=location, source=source, limit=limit, offset=offset,
    )
    repo.close()
    return [_to_job_response(r) for r in records]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    repo = JobRepository()
    record = repo.get_by_job_id(job_id)
    repo.close()
    if not record:
        raise HTTPException(404, "Job not found")
    return {
        **_to_job_response(record).model_dump(),
        "job_description": record.job_description,
        "match_reason": record.match_reason,
        "matching_skills": record.matching_skills,
        "missing_requirements": record.missing_requirements,
        "resume_path": record.resume_path,
        "resume_tex_path": record.resume_tex_path,
        "cover_letter_path": record.cover_letter_path,
        "notes": record.notes,
    }


@app.patch("/api/jobs/{job_id}/status")
async def update_job_status(job_id: str, update: StatusUpdate):
    mgr = ApplicationManager()
    status_map = {
        ApplicationStatus.APPLIED: mgr.mark_applied,
        ApplicationStatus.REJECTED: mgr.mark_rejected,
        ApplicationStatus.INTERVIEW: mgr.mark_interview,
        ApplicationStatus.OFFER: mgr.mark_offer,
        ApplicationStatus.READY_TO_APPLY: mgr.approve_application,
    }
    handler = status_map.get(update.status)
    if not handler:
        raise HTTPException(400, f"Unsupported status transition: {update.status}")
    success = handler(job_id, update.notes)
    mgr.close()
    if not success:
        raise HTTPException(404, "Job not found or transition failed")
    return {"status": "updated", "job_id": job_id, "new_status": update.status.value}


@app.post("/api/jobs/{job_id}/apply")
async def apply_to_job(job_id: str, request: ApplyRequest):
    mgr = ApplicationManager()
    if request.approve:
        mgr.approve_application(job_id)
    started = mgr.start_application(job_id)
    mgr.close()
    if not started:
        raise HTTPException(400, "Application requires approval or job not found")
    return {
        "status": "APPLICATION_STARTED",
        "job_id": job_id,
        "message": "Application marked as started. Manual submission required at the application URL.",
    }


@app.post("/api/pipeline/run")
async def run_pipeline():
    pipeline = DailyPipeline()
    summary = await pipeline.run()
    return summary.model_dump()


@app.get("/api/resume/master")
async def get_master_resume():
    parser = ResumeParser()
    resume = parser.load()
    return resume.model_dump()


@app.get("/api/resume/download/{job_id}")
async def download_resume(job_id: str, format: str = "pdf"):
    repo = JobRepository()
    record = repo.get_by_job_id(job_id)
    repo.close()
    if not record:
        raise HTTPException(404, "Job not found")

    path = record.resume_tex_path if format == "tex" else record.resume_path
    if not path or not Path(path).exists():
        raise HTTPException(404, f"Resume {format} not found")

    return FileResponse(path, filename=Path(path).name)


@app.get("/api/config")
async def get_config():
    settings = get_settings()
    return {
        "target_roles": settings.target_roles,
        "target_locations_mumbai": settings.target_locations_mumbai,
        "target_locations_india": settings.target_locations_india,
        "resume_generation_threshold": settings.resume_generation_threshold,
        "top_jobs_count": settings.top_jobs_count,
        "job_recency_hours": settings.job_recency_hours,
        "daily_search_time": settings.daily_search_time,
        "auto_apply_enabled": settings.auto_apply_enabled,
        "notification_enabled": settings.notification_enabled,
    }


@app.put("/api/config")
async def update_config(update: ConfigUpdate):
    repo = ConfigRepository()
    if update.target_roles is not None:
        repo.set_json("target_roles", update.target_roles)
    if update.resume_generation_threshold is not None:
        repo.set("resume_generation_threshold", str(update.resume_generation_threshold))
    if update.top_jobs_count is not None:
        repo.set("top_jobs_count", str(update.top_jobs_count))
    if update.daily_search_time is not None:
        repo.set("daily_search_time", update.daily_search_time)
    if update.auto_apply_enabled is not None:
        repo.set("auto_apply_enabled", str(update.auto_apply_enabled))
    if update.notification_enabled is not None:
        repo.set("notification_enabled", str(update.notification_enabled))
    repo.close()
    return {"status": "updated"}


def _to_job_response(record) -> JobResponse:
    return JobResponse(
        id=record.id,
        job_id=record.job_id,
        company=record.company,
        title=record.title,
        location=record.location,
        match_score=record.match_score,
        match_category=record.match_category,
        application_status=record.application_status,
        date_posted=record.date_posted,
        date_discovered=record.date_discovered,
        salary=record.salary or "",
        source=record.source,
        job_url=record.job_url,
        application_url=record.application_url or record.job_url,
    )
