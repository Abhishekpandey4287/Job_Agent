"""Application state management."""

from datetime import datetime
from typing import List, Optional

from job_agent.config import get_settings
from job_agent.database.repository import JobRepository
from job_agent.logging_config import get_logger
from job_agent.schemas import ApplicationQuestion, ApplicationStatus

logger = get_logger(__name__)


class ApplicationManager:
    def __init__(self):
        self.settings = get_settings()
        self.repo = JobRepository()

    def get_ready_to_apply(self) -> List:
        return self.repo.list_jobs(status=ApplicationStatus.READY_TO_APPLY.value)

    def get_for_review(self) -> List:
        return self.repo.list_jobs(
            status=ApplicationStatus.RESUME_GENERATED.value, min_score=75
        )

    def approve_application(self, job_id: str) -> bool:
        record = self.repo.update_status(job_id, ApplicationStatus.READY_TO_APPLY)
        return record is not None

    def start_application(self, job_id: str) -> bool:
        if self.settings.require_approval_before_apply:
            record = self.repo.get_by_job_id(job_id)
            if not record or record.application_status not in (
                ApplicationStatus.READY_TO_APPLY.value,
                ApplicationStatus.RESUME_GENERATED.value,
            ):
                logger.warning("application_not_approved", job_id=job_id)
                return False

        record = self.repo.update_status(job_id, ApplicationStatus.APPLICATION_STARTED)
        return record is not None

    def mark_applied(self, job_id: str, notes: str = "") -> bool:
        record = self.repo.update_status(job_id, ApplicationStatus.APPLIED, notes)
        return record is not None

    def mark_rejected(self, job_id: str, notes: str = "") -> bool:
        record = self.repo.update_status(job_id, ApplicationStatus.REJECTED, notes)
        return record is not None

    def mark_interview(self, job_id: str, notes: str = "") -> bool:
        record = self.repo.update_status(job_id, ApplicationStatus.INTERVIEW, notes)
        return record is not None

    def mark_offer(self, job_id: str, notes: str = "") -> bool:
        record = self.repo.update_status(job_id, ApplicationStatus.OFFER, notes)
        return record is not None

    def update_notes(self, job_id: str, notes: str) -> bool:
        record = self.repo.get_by_job_id(job_id)
        if not record:
            return False
        record.notes = notes
        record.updated_at = datetime.utcnow()
        self.repo.session.commit()
        return True

    def close(self):
        self.repo.close()
