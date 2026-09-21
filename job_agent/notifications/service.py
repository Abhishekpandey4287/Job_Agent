"""Notification service with pluggable channels."""

from abc import ABC, abstractmethod
from typing import List

from job_agent.config import get_settings
from job_agent.logging_config import get_logger
from job_agent.schemas import JobMatch, PipelineSummary

logger = get_logger(__name__)


class NotificationChannel(ABC):
    @abstractmethod
    async def send(self, subject: str, body: str) -> bool:
        ...


class ConsoleChannel(NotificationChannel):
    async def send(self, subject: str, body: str) -> bool:
        print(f"\n{'='*60}\n{subject}\n{'='*60}\n{body}\n")
        return True


class EmailChannel(NotificationChannel):
    async def send(self, subject: str, body: str) -> bool:
        import smtplib
        from email.mime.text import MIMEText

        settings = get_settings()
        if not settings.smtp_host or not settings.notification_email_to:
            return False

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.smtp_user
        msg["To"] = settings.notification_email_to

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.starttls()
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
            return True
        except Exception as e:
            logger.error("email_send_failed", error=str(e))
            return False


class TelegramChannel(NotificationChannel):
    async def send(self, subject: str, body: str) -> bool:
        import httpx

        settings = get_settings()
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return False

        text = f"*{subject}*\n\n{body}"
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                })
                return resp.status_code == 200
        except Exception as e:
            logger.error("telegram_send_failed", error=str(e))
            return False


class SlackChannel(NotificationChannel):
    async def send(self, subject: str, body: str) -> bool:
        import httpx

        settings = get_settings()
        if not settings.slack_webhook_url:
            return False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(settings.slack_webhook_url, json={
                    "text": f"*{subject}*\n{body}",
                })
                return resp.status_code == 200
        except Exception as e:
            logger.error("slack_send_failed", error=str(e))
            return False


class NotificationService:
    def __init__(self):
        settings = get_settings()
        channel_map = {
            "console": ConsoleChannel(),
            "email": EmailChannel(),
            "telegram": TelegramChannel(),
            "slack": SlackChannel(),
        }
        self.channel = channel_map.get(settings.notification_channel, ConsoleChannel())
        self.enabled = settings.notification_enabled

    async def notify_daily_results(
        self, summary: PipelineSummary, top_matches: List[JobMatch]
    ) -> bool:
        if not self.enabled:
            return False

        subject = f"Job Search Report — {summary.search_started.strftime('%Y-%m-%d')}"
        lines = [
            "Good morning!",
            f"{summary.jobs_within_recency} new Android/Mobile jobs found.",
            f"Strong matches: {summary.strong_matches}",
            f"Customized resumes generated: {summary.resumes_generated}",
            "",
            "Top matches:",
        ]

        for i, match in enumerate(top_matches[:10], 1):
            lines.append(
                f"{i}. {match.job.company} — {match.job.title} — {match.match_score:.0f}%"
            )

        if summary.output_path:
            lines.extend(["", f"Full report: {summary.output_path}"])

        body = "\n".join(lines)
        return await self.channel.send(subject, body)
