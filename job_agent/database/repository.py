"""Database access layer for job records."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import desc, func

from job_agent.database.models import AppConfig, JobRecord, PipelineRun, get_db_session
from job_agent.schemas import ApplicationStatus, NormalizedJob


def generate_job_id(company: str, title: str, job_url: str, source_job_id: str = "") -> str:
    if source_job_id:
        key = f"{company}|{title}|{source_job_id}"
    else:
        key = f"{company}|{title}|{job_url}"
    return hashlib.sha256(key.lower().strip().encode()).hexdigest()[:32]


class JobRepository:
    def __init__(self):
        self.session = get_db_session()

    def close(self):
        self.session.close()

    def exists(self, job_id: str) -> bool:
        return self.session.query(JobRecord.id).filter(JobRecord.job_id == job_id).first() is not None

    def get_by_job_id(self, job_id: str) -> Optional[JobRecord]:
        return self.session.query(JobRecord).filter(JobRecord.job_id == job_id).first()

    def get_by_id(self, record_id: int) -> Optional[JobRecord]:
        return self.session.query(JobRecord).filter(JobRecord.id == record_id).first()

    def upsert_job(self, job: NormalizedJob, **extra_fields) -> JobRecord:
        existing = self.get_by_job_id(job.job_id)
        if existing:
            for field, value in extra_fields.items():
                if hasattr(existing, field) and value is not None:
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            if job.application_url and not existing.application_url:
                existing.application_url = job.application_url
            for field in (
                "career_portal",
                "career_portal_job_id",
                "career_portal_url",
                "career_job_id_type",
            ):
                value = getattr(job, field, "") or ""
                if value and not getattr(existing, field, ""):
                    setattr(existing, field, value)
            self.session.commit()
            return existing

        record = JobRecord(
            job_id=job.job_id,
            company=job.company,
            title=job.title,
            location=job.location,
            location_tier=job.location_tier,
            source=job.source,
            source_job_id=job.source_job_id,
            job_url=job.job_url,
            application_url=job.application_url or job.job_url,
            date_posted=job.date_posted,
            date_posted_verified=job.date_posted_verified.value if job.date_posted_verified else "unknown",
            date_discovered=job.date_discovered,
            employment_type=job.employment_type.value,
            seniority=job.seniority.value,
            salary=job.salary,
            job_description=job.description,
            is_remote=job.is_remote,
            remote_india_eligible=job.remote_india_eligible,
            is_android_relevant=job.is_android_relevant,
            career_portal=getattr(job, "career_portal", "") or "",
            career_portal_job_id=getattr(job, "career_portal_job_id", "") or "",
            career_portal_url=getattr(job, "career_portal_url", "") or "",
            career_job_id_type=getattr(job, "career_job_id_type", "") or "",
            **extra_fields,
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def update_status(self, job_id: str, status: ApplicationStatus, notes: str = "") -> Optional[JobRecord]:
        record = self.get_by_job_id(job_id)
        if not record:
            return None
        record.application_status = status.value
        if notes:
            record.notes = notes
        if status == ApplicationStatus.APPLIED:
            record.applied_at = datetime.utcnow()
        record.updated_at = datetime.utcnow()
        self.session.commit()
        return record

    def list_jobs(
        self,
        status: Optional[str] = None,
        min_score: Optional[float] = None,
        company: Optional[str] = None,
        location: Optional[str] = None,
        source: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[JobRecord]:
        q = self.session.query(JobRecord)
        if status:
            q = q.filter(JobRecord.application_status == status)
        if min_score is not None:
            q = q.filter(JobRecord.match_score >= min_score)
        if company:
            q = q.filter(JobRecord.company.ilike(f"%{company}%"))
        if location:
            q = q.filter(JobRecord.location.ilike(f"%{location}%"))
        if source:
            q = q.filter(JobRecord.source == source)
        if since:
            q = q.filter(JobRecord.date_discovered >= since)
        return q.order_by(desc(JobRecord.match_score), desc(JobRecord.date_discovered)).offset(offset).limit(limit).all()

    def get_top_jobs_today(self, limit: int = 30) -> List[JobRecord]:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self.session.query(JobRecord)
            .filter(JobRecord.date_discovered >= today_start)
            .filter(JobRecord.match_score.isnot(None))
            .order_by(desc(JobRecord.match_score))
            .limit(limit)
            .all()
        )

    def get_dashboard_stats(self) -> dict:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        base = self.session.query(JobRecord)
        return {
            "jobs_found_today": base.filter(JobRecord.date_discovered >= today_start).count(),
            "strong_matches": base.filter(JobRecord.match_score >= 80).count(),
            "applications_ready": base.filter(JobRecord.application_status == "READY_TO_APPLY").count(),
            "applications_submitted": base.filter(JobRecord.application_status == "APPLIED").count(),
            "interviews": base.filter(JobRecord.application_status == "INTERVIEW").count(),
            "rejections": base.filter(JobRecord.application_status == "REJECTED").count(),
            "offers": base.filter(JobRecord.application_status == "OFFER").count(),
        }

    def save_pipeline_run(self, run: PipelineRun) -> PipelineRun:
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run


class ConfigRepository:
    def __init__(self):
        self.session = get_db_session()

    def close(self):
        self.session.close()

    def get(self, key: str, default: str = "") -> str:
        row = self.session.query(AppConfig).filter(AppConfig.key == key).first()
        return row.value if row else default

    def set(self, key: str, value: str) -> None:
        row = self.session.query(AppConfig).filter(AppConfig.key == key).first()
        if row:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            self.session.add(AppConfig(key=key, value=value))
        self.session.commit()

    def get_json(self, key: str, default=None):
        raw = self.get(key)
        if not raw:
            return default
        return json.loads(raw)

    def set_json(self, key: str, value) -> None:
        self.set(key, json.dumps(value))
