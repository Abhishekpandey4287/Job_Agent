"""APScheduler-based daily job search scheduler."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from job_agent.config import get_settings
from job_agent.logging_config import get_logger
from job_agent.notifications.service import NotificationService
from job_agent.pipeline.daily_pipeline import DailyPipeline

logger = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _run_daily_job():
    logger.info("scheduled_run_started")
    pipeline = DailyPipeline()
    summary = await pipeline.run()

    notifier = NotificationService()
    from job_agent.database.repository import JobRepository
    from job_agent.schemas import JobMatch, MatchCategory, MatchScoreBreakdown

    repo = JobRepository()
    top_records = repo.get_top_jobs_today(10)
    top_matches = []
    for r in top_records:
        from job_agent.schemas import NormalizedJob, DateVerification, EmploymentType, SeniorityLevel
        job = NormalizedJob(
            job_id=r.job_id, source=r.source, title=r.title, company=r.company,
            location=r.location, job_url=r.job_url, application_url=r.application_url,
        )
        top_matches.append(JobMatch(
            job=job,
            match_score=r.match_score or 0,
            match_category=MatchCategory(r.match_category) if r.match_category else MatchCategory.WEAK,
            match_explanation=r.match_reason or "",
        ))
    repo.close()

    await notifier.notify_daily_results(summary, top_matches)
    logger.info("scheduled_run_complete")


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    settings = get_settings()

    _scheduler = AsyncIOScheduler(timezone=settings.timezone)

    time_parts = settings.daily_search_time.split(":")
    hour = int(time_parts[0])
    minute = int(time_parts[1]) if len(time_parts) > 1 else 0

    _scheduler.add_job(
        _run_daily_job,
        CronTrigger(hour=hour, minute=minute, timezone=settings.timezone),
        id="daily_job_search",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("scheduler_started", time=settings.daily_search_time, tz=settings.timezone)
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
